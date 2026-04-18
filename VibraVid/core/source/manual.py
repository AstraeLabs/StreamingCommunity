# 09.04.26

import re
import time
import asyncio
import logging
import queue
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_proxy_url
from VibraVid.core.ui.tracker import download_tracker
from VibraVid.core.ui.bar_manager import DownloadBarManager, console
from VibraVid.core.utils.decrypt_engine import Decryptor, KeysManager
from VibraVid.core.muxing.helper.video import binary_merge_segments
from VibraVid.core.source.c_bridge import run_download_plan
from VibraVid.core.source.download_utils import (
    normalize_path_key,
    format_size as _fmt_size,
    format_speed as _fmt_speed,
    estimate_total_size as _estimate_total_size,
)

from .base import BaseMediaDownloader

try:
    from Cryptodome.Cipher import AES as _AES
    from Cryptodome.Util.Padding import unpad as _unpad
    _HAS_AES = True
except ImportError:
    _HAS_AES = False

logger = logging.getLogger("manual")
CONCURRENT_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "concurrent_download")
THREAD_COUNT = config_manager.config.get_int("DOWNLOAD", "thread_count")
RETRY_COUNT = config_manager.config.get_int("REQUESTS", "max_retry")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")
USE_PROXY = config_manager.config.get_bool("REQUESTS", "use_proxy")
PROXY_CFG = config_manager.config.get_dict("REQUESTS", "proxy")


class _SilentDownloadBarManager(DownloadBarManager):
    def __init__(self, download_id: Optional[str] = None):
        # Do NOT call super().__init__() here because DownloadBarManager sets up
        # Rich Live/Progress objects we deliberately want to skip.
        self.download_id = download_id
        self.progress = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def add_prebuilt_tasks(self, prebuilt_tasks):           
        return None
    def add_external_track_tasks(self, *args, **kwargs):   
        return None
    def add_external_track_task(self, label, track_key):   
        return None
    def get_task_id(self, task_key):                       
        return None
    def handle_progress_line(self, parsed):                
        return None
    def finish_all_tasks(self):                            
        return None

def _hls_base_url(playlist_url: str) -> str:
    p = urlparse(playlist_url)
    path = p.path.rsplit("/", 1)[0]
    return f"{p.scheme}://{p.netloc}{path}/"


def _detect_seg_ext(url: str, default: str = "ts") -> str:
    path = url.split("?")[0].lower()
    for ext in ("mp4", "m4s", "m4v", "m4a", "ts", "aac", "webm", "vtt", "srt"):
        if path.endswith(f".{ext}"):
            return ext
    return default


def _safe(s: str, maxlen: int = 32) -> str:
    """Strip characters unsafe for directory names and cap length."""
    cleaned = re.sub(r"[^\w\-]", "_", s or "").strip("_")
    return (cleaned or "x")[:maxlen]


def _describe_key_for_log(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, KeysManager):
        try:
            return f"KeysManager(len={len(value.get_keys_list())})"
        except Exception:
            return "KeysManager"
    if isinstance(value, str):
        return f"str(len={len(value)})"
    if isinstance(value, (bytes, bytearray)):
        return f"{type(value).__name__}(len={len(value)})"
    if isinstance(value, (list, tuple, set)):
        return f"{type(value).__name__}(len={len(value)})"
    return type(value).__name__


