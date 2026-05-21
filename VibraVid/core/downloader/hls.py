# 17.10.24

import os
import time
import logging
from typing import Dict, List, Optional

from rich.console import Console

from VibraVid.utils import config_manager, os_manager
from VibraVid.utils.http_client import get_headers
from VibraVid.core.muxing.helper.video_hybrid import split_other_tracks
from VibraVid.core.ui.tracker import download_tracker, context_tracker
from VibraVid.core.utils.media_players import MediaPlayers

from VibraVid.core.source.downloader import MediaDownloader
from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.drm.system import DRMType, _DRMSystems
from VibraVid.setup import get_wvd_path, get_prd_path

from .base import BaseDownloader


console = Console()
logger = logging.getLogger(__name__)

EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
SKIP_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "skip_download")
DELAY_SS = config_manager.config.get_int('DOWNLOAD', 'delay_after_download')


class HLS_Downloader(BaseDownloader):
    def __init__(self, m3u8_url: str, headers: Optional[Dict[str, str]] = None,
        license_url: Optional[str] = None, license_headers: Optional[Dict[str, str]] = None, license_certificate: Optional[str] = None,
        output_path: Optional[str] = None, drm_preference = _DRMSystems.WIDEVINE, key: Optional[str] = None,
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
            - key: Manual decryption key (hex format) if known.
            - cookies: HTTP cookies for authenticated requests.
            - max_segments: Maximum number of segments to download (for testing). Default: None (all).
        """
        self.m3u8_url = self._resolve_url(str(m3u8_url).strip())
        self.headers = headers or get_headers()
        self.license_url = str(license_url).strip() if license_url else None
        self.license_headers = license_headers or self.headers
        self.license_certificate = license_certificate
        self.drm_preference = drm_preference
        self.key = key
        self.cookies = cookies or {}
        self.max_segments = max_segments
        self.other_tracks = other_tracks or []
        logger.info(f"Initialized HLS_Downloader with URL: {self.m3u8_url}, License URL: {self.license_url}, DRM Pref: {self.drm_preference}, Max Segments: {self.max_segments}")

        self.drm_manager = DRMManager(
            get_wvd_path(),
            get_prd_path(),
            config_manager.config.get_dict("DRM", "widevine", default={}),
            config_manager.config.get_dict("DRM", "playready", default={}),
            config_manager.config.get_bool("DRM", "prefer_remote_cdm"),
        )

        super().__init__(output_path, "_hls_temp")

    def _collect_drm_from_streams(self, streams: list) -> Dict[str, List[Dict]]:
        """
        Read PSSH data directly from Stream.drm (DRMInfo) on selected streams.

        Returns::

            {
              DRMType.WIDEVINE: [{'pssh': '...', 'kid': '...', 'type': 'Widevine'}, ...],
              DRMType.PLAYREADY: [{'pssh': '...', 'kid': '...', 'type': 'PlayReady'}, ...],
              DRMType.FAIRPLAY: [{'uri': '...', 'kid': '...', 'type': 'FairPlay'}, ...],
            }
        """
        result: Dict[str, List[Dict]] = {DRMType.WIDEVINE: [], DRMType.PLAYREADY: [], DRMType.FAIRPLAY: []}
        seen: Dict[str, set] = {DRMType.WIDEVINE: set(), DRMType.PLAYREADY: set(), DRMType.FAIRPLAY: set()}

        for s in streams:
            if not getattr(s, "selected", False):
                continue
            
            drm = getattr(s, "drm", None)
            
            # Sub-parse variant if no DRM found yet and variant URL exists
            if not (drm and drm.is_encrypted()) and s.playlist_url:
                try:
                    from VibraVid.core.manifest.m3u8 import HLSParser
                    parser = HLSParser(self.m3u8_url, self.headers)
                    variant_drm, _ = parser.parse_variant(s.playlist_url)
                    if variant_drm and variant_drm.is_encrypted():
                        s.drm = variant_drm
                        drm = variant_drm
                        logger.info(f"Found DRM info in variant playlist: {s.playlist_url}")
                except Exception as exc:
                    logger.error(f"Failed to sub-parse variant {s.playlist_url} for DRM: {exc}")

            if not (drm and drm.is_encrypted()):
                continue

            for dt in drm.get_all_drm_types():  # DRMType.WIDEVINE, DRMType.PLAYREADY, DRMType.FAIRPLAY, DRMType.UNKNOWN
                if dt not in result:
                    continue

                pssh = drm.get_pssh_for(dt)

                if not pssh and dt == DRMType.WIDEVINE:
                    kid_val = getattr(drm, "kid", None) or getattr(drm, "default_kid", None)
                    if kid_val and kid_val != "N/A":
                        try:
                            pssh = _DRMSystems.build_widevine_pssh_from_kid(kid_val)
                            logger.info(f"HLS: synthesized Widevine PSSH from KID {kid_val[:8]}…")
                        except Exception as exc:
                            logger.debug(f"HLS: Widevine PSSH synthesis failed: {exc}")

                if not pssh or pssh in seen[dt]:
                    continue

                seen[dt].add(pssh)
                kid = (
                    getattr(drm, "kid", None)
                    or getattr(drm, "default_kid", None)
                    or "N/A"
                )

                entry = {
                    "pssh" if dt != DRMType.FAIRPLAY else "uri": pssh,
                    "kid": kid,
                    "type": "Widevine" if dt == DRMType.WIDEVINE else ("PlayReady" if dt == DRMType.PLAYREADY else "FairPlay"),
                }
                result[dt].append(entry)

        return result

    def _collect_drm_from_m3u8(self, raw_m3u8_path: Optional[str]) -> Dict[str, List[Dict]]:
        """
        Fallback: run M3U8Parser on the saved raw manifest to find PSSH data.
        Imported lazily — if the parser is unavailable the method returns {} gracefully.
        """
        result: Dict[str, List[Dict]] = {DRMType.WIDEVINE: [], DRMType.PLAYREADY: [], DRMType.FAIRPLAY: []}
        try:
            from VibraVid.core.manifest.m3u8 import HLSParser as M3U8Parser

            content = None
            if raw_m3u8_path and os.path.exists(raw_m3u8_path):
                with open(raw_m3u8_path, "r", encoding="utf-8") as f:
                    content = f.read()

            parser = M3U8Parser(self.m3u8_url, self.headers, content=content)
            drm_info = (parser.get_drm_info())  # → {'widevine': [...], 'playready': [...], 'fairplay': [...]}

            for entry in drm_info.get("widevine", []):
                result[DRMType.WIDEVINE].append({"pssh": entry["pssh"], "kid": entry.get("kid", "N/A"), "type": "Widevine"})
            for entry in drm_info.get("playready", []):
                result[DRMType.PLAYREADY].append({"pssh": entry["pssh"], "kid": entry.get("kid", "N/A"), "type": "PlayReady"})
            for entry in drm_info.get("fairplay", []):
                result[DRMType.FAIRPLAY].append({"uri": entry["uri"], "kid": entry.get("kid", "N/A"), "type": "FairPlay"})
        except Exception as exc:
            logger.error(f"_collect_drm_from_m3u8 error: {exc}")

        return result

    def _fetch_keys(self, drm_psshs: Dict[str, List[Dict]]) -> List[str]:
        """Dispatch key fetch to DRMManager using the configured drm_preference"""
        keys = None

        if self.drm_preference == DRMType.WIDEVINE and drm_psshs.get(DRMType.WIDEVINE):
            try:
                keys = self.drm_manager.get_wv_keys(
                    drm_psshs[DRMType.WIDEVINE],
                    self.license_url,
                    license_certificate=self.license_certificate,
                    headers=self.license_headers,
                    key=self.key,
                )
            except Exception as exc:
                logger.error(f"Widevine key fetch failed: {exc}")

        if self.drm_preference == DRMType.PLAYREADY and drm_psshs.get(DRMType.PLAYREADY):
            try:
                keys = self.drm_manager.get_pr_keys(
                    drm_psshs[DRMType.PLAYREADY],
                    self.license_url,
                    headers=self.license_headers,
                    key=self.key,
                )
            except Exception as exc:
                logger.error(f"PlayReady key fetch failed: {exc}")

        if not keys and self.key:
            keys = [self.key] if isinstance(self.key, str) else list(self.key)

        return keys or []

    def start(self) -> tuple[Optional[str], bool, Optional[str]]:
        """
        Execute the full HLS download pipeline.
        Returns ``(output_path, cancelled)`` — cancelled=True means abort.
        """
        if self.file_already_exists:
            console.print("[yellow]File already exists.")
            return self.output_path, False, None

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
            if not drm_psshs[DRMType.WIDEVINE] and not drm_psshs[DRMType.PLAYREADY]:
                logger.info("No PSSH in Stream objects — falling back to M3U8Parser")
                drm_psshs = self._collect_drm_from_m3u8(raw_m3u8)

            keys = self._fetch_keys(drm_psshs)

            if keys:
                self.media_downloader.set_key(keys)
            elif drm_psshs.get(DRMType.WIDEVINE) or drm_psshs.get(DRMType.PLAYREADY):
                console.print("[red]Warning: DRM detected but no decryption keys found")
        else:
            keys = []

        # ── Download ──────────────────────────────────────────────────────────
        self._log_tracks_json(streams, keys, self.m3u8_url)
        if SKIP_DOWNLOAD:
            if DELAY_SS > 0:
                console.print(f"\n[yellow]Skipping download as per configuration and sleeping {DELAY_SS} seconds...")
                time.sleep(DELAY_SS)
            return self.output_path, False, None

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
            return None, True, "cancelled"

        if self._no_media_downloaded(status):
            logger.error("No media downloaded")
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="No media downloaded")
            return None, True, "No media downloaded"

        # ── Merge ─────────────────────────────────────────────────────────────
        if self.download_id:
            download_tracker.update_status(self.download_id, "Muxing ...")

        final_file = self._merge_files(status)
        if not final_file:
            if self.download_id and download_tracker.is_stopped(self.download_id):
                    download_tracker.complete_download(self.download_id, success=False, error="cancelled")
                    return None, True, "cancelled"
            logger.error("Merge failed")
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="Merge failed")
                return None, True, "Merge failed"

        self._finalize(final_file=final_file)
        if DELAY_SS > 0:
            console.print(f"\n[green]Sleeping {DELAY_SS} seconds before finishing...")
            time.sleep(DELAY_SS)
        return self.output_path, False, None