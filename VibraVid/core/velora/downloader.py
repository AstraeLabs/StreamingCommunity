# 09.04.26

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from VibraVid.core.ui.bar_manager import DownloadBarManager, console
from VibraVid.core.ui.tracker import download_tracker
from VibraVid.core.velora.bridge import run_download_plan
from VibraVid.core.velora.curl_bridge import run_download_plan_curl_cffi
from VibraVid.core.velora.subtitle import download_external_tracks_with_progress
from VibraVid.setup import get_flux_path
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import get_proxy_url

from ._decrypt_pipeline import DecryptPipelineMixin
from ._ism_postproc import IsmPostprocMixin
from ._multiperiod import MultiPeriodMixin
from ._stream_vod import VodStreamMixin
from .base import BaseMediaDownloader
from .downloader_live import LiveDownloadMixin
from .util._stream_helpers import (
    SilentDownloadBarManager,
    detect_seg_ext,
    join_interruptible,
    print_failed_segments_report,
    safe_name,
)

logger = logging.getLogger("manual")
CONCURRENT_DL = config_manager.config.get_bool("DOWNLOAD", "concurrent_download")
THREAD_COUNT = config_manager.config.get_int("DOWNLOAD", "thread_count")
RETRY_COUNT = config_manager.config.get_int("REQUESTS", "max_retry")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")
VERIFY_TLS = config_manager.config.get_bool("REQUESTS", "verify")
SKIP_POST_DECRYPT = config_manager.config.get_bool("DOWNLOAD", "skip_post_decrypt", default=False)
SEGMENT_DELAY_SECONDS = max(0.0, config_manager.config.get_float("DOWNLOAD", "segment_delay_seconds"))
SEGMENT_DELAY_JITTER_SECONDS = max(0.0, config_manager.config.get_float("DOWNLOAD", "segment_delay_jitter_seconds"))