def _parse_hls_variant_playlist(content: str, base_url: str) -> Tuple[List[Dict], Optional[str]]:
    """Parse an HLS *variant* (media) playlist."""
    segments: List[Dict] = []
    current_enc: Dict = {"method": "NONE", "key_url": None, "iv": None}
    init_url: Optional[str] = None
    seg_num = 0
    map_count = 0

    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("#EXT-X-KEY:"):
            method_m = re.search(r"METHOD=([^,\s\"]+)", line)
            uri_m = re.search(r'URI="([^"]+)"', line)
            iv_m = re.search(r"IV=0x([0-9a-fA-F]+)", line, re.I)
            current_enc = {
                "method": (method_m.group(1).upper() if method_m else "NONE"),
                "key_url": (urljoin(base_url, uri_m.group(1)) if uri_m else None),
                "iv": (iv_m.group(1).lower().zfill(32) if iv_m else None),
            }

        elif line.startswith("#EXT-X-MAP:"):
            map_count += 1
            # Legacy HLS behavior: stop when a second MAP appears to avoid mixing init/segments from different timeline blocks.
            if map_count > 1:
                break
            uri_m = re.search(r'URI="([^"]+)"', line)
            if uri_m:
                init_url = urljoin(base_url, uri_m.group(1))

        elif line.startswith("#EXTINF:"):
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].strip().startswith("#")):
                i += 1
            if i < len(lines):
                seg_url = lines[i].strip()
                if seg_url and not seg_url.startswith("#"):
                    segments.append(
                        {
                            "url": urljoin(base_url, seg_url),
                            "number": seg_num,
                            "enc": dict(current_enc),
                        }
                    )
                    seg_num += 1
            i += 1
            continue

        i += 1

    return segments, init_url


def _decrypt_aes128(data: bytes, key_data: bytes, iv_hex: Optional[str], seg_num: int) -> bytes:
    """Decrypt one AES-128-CBC HLS segment in-process."""
    if not _HAS_AES:
        raise RuntimeError("PyCryptodome required for AES-128 decryption.\nInstall:  pip install pycryptodome")
    iv_bytes = bytes.fromhex(iv_hex) if iv_hex else seg_num.to_bytes(16, "big")
    cipher = _AES.new(key_data, _AES.MODE_CBC, iv_bytes)
    try:
        return _unpad(cipher.decrypt(data), _AES.block_size)
    except Exception:
        return cipher.decrypt(data)


def _split_http_ranges(total_size: int, chunk_size: int) -> List[Tuple[int, int]]:
    ranges: List[Tuple[int, int]] = []
    start = 0
    while start < total_size:
        end = min(start + chunk_size - 1, total_size - 1)
        ranges.append((start, end))
        start = end + 1
    return ranges


def _join_interruptible(
    threads: List[threading.Thread],
    stop_event: threading.Event,
    poll: float = 0.25,
    hard_timeout: float = 7200.0,
) -> None:
    """Join *threads* in a tight polling loop so KeyboardInterrupt is always deliverable."""
    deadline = time.monotonic() + hard_timeout
    while True:
        alive = [t for t in threads if t.is_alive()]
        if not alive:
            break
        if stop_event.is_set() or time.monotonic() >= deadline:
            break
        for t in alive:
            t.join(timeout=poll)


