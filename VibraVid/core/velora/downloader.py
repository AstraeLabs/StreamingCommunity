# 09.04.26

import re
import asyncio
import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import get_proxy_url
from VibraVid.core.ui.tracker import download_tracker
from VibraVid.core.ui.bar_manager import DownloadBarManager, console
from VibraVid.core.decryptor import KeysManager

from VibraVid.core.velora.bridge import run_download_plan
from VibraVid.core.velora.subtitle import download_external_tracks_with_progress
from VibraVid.core.decryptor._models import detect_encryption_info

from .base import BaseMediaDownloader
from .downloader_live import LiveDownloadMixin
from ._stream_vod import VodStreamMixin
from ._multiperiod import MultiPeriodMixin
from ._decrypt_pipeline import DecryptPipelineMixin
from ._ism_postproc import IsmPostprocMixin
from .util._stream_helpers import detect_seg_ext, join_interruptible, print_failed_segments_report, SilentDownloadBarManager


logger = logging.getLogger("manual")
CONCURRENT_DL = config_manager.config.get_bool("DOWNLOAD", "concurrent_download")
THREAD_COUNT = config_manager.config.get_int("DOWNLOAD",  "thread_count")
RETRY_COUNT = config_manager.config.get_int("REQUESTS",  "max_retry")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS",  "timeout")
VERIFY_TLS = config_manager.config.get_bool("REQUESTS", "verify")
REALTIME_DECRYPT = config_manager.config.get_bool("DOWNLOAD", "realtime_decrypt")
SEGMENT_DELAY_SECONDS = max(0.0, config_manager.config.get_float("DOWNLOAD", "segment_delay_seconds"))
SEGMENT_DELAY_JITTER_SECONDS = max(0.0, config_manager.config.get_float("DOWNLOAD", "segment_delay_jitter_seconds"))


