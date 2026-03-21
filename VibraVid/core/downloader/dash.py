# 05.01.26

from __future__ import annotations

import os
import shutil
import logging
from typing import Dict, List, Optional

from rich.console import Console

from VibraVid.utils import config_manager, os_manager
from VibraVid.utils.http_client import get_headers
from VibraVid.setup import get_wvd_path, get_prd_path
from VibraVid.source.style.tracker import download_tracker, context_tracker
from VibraVid.source.utils.media_players import MediaPlayers
from VibraVid.source.utils.language import resolve_locale

from VibraVid.source.n3u8dl_re import MediaDownloader
from VibraVid.core.drm.manager import DRMManager
from VibraVid.core.manifest.mpd import DashParser

from .base import BaseDownloader


console = Console()
logger = logging.getLogger(__name__)

EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
SKIP_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "skip_download")
AUDIO_FILTER = config_manager.config.get("DOWNLOAD", "select_audio")
SUBTITLE_FILTER = config_manager.config.get("DOWNLOAD", "select_subtitle")
_WV = "widevine"
_PR = "playready"


def _stream_drm_label(s) -> str:
    """Build a human-readable track label for DRM reporting."""
    stype = getattr(s, "type", "") or ""

    if stype == "video":
        h = getattr(s, "height", 0) or 0
        if not h:
            res = getattr(s, "resolution", "") or ""
            parts = res.lower().replace("p", "").split("x")
            try:
                h = int(parts[-1])
            except (ValueError, IndexError):
                h = 0

        return f"video {h}p" if h else "video"

    if stype == "audio":
        lang = (getattr(s, "language", "") or "").strip()
        if lang and lang.lower() not in ("und", "n/a", ""):
            return f"audio {lang.upper()}"
        return "audio"

    return stype or "stream"


def _filter_subtitles(sub_list: list, filter_str: str) -> list:
    """
    Filter subtitle list based on the filter string. The filter string can be:
    """
    if not sub_list:
        return []
    if not filter_str or filter_str.lower() in ("false",):
        return []
    if filter_str.lower() == "all":
        return sub_list

    wanted_locales = set()
    for token in filter_str.replace("|", ",").split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            wanted_locales.add(token.lower())
        else:
            resolved = resolve_locale(token)
            if resolved:
                wanted_locales.add(resolved.lower())

    if not wanted_locales:
        return sub_list

    filtered = []
    for s in sub_list:
        lang = (s.get("language") or "").strip().lower()
        if lang in wanted_locales:
            filtered.append(s)

    return filtered


