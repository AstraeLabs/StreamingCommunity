# 03.05.26

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

from VibraVid.core.source.downloader import MediaDownloader
from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.manifest.ism import ISMParser
from VibraVid.core.muxing.helper.video_hybrid import split_other_tracks

from .base import BaseDownloader


console = Console()
logger = logging.getLogger(__name__)

EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
SKIP_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "skip_download")
DELAY_SS = config_manager.config.get_int("DOWNLOAD", "delay_after_download")
_WV = "widevine"
_PR = "playready"


class ISM_Downloader(BaseDownloader):
    def __init__(self, ism_url: str,
        headers: Optional[Dict[str, str]] = None, license_url: Optional[str] = None, license_headers: Optional[Dict[str, str]] = None, license_certificate: Optional[str] = None,
        output_path: Optional[str] = None, drm_preference: str = "playready", key: Optional[str] = None, cookies: Optional[Dict[str, str]] = None,
        max_segments: Optional[int] = None,
        other_tracks: Optional[list] = None,
    ):
        self.ism_url = self._resolve_url(str(ism_url).strip())
        self.headers = headers or get_headers()
        self.license_url = str(license_url).strip() if license_url else None
        self.license_headers = license_headers or self.headers
        self.license_certificate = license_certificate
        self.drm_preference = drm_preference.lower()
        self.key = key
        self.cookies = cookies or {}
        self.max_segments = max_segments
        self.other_tracks = other_tracks or []
        logger.info(f"Initialized ISM_Downloader with URL: {self.ism_url}, License URL: {self.license_url}, DRM Pref: {self.drm_preference}, Max Segments: {self.max_segments}")

        if not output_path:
            output_path = f"download.{EXTENSION_OUTPUT}"
        self.output_path = os_manager.get_sanitize_path(output_path)
        if not self.output_path.endswith(f".{EXTENSION_OUTPUT}"):
            self.output_path += f".{EXTENSION_OUTPUT}"

        self.filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        self.output_dir = os.path.join(
            os.path.dirname(self.output_path),
            self.filename_base + "_ism_temp",
        )
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
        Read PSSH / PRO data directly from ``Stream.drm`` on selected streams.
        """
        result: Dict[str, List[Dict]] = {"WV": [], "PR": []}
        seen:   Dict[str, set]        = {"WV": set(), "PR": set()}

        for s in streams:
            if not getattr(s, "selected", False):
                continue

            drm = getattr(s, "drm", None)
            if not (drm and drm.is_encrypted()):
                continue

            for dt in drm.get_all_drm_types():   # 'WV', 'PR', 'FP', 'UNK'
                if dt not in result:
                    continue

                pssh = drm.get_pssh_for(dt)
                if not pssh:
                    continue

                # Collect all KIDs for this stream
                kids: List[str] = []
                if hasattr(drm, "get_all_kids"):
                    kids = [k for k in drm.get_all_kids() if k and k != "N/A"]

                if not kids:
                    for attr in ("kid", "default_kid"):
                        val = getattr(drm, attr, None)
                        if val and val != "N/A":
                            kids = [val]
                            break
                
                if not kids:
                    kids = ["N/A"]

                for kid in kids:
                    dedup = (pssh, kid)
                    if dedup in seen[dt]:
                        continue

                    seen[dt].add(dedup)
                    result[dt].append({
                        "pssh": pssh,
                        "kid":  kid,
                        "type": "Widevine" if dt == "WV" else "PlayReady",
                    })

        return result

    def _collect_drm_from_ism(self, raw_ism_path: Optional[str]) -> Dict[str, List[Dict]]:
        """
        Fallback: run :class:`ISMParser` on the saved raw manifest to find
        PSSH / PRO data.  If *raw_ism_path* is ``None`` or missing the parser
        re-fetches from the network.  Returns ``{}`` gracefully on any error.
        """
        result: Dict[str, List[Dict]] = {"WV": [], "PR": []}
        try:
            content = None
            if raw_ism_path and os.path.exists(raw_ism_path):
                with open(raw_ism_path, "r", encoding="utf-8") as f:
                    content = f.read()

            parser = ISMParser(self.ism_url, self.headers, content=content)
            if not parser.fetch_manifest():
                return result

            drm_info = parser.get_drm_info()
            for entry in drm_info.get("widevine", []):
                result["WV"].append({
                    "pssh": entry["pssh"],
                    "kid":  entry.get("kid", "N/A"),
                    "type": "Widevine",
                })

            for entry in drm_info.get("playready", []):
                result["PR"].append({
                    "pssh": entry["pssh"],
                    "kid":  entry.get("kid", "N/A"),
                    "type": "PlayReady",
                })

        except Exception as exc:
            logger.error(f"_collect_drm_from_ism error: {exc}")

        return result

    def _fetch_keys(self, drm_psshs: Dict[str, List[Dict]]) -> List[str]:
        """
        Dispatch key-fetch to :class:`DRMManager`.

        Priority order (respects ``drm_preference``):

        * ``"playready"`` → PlayReady only
        * ``"widevine"``  → Widevine only
        * ``"auto"``      → PlayReady first, then Widevine if PR fails

        Falls back to ``self.key`` (manual key) when DRM negotiation fails.
        """
        drm_manager = DRMManager(
            get_wvd_path(),
            get_prd_path(),
            config_manager.config.get_dict("DRM", "widevine",  default={}),
            config_manager.config.get_dict("DRM", "playready", default={}),
            config_manager.config.get_bool("DRM", "prefer_remote_cdm"),
        )
        pref = self.drm_preference   # 'playready' | 'widevine' | 'auto'
        keys = None

        # PlayReady — native ISM DRM; try first for 'playready' and 'auto'
        if pref in (_PR, "auto") and drm_psshs.get("PR"):
            try:
                keys = drm_manager.get_pr_keys(
                    drm_psshs["PR"],
                    self.license_url,
                    self.license_headers,
                    self.key,
                )
            except Exception as exc:
                logger.error(f"PlayReady key fetch failed: {exc}")

        # Widevine — uncommon for ISM but supported
        if not keys and pref in (_WV, "auto") and drm_psshs.get("WV"):
            try:
                keys = drm_manager.get_wv_keys(
                    drm_psshs["WV"],
                    self.license_url,
                    self.license_certificate,
                    self.license_headers,
                    self.key,
                )
            except Exception as exc:
                logger.error(f"Widevine key fetch failed: {exc}")

        # Manual key supplied directly
        if not keys and self.key:
            keys = [self.key] if isinstance(self.key, str) else list(self.key)

        return keys or []

    def start(self) -> tuple[Optional[str], bool]:
        """Execute the full ISM download pipeline."""
        if self.file_already_exists:
            console.print("[yellow]File already exists.")
            return self.output_path, False

        os_manager.create_path(self.output_dir)

        self.media_downloader = MediaDownloader(
            url=self.ism_url,
            output_dir=self.output_dir,
            filename=self.filename_base,
            headers=self.headers,
            cookies=self.cookies,
            download_id=self.download_id,
            site_name=self.site_name,
            max_segments=self.max_segments,
        )
        self.media_downloader.other_tracks = self.other_tracks

        # Inject external subtitles that were passed via other_tracks
        _, _, other_subtitles = split_other_tracks(self.other_tracks)
        if other_subtitles:
            self.media_downloader.external_subtitles = other_subtitles

        # ── Parse ─────────────────────────────────────────────────────────────
        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing ISM ...")

        streams = self.media_downloader.parse_stream(show_table=context_tracker.should_print)

        # ── DRM key fetch ─────────────────────────────────────────────────────
        if self.license_url or self.key:
            raw_ism = (
                str(self.media_downloader.raw_ism)
                if hasattr(self.media_downloader, "raw_ism") and self.media_downloader.raw_ism
                else None
            )

            # Primary: PSSH / PRO from Stream.drm (populated by ISMParser)
            drm_psshs = self._collect_drm_from_streams(streams)

            # Fallback: re-scan raw manifest via ISMParser
            if not drm_psshs["WV"] and not drm_psshs["PR"]:
                logger.info("No PSSH in Stream objects — falling back to ISMParser")
                drm_psshs = self._collect_drm_from_ism(raw_ism)

            keys = self._fetch_keys(drm_psshs)

            if keys:
                self.media_downloader.set_key(keys)
            elif drm_psshs.get("WV") or drm_psshs.get("PR"):
                console.print("[red]Warning: DRM detected but no decryption keys found")
        else:
            keys = []

        # ── Download ──────────────────────────────────────────────────────────
        self._log_tracks_json(streams, keys, self.ism_url)

        if SKIP_DOWNLOAD:
            if DELAY_SS > 0:
                console.print(
                    f"\n[yellow]Skipping download as per configuration "
                    f"and sleeping {DELAY_SS} seconds..."
                )
                time.sleep(DELAY_SS)
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
                download_tracker.complete_download(
                    self.download_id, success=False, error="cancelled"
                )
            return None, True

        if self._no_media_downloaded(status):
            logger.error("No media downloaded")
            if self.download_id:
                download_tracker.complete_download(
                    self.download_id, success=False, error="No media downloaded"
                )
            return None, True

        # ── Merge ─────────────────────────────────────────────────────────────
        if self.download_id:
            download_tracker.update_status(self.download_id, "Muxing ...")

        final_file = self._merge_files(status)
        if not final_file:
            if self.download_id and download_tracker.is_stopped(self.download_id):
                download_tracker.complete_download(
                    self.download_id, success=False, error="cancelled"
                )
                return None, True
            logger.error("Merge failed")
            if self.download_id:
                download_tracker.complete_download(
                    self.download_id, success=False, error="Merge failed"
                )
            return None, True

        self._finalize(final_file=final_file)

        if DELAY_SS > 0:
            console.print(f"\n[green]Sleeping {DELAY_SS} seconds before finishing...")
            time.sleep(DELAY_SS)

        return self.output_path, False