class MediaDownloader(LiveDownloadMixin, VodStreamMixin, MultiPeriodMixin, DecryptPipelineMixin, IsmPostprocMixin, BaseMediaDownloader):
    def __init__(self, url: str, output_dir: str, filename: str, headers: Optional[Dict] = None, key: Optional[Any] = None, cookies: Optional[Dict] = None, download_id: Optional[str] = None, site_name: Optional[str] = None, max_segments: Optional[int] = None, max_time: Optional[float] = None, manifest_content: Optional[str] = None, manifest_protocol: Optional[str] = None, manifest_refresh_fn=None) -> None:
        super().__init__(
            url=url,
            output_dir=output_dir,
            filename=filename,
            headers=headers,
            key=key,
            cookies=cookies,
            download_id=download_id,
            site_name=site_name,
            manifest_content=manifest_content,
            manifest_protocol=manifest_protocol,
            manifest_refresh_fn=manifest_refresh_fn,
        )
        self.max_segments = max_segments
        self.max_time = max_time

        # Cancellation
        self._stop_event: threading.Event = threading.Event()
        self._active_loops: List[asyncio.AbstractEventLoop] = []
        self._loops_lock: threading.Lock = threading.Lock()

        # Live-decryption tracking
        self._session_live_decrypt: bool = False

        # Failed-segment accumulator
        self._failed_segments: list = []
        self._failed_segments_lock = threading.Lock()

    def start_download(self, show_progress: bool = True) -> Dict[str, Any]:
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")

        self._promote_hls_subtitles_to_external()
        self._prepare_labels()

        selected_media = [
            s for s in self.streams
            if s.selected and not s.is_external
            and s.type in ("video", "audio", "subtitle")
        ]
        all_support_live = (all(s.supports_live_decryption for s in selected_media) if selected_media else False)

        if all_support_live and selected_media and REALTIME_DECRYPT:
            self._session_live_decrypt = True
            logger.info("All selected streams support live decryption — using in-flight decryption.")
        else:
            self._session_live_decrypt = False
            if selected_media and not all_support_live:
                logger.info("SAMPLE-AES/CBCS detected — using post-merge decryption with Shaka Packager.")
                no_keys = (
                    self.key is None
                    or (isinstance(self.key, KeysManager) and not self.key.get_keys_list())
                    or (isinstance(self.key, str) and not self.key.strip())
                    or (isinstance(self.key, (list, tuple)) and not self.key)
                )

                if no_keys:
                    console.print("[red]Warning:[/red] SAMPLE-AES/CBCS streams detected but no keys provided.")
                    logger.error("No keys provided for post-download decryption — merged file will remain encrypted.")

            else:
                logger.info("Using post-download decryption.")

        ext_result: Dict[str, Any] = {"ext_subs": [], "ext_auds": []}

        try:
            bar_ctx = (
                DownloadBarManager(self.download_id)
                if show_progress
                else SilentDownloadBarManager(self.download_id)
            )

            with bar_ctx as bar_manager:
                bar_manager.add_prebuilt_tasks(self._get_prebuilt_tasks())
                self._register_external_track_tasks(bar_manager)

                ext_loop = asyncio.new_event_loop()
                self._register_loop(ext_loop)

                def _run_externals() -> None:
                    asyncio.set_event_loop(ext_loop)
                    try:
                        subs, auds = ext_loop.run_until_complete(
                            download_external_tracks_with_progress(
                                self.headers,
                                self.external_subtitles,
                                self.external_audios,
                                self.output_dir,
                                self.filename,
                                bar_manager,
                                stop_check=self._stop_check,
                            )
                        )
                        ext_result["ext_subs"] = subs
                        ext_result["ext_auds"] = auds

                    except Exception as exc:
                        logger.error(f"External downloads failed: {exc}")

                    finally:
                        self._unregister_loop(ext_loop)
                        ext_loop.close()

                def _run_stream(s) -> None:
                    try:
                        self._download_stream(s, bar_manager)
                    except Exception as exc:
                        logger.error(f"Stream download error ({s.type}/{s.language}): {exc}", exc_info=True)

                if CONCURRENT_DL:
                    ext_thread = threading.Thread(target=_run_externals, daemon=True)
                    ext_thread.start()

                    media_threads: List[threading.Thread] = []
                    for stream in selected_media:
                        t = threading.Thread(target=_run_stream, args=(stream,), daemon=True)
                        media_threads.append(t)
                        t.start()

                    join_interruptible(media_threads, self._stop_event)
                    bar_manager.finish_all_tasks()
                    join_interruptible([ext_thread], self._stop_event, hard_timeout=300.0)

                else:
                    logger.info("Sequential download: video -> audio -> subtitles -> external tracks.")
                    video_streams  = [s for s in selected_media if s.type == "video"]
                    audio_streams  = [s for s in selected_media if s.type == "audio"]
                    sub_streams    = [s for s in selected_media if s.type == "subtitle"]

                    for stream in video_streams:
                        if self._stop_check():
                            break
                        t = threading.Thread(target=lambda s=stream: _run_stream(s), daemon=True)
                        t.start()
                        join_interruptible([t], self._stop_event)

                    for stream in audio_streams:
                        if self._stop_check():
                            break
                        t = threading.Thread(target=lambda s=stream: _run_stream(s), daemon=True)
                        t.start()
                        join_interruptible([t], self._stop_event)

                    for stream in sub_streams:
                        if self._stop_check():
                            break
                        t = threading.Thread(target=lambda s=stream: _run_stream(s), daemon=True)
                        t.start()
                        join_interruptible([t], self._stop_event)

                    bar_manager.finish_all_tasks()

                    if not self._stop_check():
                        ext_thread = threading.Thread(target=_run_externals, daemon=True)
                        ext_thread.start()
                        join_interruptible([ext_thread], self._stop_event, hard_timeout=300.0)

                ext_subs = ext_result["ext_subs"]
                ext_auds = ext_result["ext_auds"]

        except KeyboardInterrupt:
            self._stop_event.set()
            self._cancel_all_loops()
            if self.download_id:
                download_tracker.request_stop(self.download_id)
            raise

        if self._stop_event.is_set() or (self.download_id and download_tracker.is_stopped(self.download_id)):
            return {"error": "cancelled"}

        if self._failed_segments:
            print_failed_segments_report(self._failed_segments)
            self._failed_segments.clear()

        self.status = self._build_status(ext_subs, ext_auds)
        return self.status

    def _register_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._loops_lock:
            self._active_loops.append(loop)

    def _unregister_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._loops_lock:
            try:
                self._active_loops.remove(loop)
            except ValueError:
                pass

    def _cancel_all_loops(self) -> None:
        with self._loops_lock:
            for loop in list(self._active_loops):
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass

    def _stop_check(self) -> bool:
        return self._stop_event.is_set() or bool(self.download_id and download_tracker.is_stopped(self.download_id))

    def _run_dl(self, segs: List[Dict], out_dir: Path, headers: Dict, progress_cb, stream=None, event_cb=None, default_ext: str = "ts") -> List[Path]:
        try:
            plan_task_key = self._stream_task_key(stream) if stream else "download"
            if stream and stream.type == "video":
                plan_label = self._video_label

            elif stream and stream.type == "audio":
                plan_label = self._audio_labels.get((stream.language or "und").lower(), "")

            elif stream and stream.type == "subtitle":
                plan_label = self._sub_labels_by_task_key.get(plan_task_key, "")
                if not plan_label:
                    lang_raw  = (stream.language or "und").lower()
                    plan_label = self._sub_labels.get(lang_raw) or self._sub_labels.get(lang_raw.split("-")[0]) or ""

            else:
                plan_label = ""

            logger.debug(f"Starting download plan for {plan_task_key} with {len(segs)} segments")
            plan_label_or_key = plan_label or plan_task_key
            tasks = []
            for seg in segs:
                seg_ext = detect_seg_ext(seg.get("url", ""), default=default_ext)
                if seg_ext == "m4s":
                    seg_ext = "mp4"

                tasks.append({
                    "task_key": plan_task_key,
                    "label": plan_label_or_key,
                    "display_label": plan_label_or_key,
                    "url": seg["url"],
                    "path": str(out_dir / f"seg_{seg['number']:05d}.{seg_ext}"),
                    "headers": seg.get("headers", {}),
                })

            plan = {
                "project": "Velora",
                "version": 1,
                "task_key": plan_task_key,
                "label": plan_label_or_key,
                "display_label": plan_label_or_key,
                "concurrency": THREAD_COUNT,
                "retry_count": RETRY_COUNT,
                "timeout_seconds": REQUEST_TIMEOUT,
                "retry_base_delay_seconds": 1.0,
                "retry_max_delay_seconds": 4.0,
                "retry_jitter_seconds": 0.25,
                "segment_delay_seconds": SEGMENT_DELAY_SECONDS,
                "segment_delay_jitter_seconds": SEGMENT_DELAY_JITTER_SECONDS,
                "proxy_url": get_proxy_url(),
                "verify_tls": VERIFY_TLS,
                "headers": headers,
                "tasks": tasks,
            }
            results = run_download_plan(plan, progress_cb=progress_cb, event_cb=event_cb, stop_check=self._stop_check)
            return [Path(item["path"]) for item in results if item.get("path")]

        except Exception as exc:
            logger.error(f"_run_dl failed: {exc}", exc_info=True)
            return []

    def _probe_media_file(self, target_path: Path) -> None:
        try:
            if not target_path.exists() or target_path.stat().st_size <= 0:
                logger.warning(f"[PROBE] Probe target not found or empty: {target_path}")
                return

            from VibraVid.setup import get_ffprobe_path
            from VibraVid.core.muxing.helper.info import Mediainfo
            ffprobe_path = get_ffprobe_path()

            async def _run_probes() -> None:
                await asyncio.gather(
                    Mediainfo.from_file_async(ffprobe_path, str(target_path)),
                    self._report_drm_info_async(target_path),
                )

            asyncio.run(_run_probes())
        except Exception as exc:
            logger.warning(f"[PROBE] Could not probe media file: {exc}")

    async def _report_drm_info_async(self, target_path: Path) -> None:
        """Async wrapper: off-load the (blocking) DRM inspection to a thread."""
        try:
            await asyncio.to_thread(self._report_drm_info, target_path)
        except Exception as exc:
            logger.debug(f"[PROBE][DRM] info detection failed: {exc}")

    def _report_drm_info(self, target_path: Path) -> None:
        """For DRM media only: log the Widevine KID, PlayReady KID and CENC scheme type."""
        info = detect_encryption_info(str(target_path))
        if not info.encrypted:
            logger.info(f"[PROBE][{target_path.name}] No DRM (clear)")
            return

        # A track has exactly one content key (tenc.default_KID), shared by
        # every DRM system (Widevine/PlayReady/FairPlay) protecting it.
        kid = info.kid or "N/A"
        scheme = info.scheme or "unknown"
        track_ids = info.track_ids or []
        logger.info(f"[PROBE][{target_path.name}] Scheme: {scheme}, KID: {kid}, Track IDs: {track_ids}")

    def _build_headers(self) -> Dict:
        h = dict(self.headers)

        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())

        if "Referer" not in h and "referer" not in h:
            try:
                parsed = urlparse(self.url)
                h["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
            except Exception:
                pass

        h.setdefault("Accept", "*/*")
        h.setdefault("Accept-Encoding", "gzip, deflate")
        return h

    def _out_filename(self, stream, ext: str) -> str:
        if stream.type == "video":
            return f"{self.filename}.{ext}"

        lang = re.sub(r"[^\w\-]", "_", (stream.language or "und").lower())
        if stream.type == "subtitle":
            if getattr(stream, "is_wvtt_mp4", False):
                base = f"{self.filename}.{lang}.wvtt"
            else:
                _protocols = ("dash", "hls", "mp4", "m4s", "ts", "m2ts", "")
                fmt = (stream.format or "").lower().strip()
                seg = (ext or "").lower().strip()
                sub_ext = fmt if fmt not in _protocols else (seg if seg not in _protocols else "vtt")
                base = f"{self.filename}.{lang}.{sub_ext}"

            with self._assigned_sub_lock:
                if base not in self._assigned_sub_names:
                    self._assigned_sub_names.add(base)
                    return base
                counter = 2
                while True:
                    stem, _, ext_part = base.rpartition(".")
                    candidate = f"{stem}_{counter}.{ext_part}"
                    if candidate not in self._assigned_sub_names:
                        self._assigned_sub_names.add(candidate)
                        return candidate
                    counter += 1

        audio_ext = "webm" if ext == "webm" else "m4a"
        return f"{self.filename}.{lang}.{audio_ext}"
