# 09.04.26

import asyncio
import logging
import os
import queue
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_proxy_url
from VibraVid.core.ui.tracker import download_tracker
from VibraVid.core.ui.bar_manager import DownloadBarManager, console
from VibraVid.core.decryptor import Decryptor, KeysManager
from VibraVid.core.muxing.helper.video import binary_merge_segments
from VibraVid.core.source.bridge import run_download_plan
from VibraVid.core.source._ism_init import build_ism_init_segment, ISM_TIMESCALE
from VibraVid.core.source._verify_decrypt import verify_decrypted_media
from VibraVid.core.source.download_utils import (
    normalize_path_key,
    format_size   as _fmt_size,
    format_speed  as _fmt_speed,
    estimate_total_size as _estimate_total_size,
    fmt_dur as _fmt_dur,
)
from VibraVid.setup import get_bento4_decrypt_path

from .base import BaseMediaDownloader
from ._hls_utils import hls_base_url, parse_hls_variant_playlist
from ._dash_utils import build_dash_ranged_segments
from ..decryptor._segment_crypto import decrypt_aes128
from ._stream_helpers import (detect_seg_ext, safe_name, describe_key_for_log, join_interruptible, collect_failed_segments, print_failed_segments_report, SilentDownloadBarManager)
from .downloader_live import LiveDownloadMixin


logger = logging.getLogger("manual")
CONCURRENT_DL = config_manager.config.get_bool("DOWNLOAD", "concurrent_download")
THREAD_COUNT = config_manager.config.get_int("DOWNLOAD",  "thread_count")
RETRY_COUNT = config_manager.config.get_int("REQUESTS",  "max_retry")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS",  "timeout")


