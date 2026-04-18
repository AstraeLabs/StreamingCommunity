# 17.10.24

import os
import time
import logging
from typing import Dict, List, Optional

from rich.console import Console

from VibraVid.utils import config_manager, os_manager
from VibraVid.utils.http_client import get_headers
from VibraVid.setup import get_wvd_path, get_prd_path
from VibraVid.core.ui.tracker import download_tracker, context_tracker
from VibraVid.core.utils.media_players import MediaPlayers

from VibraVid.core.source.manual import MediaDownloader
from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.muxing.helper.video_hybrid import split_other_tracks

from .base import BaseDownloader


console = Console()
logger = logging.getLogger(__name__)

EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
SKIP_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "skip_download")
_WV = "widevine"
_PR = "playready"


class HLS_Downloader(BaseDownloader):
    """
    High-level HLS downloader.

    Flow
    ----
    1. ``parse_stream()``   — fetch manifest → auto-select → show table
    2. DRM extraction       — read DRMInfo from selected Stream objects (fallback: M3U8Parser scan of the saved raw .m3u8)
    3. Key fetch            — DRMManager → Widevine or PlayReady
    4. ``start_download()`` — run n3u8dl / manual, decrypt, build status dict
    5. ``_merge_files()``   — FFmpeg mux
    6. ``_finalize()``      — move, summary, NFO, tracker, cleanup
    """
    def __init__(self, m3u8_url: str, headers: Optional[Dict[str, str]] = None,
        license_url: Optional[str] = None, license_headers: Optional[Dict[str, str]] = None, license_certificate: Optional[str] = None,
        output_path: Optional[str] = None, drm_preference: str = "widevine", key: Optional[str] = None,
        cookies: Optional[Dict[str, str]] = None, max_segments: Optional[int] = None,
        other_tracks: Optional[list] = None,
    ):
        """
        Parameters:
            - m3u8_url: M3U8 manifest URL to download.
            - headers: HTTP headers for requests (auth, user-agent, etc).
            - license_url: DRM license server URL for Widevine/PlayReady.
            - license_headers: HTTP headers for DRM license requests.
            - license_certificate: Widevine certificate (base64) for license challenge.
            - output_path: Output file path. Default: "download.{EXTENSION_OUTPUT}".
            - drm_preference: DRM system preference: "widevine", "playready", or "auto".
            - key: Manual decryption key (hex format) if known.
            - cookies: HTTP cookies for authenticated requests.
            - max_segments: Maximum number of segments to download (for testing). Default: None (all).
        """
        self.m3u8_url = str(m3u8_url).strip()
        self.headers = headers or get_headers()
        self.license_url = str(license_url).strip() if license_url else None
        self.license_headers = license_headers or self.headers
        self.license_certificate = license_certificate
        self.drm_preference = drm_preference.lower()
        self.key = key
        self.cookies = cookies or {}
        self.max_segments = max_segments
        self.other_tracks = other_tracks or []
        logger.info(f"Initialized HLS_Downloader with URL: {self.m3u8_url}, License URL: {self.license_url}, DRM Pref: {self.drm_preference}, Max Segments: {self.max_segments}")

        if not output_path:
            output_path = f"download.{EXTENSION_OUTPUT}"
        self.output_path = os_manager.get_sanitize_path(output_path)
        if not self.output_path.endswith(f".{EXTENSION_OUTPUT}"):
            self.output_path += f".{EXTENSION_OUTPUT}"

        self.filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        self.output_dir = os.path.join(os.path.dirname(self.output_path), self.filename_base + "_hls_temp")
        self.file_already_exists = os.path.exists(self.output_path)

        self.download_id = context_tracker.download_id
        self.site_name = context_tracker.site_name

        self.error = None
        self.last_merge_result = None
        self.media_players = None
        self.copied_subtitles = []
        self.copied_audios = []
        self.audio_only = False

    def _collect_drm_from_streams(self, streams: list) -> Dict[str, List[Dict]]:
        """
        Read PSSH data directly from Stream.drm (DRMInfo) on selected streams.

        Returns::

            {
              'WV': [{'pssh': '...', 'kid': '...', 'type': 'Widevine'}, ...],
              'PR': [{'pssh': '...', 'kid': '...', 'type': 'PlayReady'}, ...],
              'FP': [{'uri': '...', 'kid': '...', 'type': 'FairPlay'}, ...],
            }
        """
        result: Dict[str, List[Dict]] = {"WV": [], "PR": [], "FP": []}
        seen: Dict[str, set] = {"WV": set(), "PR": set(), "FP": set()}

        for s in streams:
            if not getattr(s, "selected", False):
                continue
            
            drm = getattr(s, "drm", None)
            
            # Sub-parse variant if no DRM found yet and variant URL exists
            if not (drm and drm.is_encrypted()) and s.playlist_url:
                try:
                    from VibraVid.core.manifest.m3u8 import HLSParser
                    parser = HLSParser(self.m3u8_url, self.headers)
                    variant_drm = parser.parse_variant(s.playlist_url)
                    if variant_drm and variant_drm.is_encrypted():
                        s.drm = variant_drm
                        drm = variant_drm
                        logger.info(f"Found DRM info in variant playlist: {s.playlist_url}")
                except Exception as exc:
                    logger.error(f"Failed to sub-parse variant {s.playlist_url} for DRM: {exc}")

            if not (drm and drm.is_encrypted()):
                continue

            for dt in drm.get_all_drm_types():  # 'WV', 'PR', 'FP', 'UNK'
                if dt not in result:
                    continue
                pssh = drm.get_pssh_for(dt)
                if not pssh or pssh in seen[dt]:
                    continue
                seen[dt].add(pssh)
                kid = (
                    getattr(drm, "kid", None)
                    or getattr(drm, "default_kid", None)
                    or "N/A"
                )
                
                entry = {
                    "pssh" if dt != "FP" else "uri": pssh,
                    "kid": kid,
                    "type": "Widevine" if dt == "WV" else ("PlayReady" if dt == "PR" else "FairPlay"),
                }
                result[dt].append(entry)

        return result

    def _collect_drm_from_m3u8(self, raw_m3u8_path: Optional[str]) -> Dict[str, List[Dict]]:
        """
        Fallback: run M3U8Parser on the saved raw manifest to find PSSH data.
        Imported lazily — if the parser is unavailable the method returns {} gracefully.
        """
        result: Dict[str, List[Dict]] = {"WV": [], "PR": [], "FP": []}
        try:
            from VibraVid.core.manifest.m3u8 import HLSParser as M3U8Parser

            content = None
            if raw_m3u8_path and os.path.exists(raw_m3u8_path):
                with open(raw_m3u8_path, "r", encoding="utf-8") as f:
                    content = f.read()

            parser = M3U8Parser(self.m3u8_url, self.headers, content=content)
            drm_info = (parser.get_drm_info())  # → {'widevine': [...], 'playready': [...], 'fairplay': [...]}

            for entry in drm_info.get("widevine", []):
                result["WV"].append({"pssh": entry["pssh"], "kid": entry.get("kid", "N/A"), "type": "Widevine"})
            for entry in drm_info.get("playready", []):
                result["PR"].append({"pssh": entry["pssh"], "kid": entry.get("kid", "N/A"), "type": "PlayReady"})
            for entry in drm_info.get("fairplay", []):
                result["FP"].append({"uri": entry["uri"], "kid": entry.get("kid", "N/A"), "type": "FairPlay"})
        except Exception as exc:
            logger.error(f"_collect_drm_from_m3u8 error: {exc}")

        return result

    def _fetch_keys(self, drm_psshs: Dict[str, List[Dict]]) -> List[str]:
        """
        Dispatch key fetch to DRMManager.

        All DRM type comparisons use plain string literals ('widevine',
        'playready', 'auto') — never DRMSystem / DRMInfo class attributes.
        """
        drm_manager = DRMManager(
            get_wvd_path(),
            get_prd_path(),
            config_manager.config.get_dict("DRM", "widevine", default={}),
            config_manager.config.get_dict("DRM", "playready", default={}),
            config_manager.config.get_bool("DRM", "prefer_remote_cdm"),
        )
        pref = self.drm_preference  # 'widevine' | 'playready' | 'auto'
        keys = None

        if pref in (_WV, "auto") and drm_psshs.get("WV"):
            try:
                keys = drm_manager.get_wv_keys(drm_psshs["WV"], self.license_url, self.license_certificate, self.license_headers, self.key)
            except Exception as exc:
                logger.error(f"Widevine key fetch failed: {exc}")

        if not keys and pref in (_PR, "auto") and drm_psshs.get("PR"):
            try:
                keys = drm_manager.get_pr_keys(drm_psshs["PR"], self.license_url, self.license_headers, self.key)
            except Exception as exc:
                logger.error(f"PlayReady key fetch failed: {exc}")

        # Manual key passed directly
        if not keys and self.key:
            keys = [self.key] if isinstance(self.key, str) else list(self.key)

        return keys or []

    def start(self) -> tuple[Optional[str], bool]:
        """
        Execute the full HLS download pipeline.
        Returns ``(output_path, cancelled)`` — cancelled=True means abort.
        """
        if self.file_already_exists:
            console.print("[yellow]File already exists.")
            return self.output_path, False

        os_manager.create_path(self.output_dir)
        self.media_downloader = MediaDownloader(
            url=self.m3u8_url,
            output_dir=self.output_dir,
            filename=self.filename_base,
            headers=self.headers,
            cookies=self.cookies,
            download_id=self.download_id,
            site_name=self.site_name,
            max_segments=self.max_segments,
        )
        self.media_downloader.other_tracks = self.other_tracks
        _, _, other_subtitles = split_other_tracks(self.other_tracks)
        if other_subtitles:
            self.media_downloader.external_subtitles = other_subtitles

        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing HLS ...")

        streams = self.media_downloader.parse_stream(show_table=context_tracker.should_print)

        # ── DRM key fetch ─────────────────────────────────────────────────────
        if self.license_url or self.key:
            raw_m3u8 = (str(self.media_downloader.raw_m3u8) if self.media_downloader.raw_m3u8 else None)

            # Primary: PSSH from Stream.drm (populated by HLSParser)
            drm_psshs = self._collect_drm_from_streams(streams)

            # Fallback: scan raw manifest via M3U8Parser
            if not drm_psshs["WV"] and not drm_psshs["PR"]:
                logger.info("No PSSH in Stream objects — falling back to M3U8Parser")
                drm_psshs = self._collect_drm_from_m3u8(raw_m3u8)

            keys = self._fetch_keys(drm_psshs)

            if keys:
                self.media_downloader.set_key(keys)
            elif drm_psshs.get("WV") or drm_psshs.get("PR"):
                console.print("[red]Warning: DRM detected but no decryption keys found")
        else:
            keys = []

        # ── Download ──────────────────────────────────────────────────────────
        self._log_tracks_json(streams, keys, self.m3u8_url)
        if SKIP_DOWNLOAD:
            console.print("[yellow]Skipping download as per configuration.")
            return self.output_path, False

        try:
            self.media_players = MediaPlayers(self.output_dir)
            self.media_players.create()
        except Exception:
            pass

        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")
        print()

        status = self.media_downloader.start_download()

        if status.get("error") == "cancelled":
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="cancelled")
            return None, True

        if self._no_media_downloaded(status):
            logger.error("No media downloaded")
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="No media downloaded")
            return None, True

        # ── Merge ─────────────────────────────────────────────────────────────
        if self.download_id:
            download_tracker.update_status(self.download_id, "Muxing ...")

        final_file = self._merge_files(status)
        if not final_file:
            if self.download_id and download_tracker.is_stopped(self.download_id):
                download_tracker.complete_download(self.download_id, success=False, error="cancelled")
                return None, True
            logger.error("Merge failed")
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="Merge failed")
            return None, True

        self._finalize(final_file=final_file)
        time.sleep(config_manager.config.get_int("DOWNLOAD", "delay_after_download"))
        return self.output_path, False