class MediaDownloader(BaseMediaDownloader):
    def __init__(
        self,
        url: str,
        output_dir: str,
        filename: str,
        headers: Optional[Dict] = None,
        key: Optional[Any] = None,
        cookies: Optional[Dict] = None,
        download_id: Optional[str] = None,
        site_name: Optional[str] = None,
        max_segments: Optional[int] = None,
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
        self.max_segments = max_segments

        # Cancellation
        self._stop_event: threading.Event = threading.Event()
        self._active_loops: List[asyncio.AbstractEventLoop] = []
        self._loops_lock: threading.Lock = threading.Lock()

        # Live decryption tracking
        self._session_live_decrypt: bool = False

    def start_download(self, show_progress: bool = True) -> Dict[str, Any]:
        """Download all selected streams, decrypt if needed, return status dict."""
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")

        self._promote_hls_subtitles_to_external()
        self._prepare_labels()

        selected_media = [
            s for s in self.streams
            if s.selected and not s.is_external and s.type in ("video", "audio")
        ]
        all_support_live = (
            all(s.supports_live_decryption for s in selected_media)
            if selected_media
            else False
        )

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
                    console.print("[red]Warning:[/red] SAMPLE-AES/CBCS streams detected but no keys provided for decryption.")
                    logger.error("No keys provided for post-download decryption — merged file will remain encrypted.")
            else:
                logger.info("Using post-download decryption.")

        ext_result: Dict[str, Any] = {"ext_subs": [], "ext_auds": []}

        try:
            bar_manager_ctx = (
                DownloadBarManager(self.download_id)
                if show_progress
                else _SilentDownloadBarManager(self.download_id)
            )
            with bar_manager_ctx as bar_manager:
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
                                stop_check=self._stop_check,  # Pass stop_check to allow interruption
                            )
                        )
                        ext_result["ext_subs"] = subs
                        ext_result["ext_auds"] = auds
                    except Exception as exc:
                        logger.error(f"External downloads failed: {exc}")
                    finally:
                        self._unregister_loop(ext_loop)
                        ext_loop.close()

                ext_thread = threading.Thread(target=_run_externals, daemon=True)
                ext_thread.start()

                media_threads: List[threading.Thread] = []
                for stream in selected_media:
                    def _run_stream(s=stream) -> None:
                        try:
                            self._download_stream(s, bar_manager)
                        except Exception as exc:
                            logger.error(f"Stream download error ({s.type}/{s.language}): {exc}", exc_info=True)

                    t = threading.Thread(target=_run_stream, daemon=True)
                    media_threads.append(t)
                    t.start()

                _join_interruptible(media_threads, self._stop_event)
                bar_manager.finish_all_tasks()
                _join_interruptible([ext_thread], self._stop_event, hard_timeout=300.0)

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
        return self._stop_event.is_set() or bool(
            self.download_id and download_tracker.is_stopped(self.download_id)
        )

    def _stream_task_key(self, stream) -> str:
        if stream.type == "video":
            return self._video_task_key
        lang = (stream.resolved_language or stream.language or "und").lower()
        return f"aud_{lang.split('-')[0]}"

    def _make_stream_dir(self, stream, protocol: str) -> Path:
        # !!!!!!!!!!!!!! CAN CREATE PROBLEM FOR WIN FILESYSTEM IF EXCEED 260 CHARACTERS !!!!!!!!!!!!!!
        if stream.type == "video":
            res = _safe(stream.resolution or "unknown")
            name = f"v_{res}"
        else:
            lang = _safe((stream.language or "und").lower())
            name = f"a_{lang}"

        d = self._tmp_dir / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _download_stream(self, stream, bar_manager: DownloadBarManager) -> None:
        effective_live = self._session_live_decrypt
        if self.manifest_type == "HLS":
            self._download_hls_stream(stream, bar_manager, effective_live)
        else:
            self._download_dash_stream(stream, bar_manager, effective_live)

    def _probe_media_file(self, target_path: Path) -> None:
        """Run ffprobe on a media file to extract metadata for progress estimation."""
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

    def _download_stream_generic(self, dl_segs: List[Dict], stream, protocol: str, default_ext: str, bar_manager: DownloadBarManager, live_decryption: bool = False) -> None:
        task_key = self._stream_task_key(stream)
        total = len(dl_segs)
        stream_dir = self._make_stream_dir(stream, protocol)
        all_headers = self._build_headers()
        key_cache: Dict[str, bytes] = {}
        protocol_lower = protocol.lower()
        segment_meta_by_path = {
            normalize_path_key(str(stream_dir / f"seg_{seg['number']:08d}.bin")): seg
            for seg in dl_segs
        }
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
            logger.info("%s probe starting -> %s (%s)", protocol.upper(), target_path.name, reason)
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
                    logger.debug("%s replace retry %s/8 for %s -> %s: %s", reason, attempt, source_path.name, target_path.name, exc)
                    time.sleep(0.05 * attempt)

            if last_exc:
                raise last_exc

        def _progress(done: int, total_: int, total_bytes: int, speed_bps: float, speed_label: Optional[str] = None) -> None:
            pct = int((done / total_) * 100) if total_ else 0
            estimated_total = (
                _estimate_total_size(total_bytes, done, total_) if done > 0 else total_bytes
            )
            size_display = (
                f"{_fmt_size(total_bytes)}/{_fmt_size(estimated_total)}"
                if done < total_
                else f"{_fmt_size(total_bytes)}/{_fmt_size(total_bytes)}"
            )
            bar_manager.handle_progress_line(
                {
                    "task_key": task_key,
                    "pct": pct,
                    "segments": f"{done}/{total_}",
                    "size": size_display,
                    "speed": speed_label if speed_label is not None else _fmt_speed(speed_bps),
                }
            )

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
                    logger.warning("HLS AES-128 key length is %s bytes for %s", len(key_data), key_url)
                key_cache[key_url] = key_data

            logger.debug("AES-128 LIVE decrypt path=%s with key=%s", fp, _describe_key_for_log(key_data))
            decrypted = _decrypt_aes128(
                fp.read_bytes(),
                key_data,
                enc.get("iv"),
                int(seg.get("number", 0) or 0),
            )
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
            logger.debug("CENC LIVE decrypt path=%s init=%s with key=%s", fp, init_path if init_path and init_path.exists() else "None", _describe_key_for_log(self.key))
            ok, message, _data = dash_decryptor.decrypt_segment_live(
                encrypted_path=str(fp),
                decrypted_path=str(dec_tmp),
                raw_keys=self.key,
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
            dash_decryptor = (Decryptor() if protocol_lower == "dash" and live_decryption and self.key else None)
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
                        logger.debug("Segment completion without metadata match: %s", fp)
                        continue

                    if protocol_lower == "hls":
                        _decrypt_hls_segment(fp, seg)
                        continue

                    if protocol_lower == "dash" and live_decryption and self.key and dash_decryptor:
                        if seg.get("seg_type") == "init":
                            dash_init_path = fp
                            logger.info(f"DASH init segment cached -> {fp.name}")
                            _probe_once(fp, "dash-init-segment")
                            if pending_dash:
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
                finally:
                    decrypt_queue.task_done()

            if (
                protocol_lower == "dash"
                and live_decryption
                and self.key
                and pending_dash
                and not dash_init_path
            ):
                decrypt_errors.append("DASH live decryption never received an init segment")

        needs_hls_decrypt = protocol_lower == "hls" and any(
            str((seg.get("enc") or {}).get("method") or "NONE").upper() == "AES-128"
            for seg in dl_segs
        )
        needs_dash_live_decrypt = (
            protocol_lower == "dash" and live_decryption and bool(self.key)
        )

        if needs_hls_decrypt or needs_dash_live_decrypt:
            logger.info(
                "%s decrypt worker started (%s)",
                protocol.upper(),
                "AES-128" if needs_hls_decrypt else "live DASH",
            )
            decrypt_thread = threading.Thread(target=_decrypt_worker, daemon=True)
            decrypt_thread.start()

        def _handle_download_event(event: Dict[str, Any]) -> None:
            if (event.get("event") or "").lower() != "completed":
                return

            path_value = event.get("path")
            if path_value:
                seg = segment_meta_by_path.get(normalize_path_key(str(path_value)))
                
                # Probe first media segment for unencrypted streams (after download, before decrypt)
                if seg and seg.get("seg_type") == "media" and not decrypt_thread:
                    _probe_once(Path(path_value), f"{protocol.upper()}-first-media-segment")
                
                # DASH init segment tracking
                if not decrypt_thread and protocol_lower == "dash":
                    if seg and seg.get("seg_type") == "init":
                        pass

            if decrypt_thread:
                decrypt_queue.put(dict(event))

        try:
            paths = self._run_dl(
                dl_segs,
                stream_dir,
                all_headers,
                _progress,
                stream=stream,
                event_cb=_handle_download_event,
            )
        finally:
            if decrypt_thread:
                decrypt_queue.put(None)
                decrypt_thread.join()

        if decrypt_errors:
            raise RuntimeError(decrypt_errors[0])

        if self._stop_check() or not paths:
            return

        sample_url = dl_segs[0]["url"] if dl_segs else ""
        ext = _detect_seg_ext(sample_url, default=default_ext)
        if ext == "m4s":
            ext = "mp4"

        out_path = self.output_dir / self._out_filename(stream, ext)
        merge_total_size = sum(p.stat().st_size for p in paths if p.exists())
        logger.info(f"{protocol.upper()} binary merge starting -> {out_path.name} ({len(paths)} segs, {_fmt_size(merge_total_size)})")
        bar_manager.handle_progress_line(
            {
                "task_key": task_key,
                "pct": 100,
                "segments": f"{total}/{total}",
                "size": (
                    f"{_fmt_size(merge_total_size)}/{_fmt_size(merge_total_size)}"
                    if merge_total_size
                    else "0B/0B"
                ),
                "speed": "Merge",
            }
        )
        binary_merge_segments(paths, out_path, merge_logger=logger)
        logger.info(f"{protocol.upper()} binary merge completed -> {out_path.name}")

        # Post-merge decryption (non-live path only).
        if (not live_decryption) and self.key and out_path.exists() and out_path.stat().st_size > 0:
            post_merge_renamed = False
            post_merge_path = out_path.with_suffix(out_path.suffix + ".dec")
            try:
                logger.info("Binary merge v1 ...")
                decryptor = Decryptor()
                if decryptor.decrypt(
                    str(out_path),
                    self.key,
                    str(post_merge_path),
                    stream_type=stream.type,
                    progress_cb=bar_manager.handle_progress_line,
                ):
                    try:
                        out_path.unlink(missing_ok=True)
                        post_merge_path.rename(out_path)
                        post_merge_renamed = True
                        logger.info(f"{protocol.upper()} post-merge decrypt normalized -> {out_path.name}")
                    except Exception as exc:
                        logger.error(f"{protocol.upper()} post-merge rename failed: {exc}")
                        if post_merge_path.exists():
                            try:
                                post_merge_path.unlink()
                            except Exception:
                                pass
                else:
                    logger.warning(f"{protocol.upper()} post-merge decrypt normalization failed for {out_path.name}")
                    if post_merge_path.exists():
                        try:
                            post_merge_path.unlink()
                        except Exception:
                            pass
            except Exception as exc:
                logger.error(f"{protocol.upper()} post-merge decrypt normalization error: {exc}")
                if not post_merge_renamed and post_merge_path.exists():
                    try:
                        post_merge_path.unlink()
                    except Exception:
                        pass

        if out_path.exists() and out_path.stat().st_size > 0:
            logger.info(f"{protocol.upper()} merged {len(paths):>4} segs -> {out_path.name}  ({out_path.stat().st_size // 1024} KB)")
            _progress(total, total, out_path.stat().st_size, 0.0, speed_label="Merge")
        else:
            logger.error(f"{protocol.upper()} binary merge produced empty file: {out_path}")

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

        base_url = _hls_base_url(playlist_url)
        media_segs, init_url = _parse_hls_variant_playlist(playlist_content, base_url)

        if not media_segs and not init_url:
            logger.error(f"HLS variant playlist has no segments: {playlist_url}")
            return

        dl_segs: List[Dict] = []
        if init_url:
            dl_segs.append({"url": init_url, "number": 0, "seg_type": "init", "enc": {"method": "NONE"}})
        offset = len(dl_segs)
        for seg in media_segs:
            dl_segs.append(
                {
                    "url": seg["url"],
                    "number": seg["number"] + offset,
                    "seg_type": "media",
                    "enc": seg["enc"],
                }
            )

        if self.max_segments is not None and self.max_segments > 0:
            if init_url:
                dl_segs = dl_segs[: 1 + self.max_segments]
            else:
                dl_segs = dl_segs[: self.max_segments]
            logger.info(f"Limiting HLS download to {len(dl_segs)} segments (max_segments={self.max_segments})")

        self._download_stream_generic(
            dl_segs, stream, "hls", "ts", bar_manager, live_decryption=live_decryption
        )

    def _download_dash_stream(self, stream, bar_manager: DownloadBarManager, live_decryption: bool = False) -> None:
        if not stream.segments:
            logger.error(f"DASH stream has no segments: {stream}")
            return

        all_headers = self._build_headers()
        chunk_size = max(8 * 1024 * 1024, 1 * 1024 * 1024)

        media_segments = [s for s in stream.segments if s.seg_type == "media"]
        is_single_file_media = len(media_segments) == 1

        dl_segs: List[Dict] = []
        next_num = 0
        for seg in stream.segments:
            if seg.byte_range:
                dl_segs.append(
                    {
                        "url": seg.url,
                        "number": next_num,
                        "seg_type": seg.seg_type,
                        "enc": {"method": "NONE"},
                        "headers": {"Range": f"bytes={seg.byte_range}"},
                    }
                )
                next_num += 1

            elif is_single_file_media and seg.seg_type == "media":
                ranged = self._build_dash_ranged_segments(seg.url, all_headers, chunk_size)
                if ranged:
                    for part in ranged:
                        part["number"] = next_num
                        part["seg_type"] = seg.seg_type
                        dl_segs.append(part)
                        next_num += 1
                    continue
                else:
                    dl_segs.append(
                        {
                            "url": seg.url,
                            "number": next_num,
                            "seg_type": seg.seg_type,
                            "enc": {"method": "NONE"},
                        }
                    )
                    next_num += 1
            else:
                dl_segs.append(
                    {
                        "url": seg.url,
                        "number": next_num,
                        "seg_type": seg.seg_type,
                        "enc": {"method": "NONE"},
                    }
                )
                next_num += 1

        if self.max_segments is not None and self.max_segments > 0:
            dl_segs = dl_segs[: self.max_segments]
            logger.info(f"Limiting DASH download to {len(dl_segs)} segments (max_segments={self.max_segments})")

        self._download_stream_generic(dl_segs, stream, "dash", "mp4", bar_manager, live_decryption=live_decryption)

    def _build_dash_ranged_segments(self, media_url: str, headers: Dict, chunk_size: int) -> List[Dict]:
        """Return synthetic DASH chunk segments using HTTP Range, when supported."""
        try:
            with create_client(headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                r = c.head(media_url)
                r.raise_for_status()

            content_len = int((r.headers.get("content-length") or "0").strip() or "0")
            accept_ranges = (r.headers.get("accept-ranges") or "").lower()

            if content_len <= chunk_size or "bytes" not in accept_ranges:
                return []

            ranges = _split_http_ranges(content_len, chunk_size)
            logger.debug(f"DASH range-split | url={media_url} | size={content_len} | chunk={chunk_size} | parts={len(ranges)}")
            return [
                {
                    "url": media_url,
                    "number": 0,
                    "enc": {"method": "NONE"},
                    "headers": {"Range": f"bytes={start}-{end}"},
                }
                for start, end in ranges
            ]
        except Exception as exc:
            logger.debug(f"DASH range-split skipped for {media_url}: {exc}")
            return []

    def _run_dl(
        self,
        segs: List[Dict],
        out_dir: Path,
        headers: Dict,
        progress_cb,
        stream=None,
        event_cb=None,
    ) -> List[Path]:
        """Dispatch segment downloads to the external Velora binary."""
        try:
            plan_task_key = self._stream_task_key(stream) if stream else "download"
            plan_label = (
                self._video_label
                if stream and stream.type == "video"
                else self._audio_labels.get((stream.language or "und").lower(), "")
                if stream and stream.type == "audio"
                else ""
            )
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
                        "path": str(out_dir / f"seg_{seg['number']:08d}.bin"),
                        "headers": {**headers, **(seg.get("headers", {}))}  # Merge global headers with segment-specific headers (e.g., Range for byte-range segments)
                    }
                    for seg in segs
                ],
            }

            results = run_download_plan(
                plan,
                progress_cb=progress_cb,
                event_cb=event_cb,
                stop_check=self._stop_check,
            )
            return [Path(item["path"]) for item in results if item.get("path")]
        except Exception as exc:
            logger.error(f"_run_dl failed: {exc}", exc_info=True)
            return []

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
        """
        Build output filename so _build_status() can find it.

        Video -> ``{filename}.{ext}``
        Audio -> ``{filename}.{lang}.m4a``
        """
        if stream.type == "video":
            return f"{self.filename}.{ext}"
        lang = re.sub(r"[^\w\-]", "_", (stream.language or "und").lower())
        audio_ext = "webm" if ext == "webm" else "m4a"
        return f"{self.filename}.{lang}.{audio_ext}"