class DASH_Downloader(BaseDownloader):
    """
    High-level DASH downloader.

    Flow
    ----
    1. ``parse_stream()``   — fetch MPD → auto-select → show table
    2. DRM extraction       — collect PSSH from selected Stream.drm objects
    3. Key fetch            — DRMManager → Widevine or PlayReady
    4. ``start_download()`` — run n3u8dl, decrypt, build status dict
    5. Extra audio MPDs     — each gets its own MediaDownloader + key fetch
    6. ``_merge_files()``   — FFmpeg mux
    7. ``_finalize()``      — move, summary, NFO, tracker, cleanup
    """

    def __init__(self, mpd_url: Optional[str] = None, mpd_headers: Optional[Dict[str, str]] = None, mpd_sub_list: Optional[list] = None, mpd_audio_list: Optional[list] = None,
        license_url: Optional[str] = None, license_headers: Optional[Dict[str, str]] = None, license_certificate: Optional[str] = None, license_data: Optional[str] = None,
        output_path: Optional[str] = None, drm_preference: str = "widevine", decrypt_preference: str = "bento4", key: Optional[str] = None, cookies: Optional[Dict[str, str]] = None,
    ):
        self.mpd_url = str(mpd_url).strip() if mpd_url else None
        self.mpd_headers = mpd_headers or get_headers()
        self.mpd_sub_list = mpd_sub_list or []
        self.mpd_audio_list = mpd_audio_list or []

        self.license_url = str(license_url).strip() if license_url else None
        self.license_headers = license_headers
        self.license_certificate = license_certificate
        self.license_data = license_data

        self.drm_preference = drm_preference.lower()
        self.decrypt_preference = decrypt_preference.lower()
        self.key = key
        self.cookies = cookies or {}
        self.drm_manager = DRMManager(get_wvd_path(), get_prd_path(), config_manager.config.get_dict("DRM", "widevine"), config_manager.config.get_dict("DRM", "playready"))

        self.download_id = context_tracker.download_id
        self.site_name = context_tracker.site_name

        if not output_path:
            output_path = f"download.{EXTENSION_OUTPUT}"
        self.output_path = os_manager.get_sanitize_path(output_path)
        if not self.output_path.endswith(f".{EXTENSION_OUTPUT}"):
            self.output_path += f".{EXTENSION_OUTPUT}"

        self.filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        self.output_dir = os.path.join(os.path.dirname(self.output_path), self.filename_base + "_dash_temp")
        self.file_already_exists = os.path.exists(self.output_path)

        self.decryption_keys = []
        self.media_downloader = None
        self.error = None
        self.last_merge_result = None
        self.media_players = None
        self.copied_subtitles = []
        self.copied_audios = []
        self.audio_only = False

    def _collect_drm_from_streams(self, streams: list) -> Dict[str, List[Dict]]:
        """
        Read PSSH data directly from Stream.drm (DRMInfo) on selected streams.

        Returns:
            {
              'WV': [{'pssh': ..., 'kid': ..., 'type': 'Widevine', 'label': ...}, ...],
              'PR': [{'pssh': ..., 'kid': ..., 'type': 'PlayReady', 'label': ...}, ...],
            }
        """
        result: Dict[str, List[Dict]] = {"WV": [], "PR": []}
        seen: Dict[str, set] = {"WV": set(), "PR": set()}

        for s in streams:
            drm = getattr(s, "drm", None)
            if not (getattr(s, "selected", False) and drm and drm.is_encrypted()):
                continue

            label = _stream_drm_label(s)

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
                result[dt].append(
                    {
                        "pssh": pssh,
                        "kid": kid,
                        "type": "Widevine" if dt == "WV" else "PlayReady",
                        "label": label,
                    }
                )

        return result

    def _collect_drm_from_mpd(self, raw_mpd_path: Optional[str]) -> Dict[str, List[Dict]]:
        """Fallback: scan the saved raw .mpd via DashParser to extract PSSH."""
        result: Dict[str, List[Dict]] = {"WV": [], "PR": []}
        try:
            if raw_mpd_path and os.path.exists(raw_mpd_path):
                with open(raw_mpd_path, "r", encoding="utf-8") as f:
                    content = f.read()
                parser = DashParser(self.mpd_url, headers=self.mpd_headers, content=content)
            else:
                parser = DashParser(self.mpd_url, headers=self.mpd_headers)
                if not parser.fetch_manifest():
                    return result

            streams = parser.parse_streams()
            result = self._collect_drm_from_streams(streams)

        except Exception as exc:
            logger.debug(f"_collect_drm_from_mpd error: {exc}")

        return result

    def _fetch_keys(self, drm_psshs: Dict[str, List[Dict]]) -> List[str]:
        """Dispatch key fetch to DRMManager."""
        pref = self.drm_preference  # 'widevine' | 'playready' | 'auto'
        keys = None

        if pref in (_WV, "auto") and drm_psshs.get("WV"):
            try:
                keys = self.drm_manager.get_wv_keys(drm_psshs["WV"], self.license_url, self.license_certificate, self.license_headers, self.key)
            except Exception as exc:
                logger.error(f"Widevine key fetch failed: {exc}")

        if not keys and pref in (_PR, "auto") and drm_psshs.get("PR"):
            try:
                keys = self.drm_manager.get_pr_keys(drm_psshs["PR"], self.license_url, self.license_headers, self.key, self.license_data)
            except Exception as exc:
                logger.error(f"PlayReady key fetch failed: {exc}")

        if not keys and self.key:
            keys = [self.key] if isinstance(self.key, str) else list(self.key)

        return keys or []
    def _fetch_keys_for_audio_mpd(self, audio_url: str, audio_headers: dict, raw_mpd_path: Optional[str], streams: list, license_url: Optional[str] = None, license_hdrs: Optional[dict] = None) -> List[str]:
        """Fetch DRM keys for an extra-audio MPD. Primary: Stream.drm; fallback: DashParser."""
        drm_psshs = self._collect_drm_from_streams(streams)

        if not drm_psshs["WV"] and not drm_psshs["PR"]:
            try:
                if raw_mpd_path and os.path.exists(raw_mpd_path):
                    with open(raw_mpd_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    parser = DashParser(audio_url, headers=audio_headers, content=content)
                else:
                    parser = DashParser(audio_url, headers=audio_headers)
                    parser.fetch_manifest()

                extra_streams = parser.parse_streams()
                extra_drm = self._collect_drm_from_streams(extra_streams)
                for e in extra_drm.get("WV", []):
                    drm_psshs["WV"].append(e)
                for e in extra_drm.get("PR", []):
                    drm_psshs["PR"].append(e)
            except Exception as exc:
                logger.debug(f"Audio DashParser fallback: {exc}")

        if not drm_psshs["WV"] and not drm_psshs["PR"]:
            return []

        eff_url = license_url or self.license_url
        eff_hdrs = license_hdrs or self.license_headers
        pref = self.drm_preference

        keys = None
        if pref in (_WV, "auto") and drm_psshs.get("WV"):
            keys = self.drm_manager.get_wv_keys(drm_psshs["WV"], eff_url, self.license_certificate, eff_hdrs, self.key)
        if not keys and pref in (_PR, "auto") and drm_psshs.get("PR"):
            keys = self.drm_manager.get_pr_keys(drm_psshs["PR"], eff_url, eff_hdrs, self.key, self.license_data)
        return keys or []

    def _download_extra_audios(self) -> tuple[List[Dict], List[Dict]]:
        """Download extra audio tracks from separate MPD URLs."""
        external_audios: List[Dict] = []
        external_subtitles: List[Dict] = []

        for audio_spec in self.mpd_audio_list:
            audio_url = audio_spec.get("url")
            audio_language = audio_spec.get("language", "und")
            audio_headers = audio_spec.get("headers") or self.mpd_headers
            audio_license_url = audio_spec.get("license_url")
            audio_license_headers = audio_spec.get("license_headers")

            if not audio_url:
                console.print(f"[yellow]Skipping extra audio '{audio_language}': missing url")
                continue

            audio_temp_dir = os.path.join(self.output_dir, f"audio_{audio_language}_temp")
            os_manager.create_path(audio_temp_dir)

            try:
                audio_dl = MediaDownloader(
                    url=audio_url,
                    output_dir=audio_temp_dir,
                    filename=self.filename_base,
                    headers=audio_headers,
                    cookies=self.cookies,
                    decrypt_preference=self.decrypt_preference,
                    download_id=None,
                    site_name=self.site_name,
                )
                audio_dl.custom_filters = {
                    "video": "false",
                    "audio": "for=best",
                    "subtitle": SUBTITLE_FILTER,
                }

                if self.download_id:
                    download_tracker.update_status(self.download_id, f"Parsing audio {audio_language}...")
                console.print(f"\n[dim]Parsing DASH for audio {audio_language} ...")
                audio_streams = audio_dl.parse_stream(show_table=False)

                _, raw_mpd_str, _ = audio_dl.get_metadata()
                raw_mpd = raw_mpd_str if raw_mpd_str and raw_mpd_str != "None" else None

                audio_keys = self._fetch_keys_for_audio_mpd(audio_url, audio_headers, raw_mpd, audio_streams, license_url=audio_license_url, license_hdrs=audio_license_headers)

                if not audio_keys:
                    console.print(f"[yellow]No keys for audio {audio_language}, skipping...")
                    continue

                audio_dl.set_key(audio_keys)

                if self.download_id:
                    download_tracker.update_status(self.download_id, f"Downloading audio {audio_language}...")
                console.print(f"\n[dim]Downloading audio {audio_language}...")
                audio_status = audio_dl.start_download()

                if audio_status.get("error"):
                    console.print(f"[yellow]Error audio {audio_language}: {audio_status['error']}")
                    continue

                for af in audio_status.get("audios", []):
                    fpath = af.get("path")
                    if fpath and os.path.exists(fpath):
                        ext = os.path.splitext(fpath)[1]
                        final_path = os.path.join(self.output_dir, f"{self.filename_base}.{audio_language}{ext}")
                        try:
                            shutil.move(fpath, final_path)
                            external_audios.append(
                                {
                                    "file": os.path.basename(final_path),
                                    "language": audio_language,
                                    "path": final_path,
                                }
                            )
                        except Exception as e:
                            console.print(f"[yellow]Could not move audio {audio_language}: {e}")

                for sf in audio_status.get("subtitles", []):
                    fpath = sf.get("path")
                    if fpath and os.path.exists(fpath):
                        ext = os.path.splitext(fpath)[1]
                        sub_lang = (sf.get("language") or sf.get("name") or audio_language)
                        final_sub = os.path.join(self.output_dir, f"{self.filename_base}.{sub_lang}{ext}")
                        try:
                            shutil.move(fpath, final_sub)
                            external_subtitles.append(
                                {
                                    "path": final_sub,
                                    "language": sub_lang,
                                    "name": sub_lang,
                                    "size": os.path.getsize(final_sub),
                                }
                            )
                        except Exception as e:
                            console.print(f"[yellow]Could not move subtitle {sub_lang}: {e}")

            except Exception as e:
                console.print(f"[yellow]Warning on extra audio {audio_language}: {e}")
                logger.exception(f"Extra audio download failed for {audio_language}")
            finally:
                shutil.rmtree(audio_temp_dir, ignore_errors=True)

        return external_audios, external_subtitles

    def start(self) -> tuple[Optional[str], bool]:
        """
        Execute the full DASH download pipeline.
        Returns ``(output_path, cancelled)`` — cancelled=True means abort.
        """
        if self.file_already_exists:
            console.print("[yellow]File already exists.")
            return self.output_path, False

        os_manager.create_path(self.output_dir)

        try:
            self.media_players = MediaPlayers(self.output_dir)
            self.media_players.create()
        except Exception:
            pass

        self.media_downloader = MediaDownloader(
            url=self.mpd_url,
            output_dir=self.output_dir,
            filename=self.filename_base,
            headers=self.mpd_headers,
            cookies=self.cookies,
            decrypt_preference=self.decrypt_preference,
            download_id=self.download_id,
            site_name=self.site_name,
        )
        self.media_downloader.license_url = self.license_url
        self.media_downloader.drm_type = self.drm_preference

        if self.mpd_sub_list and SUBTITLE_FILTER != "false":
            filtered_subs = _filter_subtitles(self.mpd_sub_list, SUBTITLE_FILTER)
            if filtered_subs:
                console.print(f"[dim]Adding {len(filtered_subs)} external subtitle(s) (filtered from {len(self.mpd_sub_list)}).")
                self.media_downloader.external_subtitles = filtered_subs
            else:
                console.print(f"[dim]No subtitles matched filter '{SUBTITLE_FILTER}'.")

        if self.mpd_audio_list and AUDIO_FILTER != "false":
            console.print(f"[dim]Adding {len(self.mpd_audio_list)} external audio(s).")

        # ── Parse ─────────────────────────────────────────────────────────────
        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing DASH ...")

        streams = self.media_downloader.parse_stream(show_table=context_tracker.should_print)

        _, raw_mpd_str, _ = self.media_downloader.get_metadata()
        raw_mpd = raw_mpd_str if raw_mpd_str and raw_mpd_str != "None" else None

        # ── DRM ───────────────────────────────────────────────────────────────
        drm_psshs = self._collect_drm_from_streams(streams)
        is_protected = bool(drm_psshs.get("WV") or drm_psshs.get("PR"))

        if not is_protected and raw_mpd:
            logger.info("No PSSH in Stream objects — falling back to MPDParser")
            drm_psshs = self._collect_drm_from_mpd(raw_mpd)
            is_protected = bool(drm_psshs.get("WV") or drm_psshs.get("PR"))

        if is_protected:
            if not self.license_url and not self.key:
                msg = "DRM detected but missing both license_url and key."
                console.print(f"[yellow]{msg}")
                self.error = msg
                if self.download_id:
                    download_tracker.complete_download(self.download_id, success=False, error=self.error)
                return None, True

            if self.download_id:
                download_tracker.update_status(self.download_id, "Fetching keys ...")

            self.decryption_keys = self._fetch_keys(drm_psshs)

            if not self.decryption_keys:
                self.error = "Failed to fetch decryption keys"
                if self.download_id:
                    download_tracker.complete_download(self.download_id, success=False, error=self.error)
                return None, True

        # ── Download ──────────────────────────────────────────────────────────
        if SKIP_DOWNLOAD:
            console.print("[yellow]Skipping download as per configuration.")
            return self.output_path, False

        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")
        print()

        self.media_downloader.set_key(self.decryption_keys)
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

        # ── Extra audio MPDs ──────────────────────────────────────────────────
        if self.mpd_audio_list and AUDIO_FILTER != "false":
            if self.download_id:
                download_tracker.update_status(self.download_id, f"Downloading {len(self.mpd_audio_list)} extra audio track(s)...")
            extra_audios, extra_subs = self._download_extra_audios()
            status["external_audios"] = extra_audios
            if extra_subs:
                existing = {s.get("path") for s in status.get("subtitles", [])}
                for sub in extra_subs:
                    if sub.get("path") not in existing:
                        status["subtitles"].append(sub)
                        existing.add(sub.get("path"))

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
        return self.output_path, False