class MediaDownloader(LiveDownloadMixin, BaseMediaDownloader):
    def __init__(self, url: str, output_dir: str, filename: str, headers: Optional[Dict] = None, key: Optional[Any] = None, cookies: Optional[Dict] = None, download_id: Optional[str] = None, site_name: Optional[str] = None, max_segments: Optional[int] = None) -> None:
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
        self.max_segments = max_segments

        # Cancellation
        self._stop_event: threading.Event = threading.Event()
        self._active_loops: List[asyncio.AbstractEventLoop] = []
        self._loops_lock: threading.Lock = threading.Lock()

        # Live-decryption tracking
        self._session_live_decrypt: bool = False

        # Failed-segment accumulator
        self._failed_segments: list = []
        self._failed_segments_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
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

        if all_support_live and selected_media:
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
                        from VibraVid.core.source.subtitle import download_external_tracks_with_progress
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
                    logger.info("Concurrent download: video + audio in parallel, externals in parallel.")
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
                    logger.info("Sequential download: video → audio → subtitles → external tracks.")
                    video_streams  = [s for s in selected_media if s.type == "video"]
                    audio_streams  = [s for s in selected_media if s.type == "audio"]
                    sub_streams    = [s for s in selected_media if s.type == "subtitle"]

                    for stream in video_streams:
                        if self._stop_check():
                            break
                        t = threading.Thread(target=lambda: _run_stream(stream), daemon=True)
                        t.start()
                        join_interruptible([t], self._stop_event)

                    for stream in audio_streams:
                        if self._stop_check():
                            break
                        t = threading.Thread(target=lambda: _run_stream(stream), daemon=True)
                        t.start()
                        join_interruptible([t], self._stop_event)

                    for stream in sub_streams:
                        if self._stop_check():
                            break
                        t = threading.Thread(target=lambda: _run_stream(stream), daemon=True)
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

    # ------------------------------------------------------------------
    # Loop management
    # ------------------------------------------------------------------
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

    def _stream_task_key(self, stream) -> str:
        if stream.type == "video":
            return self._video_task_key
        if stream.type == "subtitle":
            lang = (stream.resolved_language or stream.language or "und").lower()
            return f"sub_{lang.split('-')[0]}"
        lang = (stream.resolved_language or stream.language or "und").lower()
        return f"aud_{lang.split('-')[0]}"

    def _make_stream_dir(self, stream, protocol: str) -> Path:
        if stream.type == "video":
            name = f"v_{safe_name(stream.resolution or 'unknown')}"
        elif stream.type == "subtitle":
            name = f"s_{safe_name((stream.language or 'und').lower())}"
        else:
            name = f"a_{safe_name((stream.language or 'und').lower())}"
        d = self._tmp_dir / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------------
    # Dispatch per stream type
    # ------------------------------------------------------------------
    def _download_stream(self, stream, bar_manager: DownloadBarManager) -> None:
        effective_live = self._session_live_decrypt

        if self.manifest_type == "HLS":
            if stream.is_live:
                playlist_url = stream.playlist_url
                all_headers = self._build_headers()
                first_content: Optional[str] = None
                base_url: Optional[str] = None
                try:
                    with create_client(headers=all_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                        resp = c.get(playlist_url)
                        resp.raise_for_status()
                        first_content = resp.text
                    base_url = hls_base_url(playlist_url)
                except Exception as exc:
                    logger.error(f"Failed to fetch HLS playlist for live detection: {exc}")
                    return
                self._download_hls_live_stream(stream, bar_manager, live_decryption=effective_live, first_content=first_content, base_url=base_url)
            else:
                self._download_hls_stream(stream, bar_manager, effective_live)

        if self.manifest_type == "DASH":
            if stream.is_live:
                self._download_dash_live_stream(stream, bar_manager, live_decryption=effective_live, mpd_url=self.url, headers=self._build_headers())
            else:
                self._download_dash_stream(stream, bar_manager, effective_live)

        if self.manifest_type == "ISM":
            self._download_ism_stream(stream, bar_manager)

    # ------------------------------------------------------------------
    # HLS stream download
    # ------------------------------------------------------------------
    def _download_hls_stream(self, stream, bar_manager: DownloadBarManager, live_decryption: bool = False) -> None:
        playlist_url = stream.playlist_url
        if not playlist_url:
            logger.error(f"HLS stream has no playlist_url: {stream}")
            return

        all_headers = self._build_headers()
        try:
            with create_client(headers=all_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                resp = c.get(playlist_url)
                resp.raise_for_status()
                playlist_content = resp.text
        except Exception as exc:
            logger.error(f"Failed to fetch HLS variant playlist: {exc}")
            return

        base_url = hls_base_url(playlist_url)
        media_segs, init_url = parse_hls_variant_playlist(playlist_content, base_url)

        if not media_segs and not init_url:
            logger.error(f"HLS variant playlist has no segments: {playlist_url}")
            return

        dl_segs: List[Dict] = []
        if init_url:
            dl_segs.append({"url": init_url, "number": 0, "seg_type": "init", "enc": {"method": "NONE"}})
        offset = len(dl_segs)
        for seg in media_segs:
            dl_segs.append({
                "url":      seg["url"],
                "number":   seg["number"] + offset,
                "seg_type": "media",
                "enc":      seg["enc"],
                "duration": seg.get("duration", 0.0),
            })

        if self.max_segments and self.max_segments > 0:
            dl_segs = dl_segs[:1 + self.max_segments] if init_url else dl_segs[:self.max_segments]
            logger.info(f"Limiting HLS download to {len(dl_segs)} segments (max_segments={self.max_segments})")

        self._download_stream_generic(dl_segs, stream, "hls", "ts", bar_manager, live_decryption=live_decryption)

    # ------------------------------------------------------------------
    # DASH stream download
    # ------------------------------------------------------------------
    def _download_dash_stream(self, stream, bar_manager: DownloadBarManager, live_decryption: bool = False) -> None:
        if not stream.segments:
            logger.error(f"DASH stream has no segments: {stream}")
            return

        all_headers = self._build_headers()
        chunk_size = max(8 * 1024 * 1024, 1 * 1024 * 1024)
        media_segments = [s for s in stream.segments if s.seg_type == "media"]
        is_single_file = len(media_segments) == 1

        dl_segs: List[Dict] = []
        next_num = 0
        for seg in stream.segments:
            if seg.byte_range:
                dl_segs.append({
                    "url":      seg.url,
                    "number":   next_num,
                    "seg_type": seg.seg_type,
                    "enc":      {"method": "NONE"},
                    "headers":  {"Range": f"bytes={seg.byte_range}"},
                })
                next_num += 1
            elif is_single_file and seg.seg_type == "media":
                ranged = build_dash_ranged_segments(seg.url, all_headers, chunk_size, REQUEST_TIMEOUT)
                if ranged:
                    for part in ranged:
                        part["number"]   = next_num
                        part["seg_type"] = seg.seg_type
                        dl_segs.append(part)
                        next_num += 1
                    continue
                else:
                    dl_segs.append({"url": seg.url, "number": next_num, "seg_type": seg.seg_type, "enc": {"method": "NONE"}})
                    next_num += 1
            else:
                dl_segs.append({"url": seg.url, "number": next_num, "seg_type": seg.seg_type, "enc": {"method": "NONE"}})
                next_num += 1

        if not stream.is_live and stream.duration > 0:
            media_segs_count = sum(1 for s in dl_segs if s.get("seg_type") == "media")
            if media_segs_count > 0:
                avg_dur = stream.duration / media_segs_count
                for seg in dl_segs:
                    if seg.get("seg_type") == "media":
                        seg["duration"] = avg_dur

        if self.max_segments and self.max_segments > 0:
            dl_segs = dl_segs[:self.max_segments]
            logger.info(f"Limiting DASH download to {len(dl_segs)} segments (max_segments={self.max_segments})")

        self._download_stream_generic(dl_segs, stream, "dash", "mp4", bar_manager, live_decryption=live_decryption)

    # ------------------------------------------------------------------
    # ISM stream download
    # ------------------------------------------------------------------
    def _download_ism_stream(self, stream, bar_manager: DownloadBarManager) -> None:
        if not stream.segments:
            logger.error(f"ISM stream has no segments: {stream}")
            return

        all_headers = self._build_headers()
        chunk_size = max(8 * 1024 * 1024, 1 * 1024 * 1024)
        media_segments = [s for s in stream.segments if s.seg_type == "media"]
        is_single_file = len(media_segments) == 1

        dl_segs: List[Dict] = []
        next_num = 0

        ism_enc_dict = {"method": "NONE"}
        if stream.drm and stream.drm.is_encrypted():
            ism_enc_dict = {"method": "playready-piff"}
            if hasattr(stream.drm, 'kid') and stream.drm.kid != "N/A":
                ism_enc_dict["kid"] = stream.drm.kid

        for seg in stream.segments:
            if seg.byte_range:
                dl_segs.append({
                    "url":      seg.url,
                    "number":   next_num,
                    "seg_type": seg.seg_type,
                    "enc":      ism_enc_dict,
                    "headers":  {"Range": f"bytes={seg.byte_range}"},
                })
                next_num += 1
            elif is_single_file and seg.seg_type == "media":
                ranged = build_dash_ranged_segments(seg.url, all_headers, chunk_size, REQUEST_TIMEOUT)
                if ranged:
                    for part in ranged:
                        part["number"]   = next_num
                        part["seg_type"] = seg.seg_type
                        part["enc"] = ism_enc_dict
                        dl_segs.append(part)
                        next_num += 1
                    continue
                else:
                    dl_segs.append({"url": seg.url, "number": next_num, "seg_type": seg.seg_type, "enc": ism_enc_dict})
                    next_num += 1
            else:
                dl_segs.append({"url": seg.url, "number": next_num, "seg_type": seg.seg_type, "enc": ism_enc_dict})
                next_num += 1

        if not stream.is_live and stream.duration > 0:
            media_segs_count = sum(1 for s in dl_segs if s.get("seg_type") == "media")
            if media_segs_count > 0:
                avg_dur = stream.duration / media_segs_count
                for seg in dl_segs:
                    if seg.get("seg_type") == "media":
                        seg["duration"] = avg_dur

        if self.max_segments and self.max_segments > 0:
            dl_segs = dl_segs[:self.max_segments]
            logger.info(f"Limiting ISM download to {len(dl_segs)} segments (max_segments={self.max_segments})")

        # Force live_decryption=False → no per-segment decrypt worker
        self._download_stream_generic(dl_segs, stream, "ism", "mp4", bar_manager, live_decryption=False)

    # ------------------------------------------------------------------
    # Helper to locate mp4fragment
    # ------------------------------------------------------------------
    @staticmethod
    def _get_mp4fragment_path() -> Optional[str]:
        decrypt_path = get_bento4_decrypt_path()
        if decrypt_path and os.path.isfile(decrypt_path):
            base_dir = os.path.dirname(decrypt_path)
            for name in ("mp4fragment", "mp4fragment.exe"):
                candidate = os.path.join(base_dir, name)
                if os.path.isfile(candidate):
                    print(f"Found mp4fragment at {candidate} based on Bento4 decrypt path")
                    return candidate
        print(f"mp4fragment: {shutil.which('mp4fragment')}")
        return shutil.which("mp4fragment")

    # ------------------------------------------------------------------
    # ISM init-segment construction
    # (delegated to _ism_init for a correct CENC fragmented MP4 layout)
    # ------------------------------------------------------------------
    @staticmethod
    def _build_ism_init(stream, kid_hex: str) -> bytes:
        """Build a valid ftyp + moov for the encrypted ISM stream."""
        media_segs = [s for s in stream.segments if s.seg_type == "media"]
        seg_count = max(len(media_segs), 1)
        if stream.duration and stream.duration > 0:
            duration = int(stream.duration * ISM_TIMESCALE)
        else:
            # 20s per fragment is the typical ISM segment length
            duration = 20 * ISM_TIMESCALE * seg_count

        codec = (stream.codecs or "").lower() or "hvc1"

        if stream.type == "video":
            return build_ism_init_segment(
                stream_type="video",
                duration=duration,
                codec=codec,
                codec_private=stream.codec_private_data or b"",
                kid_hex=kid_hex,
                width=stream.width or 0,
                height=stream.height or 0,
            )

        if stream.type == "audio":
            # Normalize sample rate and channels to integers (manifests may provide strings)
            sr_val = 0
            try:
                sr_src = getattr(stream, "sample_rate", None)
                if sr_src is not None:
                    sr_val = int(sr_src)
            except Exception:
                try:
                    sr_val = int(float(sr_src))
                except Exception:
                    sr_val = 0

            ch_val = 0
            try:
                ch_src = getattr(stream, "channels", None)
                if ch_src is not None and ch_src != "":
                    ch_val = int(ch_src)
            except Exception:
                try:
                    ch_val = int(float(ch_src))
                except Exception:
                    import re
                    m = re.search(r"(\d+)", str(ch_src or ""))
                    ch_val = int(m.group(1)) if m else 0

            return build_ism_init_segment(
                stream_type="audio",
                duration=duration,
                codec=codec,
                codec_private=stream.codec_private_data or b"",
                kid_hex=kid_hex,
                sample_rate=sr_val,
                channels=ch_val,
                language=getattr(stream, "language", "und") or "und",
            )

        raise ValueError(f"Unsupported ISM stream type: {stream.type!r}")

    # ------------------------------------------------------------------
    # ISM post‑processing (corretta: init + concat + decrypt)
    # ------------------------------------------------------------------
    def _ism_postproc(self, seg_paths: List[Path], out_path: Path, stream, bar_manager, task_key: str, total: int) -> bool:
        audio_codec = (getattr(stream, "codecs", "") or "").lower()
        codec_private_optional = audio_codec in ("ec-3", "eac3", "ac-3", "ac3")
        if not stream.codec_private_data and not codec_private_optional:
            logger.error("CodecPrivateData mancante per lo stream ISM")
            return False

        kid_hex = stream.drm.kid
        if not kid_hex:
            logger.error("KID mancante nello stream ISM")
            return False

        # 1. Generate init segment (ftyp+moov) based on stream metadata + KID (for CENC)
        init_data = self._build_ism_init(stream, kid_hex)

        # 2. Concatenate init + media segments into a single file (in the correct order, skipping invalid segments)
        encrypted_temp = out_path.with_suffix(".enc.mp4")
        try:
            from VibraVid.core.muxing.helper.video import _segment_number
            valid_segs = [(p, _segment_number(p)) for p in seg_paths if p.exists() and p.stat().st_size > 0]
            valid_segs.sort(key=lambda item: item[1])
            if not valid_segs:
                logger.error("Nessun segmento ISM valido trovato per la concatenazione")
                return False
            with open(encrypted_temp, "wb") as out:
                out.write(init_data)
                for seg_path, _ in valid_segs:
                    out.write(seg_path.read_bytes())
        except Exception as exc:
            logger.error(f"Creazione file unificato ISM fallita: {exc}")
            return False
        
        logger.debug(f"File ISM cifrato pronto: {encrypted_temp} ({encrypted_temp.stat().st_size} bytes)")
        decryptor = Decryptor()
        ok = decryptor.decrypt(
            str(encrypted_temp),
            self.key,
            str(out_path),
            stream_type=stream.type,
            progress_cb=bar_manager.handle_progress_line,
        )

        try:
            encrypted_temp.unlink()
        except Exception:
            pass

        if not (ok and out_path.exists() and out_path.stat().st_size > 0):
            logger.error("Decrittazione post-processing ISM fallita")
            return False

        verify_ok, verify_msg = verify_decrypted_media(out_path)
        if not verify_ok:
            logger.error(f"Verifica post-mux fallita per {out_path.name}: {verify_msg}")
            return False
        logger.debug(f"Verifica post-mux OK per {out_path.name}: {verify_msg}")

        bar_manager.handle_progress_line({
            "task_key": task_key,
            "pct": 100,
            "segments": f"{total}/{total}",
            "size": f"{_fmt_size(out_path.stat().st_size)}/{_fmt_size(out_path.stat().st_size)}",
            "speed": "Merge",
        })
        return True


    # ------------------------------------------------------------------
    # Generic stream download (handles ISM hook)
    # ------------------------------------------------------------------
    def _download_stream_generic(self, dl_segs: List[Dict], stream, protocol: str, default_ext: str, bar_manager: DownloadBarManager, live_decryption: bool = False) -> None:
        task_key = self._stream_task_key(stream)
        total = len(dl_segs)
        stream_dir = self._make_stream_dir(stream, protocol)
        all_headers = self._build_headers()
        protocol_lower = protocol.lower()

        key_cache: Dict[str, bytes] = {}
        segment_meta_by_path = {}
        for seg in dl_segs:
            seg_ext = detect_seg_ext(seg.get("url", ""), default=default_ext)
            if seg_ext == "m4s":
                seg_ext = "mp4"
            seg_path = stream_dir / f"seg_{seg['number']:05d}.{seg_ext}"
            segment_meta_by_path[normalize_path_key(str(seg_path))] = seg

        _total_duration: float = sum(s.get("duration", 0.0) for s in dl_segs if s.get("seg_type") != "init")
        _media_segs_only: List[Dict] = [s for s in dl_segs if s.get("seg_type") != "init"]
        _seg_dur_cumulative: List[float] = []
        _acc = 0.0
        for _s in _media_segs_only:
            _acc += _s.get("duration", 0.0)
            _seg_dur_cumulative.append(_acc)

        decrypt_queue: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        decrypt_errors: List[str] = []
        decrypt_thread: Optional[threading.Thread] = None
        probe_lock = threading.Lock()
        probe_done = False

        def _probe_once(target_path: Optional[Path], reason: str) -> None:
            nonlocal probe_done
            if probe_done or not target_path:
                return
            if not target_path.exists() or target_path.stat().st_size <= 0:
                return
            with probe_lock:
                if probe_done:
                    return
                if not target_path.exists() or target_path.stat().st_size <= 0:
                    return
                probe_done = True
            logger.debug(f"{protocol.upper()} probe starting -> {target_path.name} ({reason})")
            self._probe_media_file(target_path)

        def _replace_segment_file(source_path: Path, target_path: Path, reason: str) -> None:
            last_exc: Optional[Exception] = None
            for attempt in range(1, 9):
                try:
                    if target_path.exists():
                        try:
                            target_path.unlink()
                        except Exception:
                            pass
                    source_path.replace(target_path)
                    return
                except OSError as exc:
                    last_exc = exc
                    if attempt >= 8:
                        raise
                    if getattr(exc, "winerror", None) not in (5, 32) and not isinstance(exc, PermissionError):
                        raise
                    logger.debug(f"{reason} replace retry {attempt}/8 for {source_path.name} -> {target_path.name}: {exc}")
                    time.sleep(0.05 * attempt)
            if last_exc:
                raise last_exc

        def _progress(done: int, total_: int, total_bytes: int, speed_bps: float, speed_label: Optional[str] = None) -> None:
            pct = int((done / total_) * 100) if total_ else 0
            estimated_total = _estimate_total_size(total_bytes, done, total_) if done > 0 else total_bytes
            size_display = (f"{_fmt_size(total_bytes)}/{_fmt_size(estimated_total)}" if done < total_ else f"{_fmt_size(total_bytes)}/{_fmt_size(total_bytes)}")
            duration_display = ""
            if _total_duration > 0:
                media_done = max(0, done - (1 if any(s.get("seg_type") == "init" for s in dl_segs) else 0))
                elapsed_dur = _seg_dur_cumulative[media_done - 1] if media_done > 0 and media_done <= len(_seg_dur_cumulative) else 0.0
                duration_display = f"{_fmt_dur(elapsed_dur)}/{_fmt_dur(_total_duration)}"
            bar_manager.handle_progress_line({
                "task_key": task_key,
                "pct":      pct,
                "segments": f"{done}/{total_}",
                "size":     size_display,
                "speed":    speed_label if speed_label is not None else _fmt_speed(speed_bps),
                "duration": duration_display,
            })

        def _decrypt_hls_segment(fp: Path, seg: Dict[str, Any]) -> None:
            enc = seg.get("enc") or {}
            method = str(enc.get("method") or "NONE").upper()
            if method != "AES-128":
                return
            key_url = enc.get("key_url")
            if not key_url:
                raise RuntimeError(f"Missing AES-128 key URL for {fp.name}")
            key_data = key_cache.get(key_url)
            if key_data is None:
                with create_client(headers=all_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                    r = c.get(key_url)
                    r.raise_for_status()
                    key_data = r.content
                if len(key_data) != 16:
                    logger.warning(f"HLS AES-128 key length is {len(key_data)} bytes for {key_url}")
                key_cache[key_url] = key_data
            logger.debug(f"AES-128 LIVE decrypt path={fp} with key={describe_key_for_log(key_data)}")
            decrypted = decrypt_aes128(fp.read_bytes(), key_data, enc.get("iv"), int(seg.get("number", 0) or 0))
            tmp_path = fp.with_suffix(fp.suffix + ".dec")
            tmp_path.write_bytes(decrypted)
            _replace_segment_file(tmp_path, fp, "HLS AES-128")
            logger.debug(f"HLS AES-128 decrypted -> {fp.name}")
            _probe_once(fp, "hls-first-decrypted-segment")

        def _decrypt_dash_segment(fp: Path, seg: Dict[str, Any], dash_decryptor: Decryptor, init_path: Optional[Path]) -> None:
            if seg.get("seg_type") == "init":
                logger.info(f"DASH init segment ready -> {fp.name}")
                return
            dec_tmp = fp.with_suffix(fp.suffix + ".dec")
            logger.debug(f'CENC LIVE decrypt path={fp} init={init_path if init_path and init_path.exists() else "None"} with key={describe_key_for_log(self.key)}')
            ok, message, _data = dash_decryptor.decrypt_segment_live(
                encrypted_path=str(fp), decrypted_path=str(dec_tmp), raw_keys=self.key,
                init_path=str(init_path) if init_path and init_path.exists() else None,
            )
            if not ok:
                raise RuntimeError(f"DASH live decrypt failed for {fp.name}: {message}")
            if not dec_tmp.exists():
                raise RuntimeError(f"DASH live decrypt produced no output for {fp.name}")
            _replace_segment_file(dec_tmp, fp, "DASH live")
            logger.debug(f"DASH live decrypted -> {fp.name}")
            _probe_once(fp, "dash-first-decrypted-segment")

        def _decrypt_worker() -> None:
            dash_decryptor = Decryptor() if protocol_lower == "dash" and live_decryption and self.key else None
            dash_init_path: Optional[Path] = None
            pending_dash: List[Tuple[Path, Dict[str, Any]]] = []
            while True:
                item = decrypt_queue.get()
                if item is None:
                    break
                try:
                    if item.get("skipped"):
                        continue
                    path_value = item.get("path")
                    if not path_value:
                        continue
                    fp = Path(path_value)
                    if not fp.exists() or fp.stat().st_size <= 0:
                        continue
                    seg = segment_meta_by_path.get(normalize_path_key(str(fp)))
                    if not seg:
                        logger.debug(f"Segment completion without metadata match: {fp}")
                        continue

                    if protocol_lower == "hls":
                        _decrypt_hls_segment(fp, seg)
                        continue
                    if protocol_lower == "dash" and live_decryption and self.key:
                        if seg.get("seg_type") == "init":
                            dash_init_path = fp
                            logger.info(f"DASH init segment cached -> {fp.name}")
                            _probe_once(fp, "dash-init-segment")
                            queued = pending_dash[:]
                            pending_dash.clear()
                            for pending_fp, pending_seg in queued:
                                _decrypt_dash_segment(pending_fp, pending_seg, dash_decryptor, dash_init_path)
                        else:
                            if dash_init_path is None:
                                pending_dash.append((fp, seg))
                            else:
                                _decrypt_dash_segment(fp, seg, dash_decryptor, dash_init_path)
                except Exception as exc:
                    decrypt_errors.append(str(exc))
                    logger.error(f"Segment decrypt error ({protocol_lower}/{task_key}): {exc}")
                    decrypt_queue.task_done()

        needs_hls_decrypt = protocol_lower == "hls" and any(str((seg.get("enc") or {}).get("method") or "NONE").upper() == "AES-128" for seg in dl_segs)
        needs_dash_live = protocol_lower == "dash" and live_decryption and bool(self.key)

        if needs_hls_decrypt or needs_dash_live:
            logger.debug(f'{protocol.upper()} decrypt worker started ({"AES-128" if needs_hls_decrypt else "live DASH"})')
            decrypt_thread = threading.Thread(target=_decrypt_worker, daemon=True)
            decrypt_thread.start()

        def _handle_download_event(event: Dict[str, Any]) -> None:
            event_name = (event.get("event") or "").lower()
            if event_name in {"start", "summary", "retry", "error", "cancelled"}:
                return
            path_value = event.get("path")
            if not path_value:
                return
            if event.get("skipped"):
                return
            seg = segment_meta_by_path.get(normalize_path_key(str(path_value)))
            should_probe_now = (
                seg
                and seg.get("seg_type") == "media"
                and not decrypt_thread
                and not ((not live_decryption) and bool(self.key))
            )
            if should_probe_now:
                _probe_once(Path(path_value), f"{protocol.upper()}-first-media-segment")
            if decrypt_thread:
                decrypt_queue.put(dict(event))

        paths = self._run_dl(dl_segs, stream_dir, all_headers, _progress, stream=stream, event_cb=_handle_download_event, default_ext=default_ext)

        if decrypt_thread:
            decrypt_queue.put(None)
            decrypt_thread.join()

        if paths is not None:
            _stream_label_rich = (
                self._video_label if stream.type == "video"
                else self._audio_labels.get((stream.language or "und").lower(), stream.language or "und")
                if stream.type == "audio"
                else stream.language or "und"
            )
            
            _plain_label = re.sub(r"\[/?[^\[\]]*\]", "", _stream_label_rich).strip() or task_key
            failed = collect_failed_segments(dl_segs, paths, stream_dir, default_ext)
            if failed:
                with self._failed_segments_lock:
                    self._failed_segments.append((_plain_label, failed))

        if decrypt_errors:
            raise RuntimeError(decrypt_errors[0])

        if self._stop_check() or not paths:
            return

        sample_url = dl_segs[0]["url"] if dl_segs else ""
        ext = detect_seg_ext(sample_url, default=default_ext)
        if ext == "m4s":
            ext = "mp4"
        out_path = self.output_dir / self._out_filename(stream, ext)

        # ----- ISM POST‑PROCESSING -----
        if protocol_lower == "ism" and self.key:
            logger.info(f"ISM post‑processing {len(paths)} segmenti -> {out_path}")
            success = self._ism_postproc(paths, out_path, stream, bar_manager, task_key, total)
            if not success:
                logger.error("ISM post‑processing failed")
            return

        # Standard merge for HLS/DASH
        merge_total_size = sum(p.stat().st_size for p in paths if p.exists())
        logger.debug(f"{protocol.upper()} binary merge starting -> {out_path.name} ({len(paths)} segs, {_fmt_size(merge_total_size)})")
        bar_manager.handle_progress_line({
            "task_key": task_key,
            "pct": 100,
            "segments": f"{total}/{total}",
            "size": f"{_fmt_size(merge_total_size)}/{_fmt_size(merge_total_size)}",
            "speed": "Merge",
        })
        binary_merge_segments(paths, out_path, merge_logger=logger)
        logger.info(f"{protocol.upper()} binary merge completed -> {out_path.name}")

        # Post-merge decryption for HLS/DASH (if needed)
        is_plain_subtitle = (
            stream is not None
            and getattr(stream, "type", "") == "subtitle"
            and not getattr(stream, "is_wvtt_mp4", False)
        )

        stream_is_encrypted = stream.drm.method is not None
        if (not live_decryption) and self.key and stream_is_encrypted and out_path.exists() and out_path.stat().st_size > 0 and not is_plain_subtitle:
            post_merge_path = out_path.with_suffix(out_path.suffix + ".dec")
            try:
                decryptor = Decryptor()
                if decryptor.decrypt(str(out_path), self.key, str(post_merge_path), stream_type=stream.type, progress_cb=bar_manager.handle_progress_line):
                    try:
                        out_path.unlink(missing_ok=True)
                        post_merge_path.rename(out_path)
                    except Exception as exc:
                        logger.error(f"rename failed: {exc}")
                        if post_merge_path.exists():
                            try:
                                post_merge_path.unlink()
                            except Exception:
                                pass
                else:
                    logger.warning(f"Post-merge decryption failed for {out_path.name}")
                    if post_merge_path.exists():
                        try:
                            post_merge_path.unlink()
                        except Exception:
                            pass
            except Exception as exc:
                logger.error(f"Post-merge decryption error: {exc}")

        if out_path.exists() and out_path.stat().st_size > 0:
            logger.debug(f"{protocol.upper()} merged {len(paths):>4} segs -> {out_path.name} ({out_path.stat().st_size // 1024} KB)")
            _progress(total, total, out_path.stat().st_size, 0.0, speed_label="Merge")
        else:
            logger.error(f"{protocol.upper()} binary merge produced empty file: {out_path}")

    # ------------------------------------------------------------------
    # Velora download dispatch
    # ------------------------------------------------------------------
    def _run_dl(self, segs: List[Dict], out_dir: Path, headers: Dict, progress_cb, stream=None, event_cb=None, default_ext: str = "ts") -> List[Path]:
        try:
            plan_task_key = self._stream_task_key(stream) if stream else "download"
            if stream and stream.type == "video":
                plan_label = self._video_label
            elif stream and stream.type == "audio":
                plan_label = self._audio_labels.get((stream.language or "und").lower(), "")
            elif stream and stream.type == "subtitle":
                lang_raw  = (stream.language or "und").lower()
                task_lang = lang_raw.split("-")[0]
                plan_label = (self._sub_labels.get(lang_raw) or self._sub_labels.get(task_lang) or "")
            else:
                plan_label = ""

            logger.debug(f"Starting download plan for {plan_task_key} with {len(segs)} segments")
            plan = {
                "project": "Velora",
                "version": 1,
                "task_key": plan_task_key,
                "label": plan_label or plan_task_key,
                "display_label": plan_label or plan_task_key,
                "concurrency": THREAD_COUNT,
                "retry_count": RETRY_COUNT,
                "timeout_seconds": REQUEST_TIMEOUT,
                "retry_base_delay_seconds": 1.0,
                "retry_max_delay_seconds": 4.0,
                "retry_jitter_seconds": 0.25,
                "proxy_url": get_proxy_url(),
                "headers": headers,
                "tasks": [
                    {
                        "task_key": plan_task_key,
                        "label": plan_label or plan_task_key,
                        "display_label": plan_label or plan_task_key,
                        "url": seg["url"],
                        "path": (lambda _s=seg: str(out_dir / f"seg_{_s['number']:05d}.{('mp4' if detect_seg_ext(_s.get('url',''), default=default_ext) == 'm4s' else detect_seg_ext(_s.get('url',''), default=default_ext))}"))(),
                        "headers": seg.get("headers", {}),
                    }
                    for seg in segs
                ],
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
            try:
                from VibraVid.setup import get_ffprobe_path
                from VibraVid.core.muxing.util.info import Mediainfo
                ffprobe_path = get_ffprobe_path()
                asyncio.run(Mediainfo.from_file_async(ffprobe_path, str(target_path)))
            except Exception as exc:
                logger.warning(f"[PROBE] Could not probe media file: {exc}")
        except Exception as exc:
            logger.error(f"[PROBE] Error: {exc}")

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
                return f"{self.filename}.{lang}.wvtt"
            sub_ext = (stream.format or ext or "vtt").lower().strip()
            if sub_ext in ("dash", "mp4", "m4s", ""):
                sub_ext = "vtt"
            return f"{self.filename}.{lang}.{sub_ext}"
        audio_ext = "webm" if ext == "webm" else "m4a"
        return f"{self.filename}.{lang}.{audio_ext}"