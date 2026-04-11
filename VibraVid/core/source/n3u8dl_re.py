# 09.04.26 — refactored: duplicated logic moved to BaseMediaDownloader (base.py)

from __future__ import annotations

import asyncio
import logging
import platform
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.console import Console

from VibraVid.setup import get_ffmpeg_path, get_n_m3u8dl_re_path
from VibraVid.utils import config_manager
from VibraVid.utils.tmdb_client import tmdb_client
from VibraVid.core.ui.tracker import download_tracker
from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.core.utils.language import resolve_locale
from VibraVid.core.downloader.subtitle import download_external_tracks_with_progress
from VibraVid.core.utils.decrypt_engine import Decryptor, KeysManager
from .base import BaseMediaDownloader


console = Console(force_terminal=True if platform.system().lower() != "windows" else None)
logger  = logging.getLogger("n3u8dl_re")
CONCURRENT_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "concurrent_download")
THREAD_COUNT    = config_manager.config.get_int("DOWNLOAD", "thread_count")
RETRY_COUNT     = config_manager.config.get_int("REQUESTS", "max_retry")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")
MAX_SPEED       = config_manager.config.get("DOWNLOAD", "max_speed")
USE_PROXY       = config_manager.config.get_bool("REQUESTS", "use_proxy")
PROXY_CFG       = config_manager.config.get_dict("REQUESTS", "proxy")

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
_SPEED_RE   = re.compile(r"(\d+(?:\.\d+)?(?:MB|KB|GB|B)ps)")
_SIZE_RE    = re.compile(r"(\d+(?:\.\d+)?(?:MB|GB|KB|B))/(\d+(?:\.\d+)?(?:MB|GB|KB|B))")
_SEG_RE     = re.compile(r"(\d+)/(\d+)")
_VID_RES_RE = re.compile(r"Vid\s+(\d+x\d+)")
_AUD_PROG_RE = re.compile(r"Aud\s+(.+?)\s*\|\s*([\w-]+)(?:\s{3,}|\s*-{5,}|$)")
_SUB_PROG_RE = re.compile(r"Sub\s+([\w-]+)\s*\|\s*(.+?)(?:\s{3,}|\s*-{5,}|$)")
_SUBFIN_RE  = re.compile(r"(\d+\.?\d*(?:B|KB|MB|GB))\s+-\s+00:00:00")