class MediaDownloader(
    LiveDownloadMixin, VodStreamMixin, MultiPeriodMixin, DecryptPipelineMixin, IsmPostprocMixin, BaseMediaDownloader
):
    def __init__(
        self,
        url: str,
        output_dir: str,
        filename: str,
        headers: dict | None = None,
        key: Any | None = None,
        cookies: dict | None = None,
        download_id: str | None = None,
        site_name: str | None = None,
        max_segments: int | tuple[int, int | None] | None = None,
        max_time: float | tuple[float, float | None] | None = None,
        manifest_content: str | None = None,
        manifest_protocol: str | None = None,
        manifest_refresh_fn=None,
        has_drm: bool = False,
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
            manifest_content=manifest_content,
            manifest_protocol=manifest_protocol,
            manifest_refresh_fn=manifest_refresh_fn,
            has_drm=has_drm,
        )
        self.max_segments = max_segments
        self.max_time = max_time

        # Cancellation
        self._stop_event: threading.Event = threading.Event()
        self._active_loops: list[asyncio.AbstractEventLoop] = []
        self._loops_lock: threading.Lock = threading.Lock()

        # Live-decryption tracking
        self._session_live_decrypt: bool = False

        # Failed-segment accumulator
        self._failed_segments: list = []
        self._failed_segments_lock = threading.Lock()
        self.missing_segments_count: int = 0

        # Decryption-failure accumulator: per-track records for streams that are still encrypted after decrypt
        self.decrypt_failures: list = []
        self._decrypt_failures_lock = threading.Lock()

    def start_download(self, show_progress: bool = True) -> dict[str, Any]:
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")

        self._promote_hls_subtitles_to_external()
        self._prepare_labels()

        selected_media = [
            s for s in self.streams if s.selected and not s.is_external and s.type in ("video", "audio", "subtitle")
        ]
        all_support_live = all(s.supports_live_decryption for s in selected_media) if selected_media else False
        flux_available = bool(get_flux_path())

        # Live (in-flight) decryption is automatic: it engages whenever every
        # selected stream is truly segmented (a real init/moov per the manifest).
        # The per-stream `_frag_init_probe()` in `_stream_vod.py` downgrades to the
        # post-download decrypt pass if the first init turns out not to be a valid
        # ftyp+moov, so there is no config knob to get wrong.
        if all_support_live and selected_media and flux_available and not SKIP_POST_DECRYPT:
            self._session_live_decrypt = True
            logger.info("All selected streams support live decryption — using in-flight decryption.")

        ext_result: dict[str, Any] = {"ext_subs": [], "ext_auds": []}
        spawned_threads: list[threading.Thread] = []

        try:
            bar_ctx = (
                DownloadBarManager(self.download_id) if show_progress else SilentDownloadBarManager(self.download_id)
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

                # Live recordings can legitimately run far longer than join_interruptible's default 2h hard_timeout
                is_live_session = any(getattr(s, "is_live", False) for s in selected_media)
                media_hard_timeout = float("inf") if is_live_session else 7200.0

                if CONCURRENT_DL:
                    ext_thread = threading.Thread(target=_run_externals, daemon=True)
                    spawned_threads.append(ext_thread)
                    ext_thread.start()

                    media_threads: list[threading.Thread] = []
                    for stream in selected_media:
                        t = threading.Thread(target=_run_stream, args=(stream,), daemon=True)
                        media_threads.append(t)
                        spawned_threads.append(t)
                        t.start()

                    join_interruptible(media_threads, self._stop_event, hard_timeout=media_hard_timeout)
                    bar_manager.finish_all_tasks()
                    join_interruptible([ext_thread], self._stop_event, hard_timeout=300.0)

                else:
                    logger.info("Sequential download: video -> audio -> subtitles -> external tracks.")
                    video_streams = [s for s in selected_media if s.type == "video"]
                    audio_streams = [s for s in selected_media if s.type == "audio"]
                    sub_streams = [s for s in selected_media if s.type == "subtitle"]

                    for stream in video_streams:
                        if self._stop_check():
                            break
                        t = threading.Thread(target=lambda s=stream: _run_stream(s), daemon=True)
                        spawned_threads.append(t)
                        t.start()
                        join_interruptible([t], self._stop_event, hard_timeout=media_hard_timeout)

                    for stream in audio_streams:
                        if self._stop_check():
                            break
                        t = threading.Thread(target=lambda s=stream: _run_stream(s), daemon=True)
                        spawned_threads.append(t)
                        t.start()
                        join_interruptible([t], self._stop_event, hard_timeout=media_hard_timeout)

                    for stream in sub_streams:
                        if self._stop_check():
                            break
                        t = threading.Thread(target=lambda s=stream: _run_stream(s), daemon=True)
                        spawned_threads.append(t)
                        t.start()
                        join_interruptible([t], self._stop_event, hard_timeout=media_hard_timeout)

                    bar_manager.finish_all_tasks()

                    if not self._stop_check():
                        ext_thread = threading.Thread(target=_run_externals, daemon=True)
                        spawned_threads.append(ext_thread)
                        ext_thread.start()
                        join_interruptible([ext_thread], self._stop_event, hard_timeout=300.0)

                ext_subs = ext_result["ext_subs"]
                ext_auds = ext_result["ext_auds"]

        except KeyboardInterrupt:
            self._stop_event.set()
            self._cancel_all_loops()
            if self.download_id:
                download_tracker.request_stop(self.download_id)

            console.print("\n[yellow]Stopping — finishing the current segment and merging what was downloaded...")
            logger.info("KeyboardInterrupt: waiting for stream threads to finish merging before returning")

            for t in spawned_threads:
                if t.is_alive():
                    t.join(timeout=30.0)

            ext_subs = ext_result.get("ext_subs", [])
            ext_auds = ext_result.get("ext_auds", [])

        if self._failed_segments:
            self.missing_segments_count += sum(len(failed) for _, failed in self._failed_segments)
            print_failed_segments_report(self._failed_segments)
            self._failed_segments.clear()

        self.status = self._build_status(ext_subs, ext_auds)

        # A stop (Ctrl+C or a tracker-level request, e.g. a live source going
        # offline) can still have produced a fully merged file by the time we
        # get here — only treat it as a cancellation if nothing was produced.
        was_stopped = self._stop_event.is_set() or bool(
            self.download_id and download_tracker.is_stopped(self.download_id)
        )
        if was_stopped and not self.status.get("video") and not self.status.get("audios"):
            return {"error": "cancelled"}

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

    def _run_dl(
        self,
        segs: list[dict],
        out_dir: Path,
        headers: dict,
        progress_cb,
        stream=None,
        event_cb=None,
        default_ext: str = "ts",
    ) -> list[Path]:
        try:
            plan_task_key = self._stream_task_key(stream) if stream else "download"
            if stream and stream.type == "video":
                plan_label = self._video_labels_by_task_key.get(plan_task_key) or self._video_label

            elif stream and stream.type == "audio":
                plan_label = self._audio_labels_by_task_key.get(plan_task_key) or self._audio_labels.get(
                    (stream.language or "und").lower(), ""
                )

            elif stream and stream.type == "subtitle":
                plan_label = self._sub_labels_by_task_key.get(plan_task_key, "")
                if not plan_label:
                    lang_raw = (stream.language or "und").lower()
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

                tasks.append(
                    {
                        "task_key": plan_task_key,
                        "label": plan_label_or_key,
                        "display_label": plan_label_or_key,
                        "url": seg["url"],
                        "path": str(out_dir / f"seg_{seg['number']:05d}.{seg_ext}"),
                        "headers": seg.get("headers", {}),
                    }
                )

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
            use_curl_cffi = config_manager.config.get_bool("DOWNLOAD", "use_curl_cffi_segments")
            backend = run_download_plan_curl_cffi if use_curl_cffi else run_download_plan
            results = backend(plan, progress_cb=progress_cb, event_cb=event_cb, stop_check=self._stop_check)
            return [Path(item["path"]) for item in results if item.get("path")]

        except Exception as exc:
            logger.error(f"_run_dl failed: {exc}", exc_info=True)
            return []

    def _build_headers(self) -> dict:
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

        raw_lang = getattr(stream, "resolved_language", "") or stream.language or "und"
        lang = safe_name(raw_lang.lower())
        if stream.type == "subtitle":
            if getattr(stream, "forced", False):
                lang = f"{lang}_forced"
            elif getattr(stream, "is_sdh", False):
                lang = f"{lang}_sdh"
            elif getattr(stream, "is_cc", False):
                lang = f"{lang}_cc"

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