class MediaDownloader(BaseMediaDownloader):
    def __init__(
        self, url: str, output_dir: str, filename: str, headers: Optional[Dict] = None, key: Optional[Any] = None, cookies: Optional[Dict] = None,
        download_id: Optional[str] = None, site_name: Optional[str] = None, max_segments: Optional[int] = None
    ) -> None:
        super().__init__(
            url=url,
            output_dir=output_dir,
            filename=filename,
            headers=headers,
            key=key,
            cookies=cookies,
            download_id=download_id,
            site_name=site_name,
        )
        self.max_segments = max_segments    # !!!! NOT USE

    def set_key(self, key: Any) -> None:
        key_type  = type(key).__name__
        key_count = (
            len(key) if isinstance(key, list)
            else (len(key.get_keys_list()) if isinstance(key, KeysManager) else 1)
        )
        logger.info(f"set_key() called: key_type={key_type}, key_count={key_count}")
        self.key = key.get_keys_list() if isinstance(key, KeysManager) else key

    def start_download(self) -> Dict[str, Any]:
        """Build command → run N_m3u8DL-RE → download externals → return status dict."""
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")

        # N_m3u8DL-RE always uses post-download decryption
        self.actual_live_decryption = False
        selected_streams = [
            s for s in self.streams
            if s.selected and not s.is_external and s.type in ("video", "audio")
        ]
        if selected_streams:
            logger.info(
                "N_m3u8DL-RE uses post-download decryption: keys are obtained after "
                "download completes, file decrypted via bento4."
            )

        # Promote HLS subtitle streams to external download list (shared helper)
        self._promote_hls_subtitles_to_external()

        sv = self._sv or "best"
        sa = self._sa or "best"
        ss = "false"
        proxy = (PROXY_CFG.get("https") or PROXY_CFG.get("http", "")) if USE_PROXY else ""
        self._prepare_labels()

        cmd = self._build_command(sv=sv, sa=sa, ss=ss, proxy=proxy)
        logger.info(f"N_m3u8DL-RE command: {' '.join(cmd)}")

        with DownloadBarManager(self.download_id) as bar_manager:
            bar_manager.add_prebuilt_tasks(self._get_prebuilt_tasks())
            self._register_external_track_tasks(bar_manager)

            loop = asyncio.new_event_loop()
            download_result: Dict[str, Any] = {"ext_subs": [], "ext_auds": []}

            def _run_externals() -> None:
                asyncio.set_event_loop(loop)
                try:
                    ext_subs, ext_auds = loop.run_until_complete(
                        download_external_tracks_with_progress(
                            self.headers,
                            self.external_subtitles,
                            self.external_audios,
                            self.output_dir,
                            self.filename,
                            bar_manager,
                        )
                    )
                    download_result["ext_subs"] = ext_subs
                    download_result["ext_auds"] = ext_auds
                except Exception as exc:
                    logger.error(f"External downloads failed: {exc}")
                finally:
                    loop.close()

            download_thread = threading.Thread(target=_run_externals, daemon=False)
            download_thread.start()

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, universal_newlines=True,
            )
            if self.download_id:
                download_tracker.register_process(self.download_id, proc)

            with proc:
                for line in proc.stdout:
                    if " : " in line:
                        logger.info(line.rstrip())
                    if self.download_id and download_tracker.is_stopped(self.download_id):
                        proc.terminate()
                        break
                    bar_manager.handle_progress_line(self._parse_progress_line(line))
                bar_manager.finish_all_tasks()

            download_thread.join(timeout=300)
            if download_thread.is_alive():
                logger.error("External download thread timed out — proceeding anyway")

            ext_subs = download_result["ext_subs"]
            ext_auds = download_result["ext_auds"]

        if self.download_id and download_tracker.is_stopped(self.download_id):
            return {"error": "cancelled"}

        self.status = self._build_status(ext_subs, ext_auds)

        logger.info(f"Post-decrypt check: key={bool(self.key)}, actual_live_decryption={self.actual_live_decryption}")
        if self.key and not (self.actual_live_decryption):
            logger.info("Post-decrypt check: WILL decrypt files now")
            self._decrypt_check(self.status)
        else:
            reason = []
            if not self.key:
                reason.append("no key")
            if self.actual_live_decryption:
                reason.append("live decryption with ffmpeg/bento4")
            logger.info("Post-decrypt check: SKIPPING decryption. ")

        return self.status

    def _build_command(self, sv: str, sa: str, ss: str, proxy: str = "") -> List[str]:
        cmd: List[str] = [
            get_n_m3u8dl_re_path(),
            "--save-name",            self.filename,
            "--save-dir",             str(self.output_dir),
            "--tmp-dir",              str(self._tmp_dir),
            "--ffmpeg-binary-path",   get_ffmpeg_path(),
            "--write-meta-json",      "false",
            "--binary-merge",
            "--del-after-done",
            "--auto-subtitle-fix",    "false",
            "--check-segments-count", "false",
            "--no-log",
        ]

        cmd.extend(["--drop-video", "all"]  if sv == "false" else ["--select-video", sv])
        cmd.extend(["--drop-audio", "all"]  if sa == "false" else ["--select-audio", sa])
        cmd.extend(["--drop-subtitle", "all"])  # subtitles always handled externally

        for k, v in self.headers.items():
            cmd.extend(["--header", f"{k}: {v}"])
        if self.cookies:
            cmd.extend(["--header", "Cookie: " + "; ".join(f"{k}={v}" for k, v in self.cookies.items())])

        if USE_PROXY and proxy:
            cmd.extend(["--use-system-proxy", "false", "--custom-proxy", proxy])
        if CONCURRENT_DOWNLOAD:
            cmd.append("--concurrent-download")
        if THREAD_COUNT > 0:
            cmd.extend(["--thread-count", str(THREAD_COUNT)])
        if REQUEST_TIMEOUT > 0:
            cmd.extend(["--http-request-timeout", str(REQUEST_TIMEOUT)])
        if RETRY_COUNT > 0:
            cmd.extend(["--download-retry-count", str(RETRY_COUNT)])
        if MAX_SPEED and str(MAX_SPEED).lower() not in ("", "false"):
            cmd.extend(["--max-speed", str(MAX_SPEED)])

        cmd.append(self.url)
        return cmd

    def _decrypt_check(self, status: Dict[str, Any]) -> None:
        """Post-download decryption: decrypt video/audio after N_m3u8DL-RE finishes."""
        logger.info(
            f"_decrypt_check: Starting post-download decryption. "
            f"Key type: {type(self.key).__name__}, "
            f"License URL: {bool(self.license_url)}, DRM: {self.drm_type}"
        )

        if self.download_id:
            download_tracker.update_status(self.download_id, "Decrypting ...")

        decryptor = Decryptor(
            license_url=self.license_url,
            drm_type=self.drm_type,
        )
        keys = (
            self.key.get_keys_list() if isinstance(self.key, KeysManager)
            else ([self.key] if isinstance(self.key, str) else self.key)
        )
        logger.info(f"_decrypt_check: Found {len(keys) if isinstance(keys, list) else 1} key(s)")

        targets = []
        if status.get("video"):
            targets.append((status["video"], "video"))
        for aud in status.get("audios", []):
            targets.append((aud, "audio"))

        for target, stype in targets:
            fp = Path(target["path"])
            if not fp.exists():
                logger.warning(f"_decrypt_check: File not found for decryption: {fp}")
                continue
            logger.info(f"_decrypt_check: Decrypting {stype} file: {fp.name}")
            out = fp.with_suffix(fp.suffix + ".dec")
            if decryptor.decrypt(str(fp), keys, str(out), stream_type=stype):
                try:
                    fp.unlink()
                    out.rename(fp)
                    target["size"] = fp.stat().st_size
                    logger.info(f"_decrypt_check: ✓ {stype} decrypted successfully: {fp.name}")
                except Exception as exc:
                    logger.error(f"Failed to replace encrypted file: {exc}")
                    if out.exists():
                        out.unlink()
            else:
                logger.error(f"_decrypt_check: ✗ Decryption failed for {stype}: {fp.name}")
                if out.exists():
                    try:
                        out.unlink()
                    except Exception:
                        pass

    def _parse_progress_line(self, line: str) -> Optional[Dict[str, Any]]:
        """
        Parse one N_m3u8DL-RE stdout line.
        Returns a dict with progress data, or None for irrelevant lines.
        """
        line_s = line.strip()
        if not line_s:
            return None

        result: Dict[str, Any] = {}

        if line_s.startswith("Vid"):
            if not (
                _VID_RES_RE.search(line_s)
                or re.search(r"Vid\s+[\d.]+\s*[KMGT]?bps", line_s)
                or "Vid main" in line_s
                or "Vid " in line_s
            ):
                return None
            result["_task_key"]  = self._video_task_key
            result["label"]      = f"[bold cyan]Vid[/bold cyan] {self._video_label}"
            result["_lang_code"] = ""

        elif line_s.startswith("Aud"):
            result["track"] = "audio"
            content = re.split(r"\s{3,}|\s*-{5,}", line_s[3:])[0].strip()
            parts   = [p.strip().lower() for p in content.split("|")]

            lang_code = ""
            label     = ""

            for p in parts:
                if p in self._audio_labels:
                    lang_code, label = p, self._audio_labels[p]
                    break

            if not label:
                m = re.search(r"\b([a-z]{2}(?:-[a-z]{2})?)\b", content, re.I)
                if m:
                    extracted = m.group(1).lower()
                    if extracted in self._audio_labels:
                        lang_code, label = extracted, self._audio_labels[extracted]

            if not label:
                m = re.search(r"(\d+(?:\.\d+)?)\s*([KMG]?bps)", content, re.I)
                if m:
                    br_str = m.group(0).lower()
                    for k, v in self._audio_labels.items():
                        if br_str in v.lower():
                            lang_code, label = k, v
                            break

            if not label:
                m = _AUD_PROG_RE.search(line_s)
                lang_code = m.group(2).strip().lower() if m else ""

            normalized = resolve_locale(lang_code) if lang_code else ""
            task_lang  = normalized.split("-")[0].lower() if normalized else lang_code
            result["label"]      = (
                f"[bold cyan]Aud[/bold cyan] "
                f"{label or f'[bold white]{content or chr(65)}[/bold white]'}"
            )
            result["_task_key"]  = f"aud_{task_lang or 'main'}"
            result["_lang_code"] = lang_code

        elif line_s.startswith("Sub"):
            result["track"] = "subtitle"
            m = _SUB_PROG_RE.search(line_s)
            lang_code    = m.group(1).strip().lower() if m else ""
            display_name = m.group(2).strip()         if m else ""

            name_slug    = tmdb_client._slugify(display_name) if display_name else lang_code
            compound_key = f"{lang_code}:{name_slug}"
            base         = lang_code.split("-")[0]
            label = (
                self._sub_labels.get(compound_key)
                or self._sub_labels.get(lang_code, "")
                or self._sub_labels.get(f"{base}:{name_slug}", "")
                or self._sub_labels.get(base, "")
            )
            result["label"]      = (
                f"[bold cyan]Sub[/bold cyan] "
                f"{label or f'[bold white]{display_name or lang_code}[/bold white]'}"
            )
            result["_task_key"]  = (
                f"sub_{lang_code}_{name_slug}" if name_slug else f"sub_{lang_code}"
            )
            result["_lang_code"] = lang_code

            fm = _SUBFIN_RE.search(line_s)
            if fm:
                result["final_size"] = fm.group(1)
                result["pct"]        = 100.0
        else:
            return None

        # Numeric progress fields
        m = _PERCENT_RE.search(line_s)
        if m:
            result["pct"] = float(m.group(1))

        m = _SEG_RE.search(line_s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b > 0:
                result["segments"] = f"{a}/{b}"
                if "pct" not in result:
                    result["pct"] = (a / b) * 100.0

        m = _SIZE_RE.search(line_s)
        if m:
            result["size"] = f"{m.group(1)}/{m.group(2)}"

        m = _SPEED_RE.search(line_s)
        if m:
            result["speed"] = m.group(1)

        has_data = any(k in result for k in ("pct", "segments", "size", "speed", "final_size"))

        if has_data or any(k in result for k in ("_task_key", "track")):
            if not has_data:
                result["pct"] = 0.0
            return result

        return None