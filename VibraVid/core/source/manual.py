# 09.04.26

import re
import time
import queue
import asyncio
import logging
import platform
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client
from VibraVid.utils.http_fallback_requests import patch_curl_cffi_with_requests_fallback
from VibraVid.core.ui.tracker import download_tracker
from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.core.utils.decrypt_engine import Decryptor, KeysManager
from VibraVid.core.muxing.helper.video import binary_merge_segments
from .base import BaseMediaDownloader
patch_curl_cffi_with_requests_fallback()

try:
    from Cryptodome.Cipher import AES as _AES
    from Cryptodome.Util.Padding import unpad as _unpad
    _HAS_AES = True
except ImportError:
    _HAS_AES = False


console = Console(force_terminal=True if platform.system().lower() != "windows" else None)
logger  = logging.getLogger("manual")
CONCURRENT_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "concurrent_download")
THREAD_COUNT  = config_manager.config.get_int("DOWNLOAD", "thread_count")
RETRY_COUNT   = config_manager.config.get_int("REQUESTS", "max_retry")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")
USE_PROXY  = config_manager.config.get_bool("REQUESTS", "use_proxy")
PROXY_CFG  = config_manager.config.get_dict("REQUESTS", "proxy")


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


def _parse_hls_variant_playlist(content: str, base_url: str) -> Tuple[List[Dict], Optional[str]]:
    """Parse an HLS *variant* (media) playlist."""
    segments: List[Dict] = []
    current_enc: Dict    = {"method": "NONE", "key_url": None, "iv": None}
    init_url: Optional[str] = None
    seg_num = 0
    map_count = 0

    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("#EXT-X-KEY:"):
            method_m = re.search(r"METHOD=([^,\s\"]+)", line)
            uri_m    = re.search(r'URI="([^"]+)"', line)
            iv_m     = re.search(r"IV=0x([0-9a-fA-F]+)", line, re.I)
            current_enc = {
                "method":  (method_m.group(1).upper() if method_m else "NONE"),
                "key_url": (urljoin(base_url, uri_m.group(1)) if uri_m else None),
                "iv":      (iv_m.group(1).lower().zfill(32) if iv_m else None),
            }

        elif line.startswith("#EXT-X-MAP:"):
            map_count += 1
            # N_m3u8DL-RE behavior for this workflow: stop when a second MAP appears
            # to avoid mixing init/segments from different timeline blocks.
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
                            "url":    urljoin(base_url, seg_url),
                            "number": seg_num,
                            "enc":    dict(current_enc),
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


def _fmt_size(nb: int) -> str:
    if nb >= 1_073_741_824:
        return f"{nb / 1_073_741_824:.2f}GB"
    if nb >= 1_048_576:
        return f"{nb / 1_048_576:.1f}MB"
    if nb >= 1_024:
        return f"{nb / 1024:.0f}KB"
    return f"{nb}B"


def _fmt_speed(bps: float) -> str:
    if bps == 0:
        return "---"
    if bps >= 1_048_576:
        return f"{bps / 1_048_576:.2f}MB/s"
    if bps >= 1_024:
        return f"{bps / 1024:.0f}KB/s"
    return f"{bps:.0f}B/s"


def _estimate_total_size(completed: int, done_segs: int, total_segs: int) -> int:
    if done_segs <= 0 or total_segs <= 0:
        return completed
    return int((completed / done_segs) * total_segs)


def _split_http_ranges(total_size: int, chunk_size: int) -> List[Tuple[int, int]]:
    ranges: List[Tuple[int, int]] = []
    start = 0
    while start < total_size:
        end = min(start + chunk_size - 1, total_size - 1)
        ranges.append((start, end))
        start = end + 1
    return ranges


def _download_segments_threaded(
    segments: List[Dict], out_dir: Path, headers: Dict, concurrency: int = 8, progress_cb=None,
    stop_check=None, retry: int = 3, timeout_s: int = 30,
    key_cache: Optional[Dict[str, bytes]] = None, key: Optional[Any] = None, live_decryption: bool = False,
) -> List[Path]:
    """
    Queue-based threaded segment downloader.

    Each worker thread owns its own curl_cffi session.

    NON-LIVE mode (live_decryption=False, default):
        • AES-128 (#EXT-X-KEY) segments are decrypted in-process.
        • CENC / Widevine segments are written RAW — post-download decryption
          via Bento4 / Shaka is handled by _decrypt_check().

    LIVE mode (live_decryption=True):
        • AES-128 segments are still decrypted in-process (unchanged).
        • CENC segments are decrypted per-fragment using
          Decryptor.decrypt_segment_live().
        • The merged file will already be clear-text; _decrypt_check() skips it.
    """
    if key_cache is None:
        key_cache = {}

    total = len(segments)
    if total == 0:
        return []

    logger.info(
        f"[DL] start — {total} segs, {concurrency} workers, "
        f"timeout={timeout_s}s, retry={retry}, live_decrypt={live_decryption}"
    )

    work_q: queue.Queue = queue.Queue()
    for seg in segments:
        work_q.put(seg)

    results: Dict[int, Path] = {}
    lock     = threading.Lock()
    key_lock = threading.Lock()
    done_count  = 0
    total_bytes = 0
    t_start     = time.monotonic()
    last_progress_cb_time = t_start

    worker_state: Dict[int, str] = {}
    worker_seg:   Dict[int, int] = {}

    # For live-decrypt: workers that need the init segment wait on this event
    init_ready       = threading.Event()
    global_init_data = None  # type: Optional[bytes]

    if segments and segments[0].get("number") == 0 and segments[0].get("enc", {}).get("method") == "NONE":
        pass  # Will be signalled after worker 0 downloads it
    else:
        init_ready.set()  # No init segment → nothing to wait for

    # ---------- AES-128 key fetch -------------------------------------------
    def _fetch_key(client, key_url: str) -> bytes:
        with key_lock:
            if key_url in key_cache:
                return key_cache[key_url]
        resp = client.get(key_url, timeout=15)
        resp.raise_for_status()
        kdata = resp.content
        with key_lock:
            key_cache.setdefault(key_url, kdata)
        return key_cache[key_url]

    # ---------- watchdog ----------------------------------------------------
    watchdog_stop = threading.Event()

    def _watchdog() -> None:
        while not watchdog_stop.wait(3.0):
            elapsed = time.monotonic() - t_start
            with lock:
                dc = done_count
                tb = total_bytes
            q_size = work_q.qsize()
            speed  = tb / max(elapsed, 0.001)
            states = ", ".join(f"W{wid}={worker_state.get(wid, '?')}" for wid in sorted(worker_state))
            logger.info(f"t={elapsed:.1f}s  done={dc}/{total}  queue={q_size}  speed={_fmt_speed(speed)}  [{states}]")
            now = time.monotonic()
            for wid, seg_num in list(worker_seg.items()):
                state = worker_state.get(wid, "")
                if state.startswith(("connecting", "reading")) and ":" in state:
                    parts = state.split(":")
                    if len(parts) == 3:
                        try:
                            stuck_for = now - float(parts[2])
                            if stuck_for > 10:
                                logger.warning(f"W{wid} STUCK on seg {seg_num} in phase '{parts[0]}' for {stuck_for:.0f}s")
                        except (ValueError, IndexError):
                            pass

    wd = threading.Thread(target=_watchdog, daemon=True, name="dl-watchdog")
    wd.start()

    # ---------- worker loop -------------------------------------------------
    def _worker(wid: int) -> None:
        nonlocal done_count, total_bytes, global_init_data, last_progress_cb_time

        worker_state[wid] = "idle"
        logger.info(f"W{wid} started")

        client = create_client(headers=headers, timeout=timeout_s)
        try:
            while True:
                try:
                    seg = work_q.get_nowait()
                except queue.Empty:
                    logger.info(f"W{wid} queue empty — exiting")
                    break

                if stop_check and stop_check():
                    work_q.task_done()
                    logger.info(f"W{wid} stop requested — draining queue")
                    while True:
                        try:
                            work_q.get_nowait()
                            work_q.task_done()
                        except queue.Empty:
                            break
                    break

                num      = seg["number"]
                url      = seg["url"]
                enc      = seg.get("enc", {})
                seg_path = out_dir / f"seg_{num:08d}.bin"
                worker_seg[wid] = num

                # Resume: skip already-complete segments
                if seg_path.exists() and seg_path.stat().st_size > 0:
                    with lock:
                        results[num]  = seg_path
                        done_count   += 1
                    work_q.task_done()
                    continue

                for attempt in range(retry):
                    if stop_check and stop_check():
                        break
                    t_seg = time.monotonic()
                    try:
                        worker_state[wid] = f"connecting:{num}:{t_seg:.3f}"
                        logger.info(f"W{wid} seg {num} attempt {attempt+1}/{retry} GET: {url}")
                        req_headers = seg.get("headers") or None
                        resp = client.get(url, headers=req_headers)
                        resp.raise_for_status()
                        data = resp.content
                        elapsed_get = time.monotonic() - t_seg
                        logger.info(f"W{wid} seg {num} downloaded {len(data)} bytes in {elapsed_get:.2f}s")

                        # ── AES-128 (#EXT-X-KEY) — always in-process ──────────────
                        method = enc.get("method", "NONE").upper()
                        if method in ("AES-128", "AES-128-CBC"):
                            key_url = enc.get("key_url")
                            if key_url:
                                worker_state[wid] = f"decrypting:{num}:{time.monotonic():.3f}"
                                key_data = _fetch_key(client, key_url)
                                data = _decrypt_aes128(data, key_data, enc.get("iv"), num)
                                logger.info(f"W{wid} seg {num} AES-128 decrypted OK")
                            else:
                                logger.error(f"W{wid} seg {num}: AES-128 but no key URI — writing raw")

                        # ── LIVE per-segment CENC decrypt ─────────────────────────
                        elif live_decryption and key:
                            if num == 0 and enc.get("method", "NONE") == "NONE":
                                with lock:
                                    global_init_data = data
                                init_ready.set()
                                logger.info(f"W{wid} seg {num} (init) stored {len(data)} bytes, signalling init_ready")
                            else:
                                try:
                                    key_str = (str(key[0]).strip() if isinstance(key, list) else str(key).strip())

                                    # Extract first key pair from pipe-separated list
                                    first_key_pair = key_str.split("|")[0]
                                    key_parts = first_key_pair.split(":")
                                    if len(key_parts) >= 2:
                                        raw_kid = key_parts[0]
                                        raw_key = key_parts[1]
                                    else:
                                        raw_kid = "00000000000000000000000000000000"
                                        raw_key = key_parts[0]
                                except Exception:
                                    raw_kid = "00000000000000000000000000000000"
                                    raw_key = str(key)

                                worker_state[wid] = f"decrypting_live:{num}:{time.monotonic():.3f}"

                                init_ready.wait(timeout=timeout_s * 2)
                                enc_tmp  = seg_path.with_suffix(".enc.m4s")
                                dec_tmp  = seg_path.with_suffix(".dec.m4s")
                                init_tmp = out_dir / "init_seg.mp4"
                                enc_tmp.write_bytes(data)

                                if not init_tmp.exists():
                                    with lock:
                                        _idata = global_init_data
                                    if _idata:
                                        init_tmp.write_bytes(_idata)

                                decryptor_live = Decryptor()
                                success, msg, dec_data = decryptor_live.decrypt_segment_live(
                                    encrypted_path=str(enc_tmp),
                                    decrypted_path=str(dec_tmp),
                                    raw_key=raw_key,
                                    raw_kid=raw_kid,
                                    init_path=str(init_tmp) if init_tmp.exists() else None,
                                )

                                enc_tmp.unlink(missing_ok=True)
                                dec_tmp.unlink(missing_ok=True)

                                if success and dec_data:
                                    data = dec_data
                                    logger.info(f"W{wid} seg {num} live decrypt OK, {len(data)} bytes")
                                else:
                                    logger.error(f"W{wid} seg {num} live decrypt FAILED: {msg} — writing raw encrypted data")

                        # ── Write to disk ─────────────────────────────────────────
                        worker_state[wid] = f"writing:{num}:{time.monotonic():.3f}"
                        seg_path.write_bytes(data)
                        logger.info(f"W{wid} seg {num} wrote {len(data)} bytes to {seg_path.name}")
                        nb = len(data)

                        with lock:
                            results[num]   = seg_path
                            done_count    += 1
                            total_bytes   += nb
                            elapsed_total  = max(time.monotonic() - t_start, 0.001)
                            speed          = total_bytes / elapsed_total
                            if progress_cb:
                                progress_cb(done_count, total, total_bytes, speed)

                        worker_state[wid] = "idle"
                        break  # success — next segment

                    except Exception as exc:
                        elapsed_fail = time.monotonic() - t_seg
                        if attempt < retry - 1:
                            is_503 = "503" in str(exc) or "Service Unavailable" in str(exc)
                            wait   = min(3 * (2 ** attempt), 30) if is_503 else 0.5 * (2 ** attempt)
                            logger.warning(
                                f"W{wid} seg {num} attempt {attempt+1}/{retry} FAILED "
                                f"after {elapsed_fail:.2f}s ({type(exc).__name__}: {exc}) — retry in {wait:.1f}s"
                            )
                            worker_state[wid] = f"retry_wait:{num}:{time.monotonic():.3f}"
                            time.sleep(wait)
                        else:
                            logger.error(f"W{wid} seg {num} GAVE UP after {retry} attempts ({elapsed_fail:.2f}s last) — {exc}")
                            worker_state[wid] = "idle"

                work_q.task_done()

        finally:
            worker_state[wid] = "done"
            logger.info(f"W{wid} exiting")
            try:
                client.close()
            except Exception:
                pass

    # ---------- launch workers (gradual ramp-up to avoid 503) ---------------
    n_workers = min(concurrency, total)
    logger.info(f"[DL] launching {n_workers} workers (ramp-up over 5s)")
    workers = [
        threading.Thread(target=_worker, args=(i,), daemon=True, name=f"dl-worker-{i}")
        for i in range(n_workers)
    ]
    ramp_interval = 5.0 / max(n_workers, 1)
    for idx, w in enumerate(workers):
        if idx > 0:
            time.sleep(ramp_interval)
        w.start()
        logger.info(f"[DL] W{idx} started (ramp-up {idx+1}/{n_workers})")

    deadline = time.monotonic() + 7200.0
    while True:
        alive = [w for w in workers if w.is_alive()]
        if not alive:
            break
        if stop_check and stop_check():
            logger.info("[DL] stop_check fired in join loop — breaking")
            break
        if time.monotonic() >= deadline:
            logger.error("[DL] hard 2-hour timeout reached")
            break
        for w in alive:
            w.join(timeout=0.25)

    watchdog_stop.set()

    with lock:
        elapsed = time.monotonic() - t_start
        speed   = total_bytes / max(elapsed, 0.001)
        if progress_cb:
            progress_cb(done_count, total, total_bytes, speed)

    elapsed = time.monotonic() - t_start
    logger.info(
        f"[DL] finished — {len(results)}/{total} segs  {_fmt_size(total_bytes)}  "
        f"{elapsed:.1f}s  avg {_fmt_speed(total_bytes / max(elapsed, 0.001))}"
    )
    return [results[n] for n in sorted(results)]


def _join_interruptible(threads: List[threading.Thread], stop_event: threading.Event, poll: float = 0.25, hard_timeout: float = 7200.0) -> None:
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
    def __init__(self, 
        url: str, output_dir: str, filename: str, headers: Optional[Dict] = None, key: Optional[Any] = None, cookies: Optional[Dict] = None,
        download_id: Optional[str] = None, site_name: Optional[str] = None, max_segments: Optional[int] = None,
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
        self.manual_concurrency = 8
        self.max_segments = max_segments

        # Cancellation
        self._stop_event: threading.Event = threading.Event()
        self._active_loops: List[asyncio.AbstractEventLoop] = []
        self._loops_lock: threading.Lock = threading.Lock()

        # Live decryption tracking
        self._session_live_decrypt: bool = False

    def start_download(self) -> Dict[str, Any]:
        """Download all selected streams, decrypt if needed, return status dict."""
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")

        # Promote HLS embedded subtitles to external list (shared helper)
        self._promote_hls_subtitles_to_external()

        self._prepare_labels()

        # Determine optimal live decryption mode
        # Live decryption only if ALL streams support it (e.g., CENC/Widevine)
        # For SAMPLE-AES/CBCS: Must use post-merge decryption
        selected_media = [
            s for s in self.streams
            if s.selected and not s.is_external and s.type in ("video", "audio")
        ]
        all_support_live = all(s.supports_live_decryption for s in selected_media) if selected_media else False
        
        if all_support_live and selected_media:
            self._session_live_decrypt = True
            logger.info("All selected streams support live decryption - using in-flight decryption.")
        else:
            self._session_live_decrypt = False
            if selected_media and not all_support_live:
                logger.info("SAMPLE-AES/CBCS detected - using post-merge decryption with Shaka Packager.")
            else:
                logger.info("Using post-download decryption.")

        ext_result: Dict[str, Any] = {"ext_subs": [], "ext_auds": []}

        try:
            with DownloadBarManager(self.download_id) as bar_manager:
                bar_manager.add_prebuilt_tasks(self._get_prebuilt_tasks())
                self._register_external_track_tasks(bar_manager)

                # External tracks thread
                ext_loop = asyncio.new_event_loop()
                self._register_loop(ext_loop)

                def _run_externals() -> None:
                    asyncio.set_event_loop(ext_loop)
                    try:
                        from VibraVid.core.downloader.subtitle import download_external_tracks_with_progress
                        subs, auds = ext_loop.run_until_complete(
                            download_external_tracks_with_progress(
                                self.headers,
                                self.external_subtitles,
                                self.external_audios,
                                self.output_dir,
                                self.filename,
                                bar_manager,
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

                # Media stream threads (video + audio)
                media_threads: List[threading.Thread] = []
                for stream in selected_media:
                    def _run_stream(s=stream) -> None:
                        try:
                            self._download_stream(s, bar_manager)
                        except Exception as exc:
                            logger.error(
                                f"Stream download error ({s.type}/{s.language}): {exc}",
                                exc_info=True,
                            )

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

        if self.key:
            self._decrypt_check(self.status, live_decryption_used=self._session_live_decrypt)

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

    def _decrypt_check(self, status: Dict[str, Any], live_decryption_used: bool = False) -> None:
        """
        Post-download decryption for live_decryption_used=False (default).

        If live_decryption_used=True the segments were already decrypted
        in-flight by _download_segments_threaded(), so we skip re-decryption.
        """
        if live_decryption_used and self.key:
            logger.info("_decrypt_check: segments were already decrypted during download, skipping.")
            return

        if self.download_id:
            download_tracker.update_status(self.download_id, "Decrypting ...")

        decryptor = Decryptor(
            license_url=self.license_url,
            drm_type=self.drm_type,
        )
        if isinstance(self.key, KeysManager):
            keys = self.key.get_keys_list()
        elif isinstance(self.key, str):
            keys = [self.key]
        else:
            keys = self.key

        targets = []
        if status.get("video"):
            targets.append((status["video"], "video"))
        for aud in status.get("audios", []):
            targets.append((aud, "audio"))

        for target, stype in targets:
            fp  = Path(target["path"])
            if not fp.exists():
                continue
            out = fp.with_suffix(fp.suffix + ".dec")
            success = decryptor.decrypt(str(fp), keys, str(out), stream_type=stype)
            if success:
                try:
                    fp.unlink()
                    out.rename(fp)
                    target["size"] = fp.stat().st_size
                except Exception as exc:
                    logger.error(f"Failed to replace encrypted file: {exc}")
                    if out.exists():
                        out.unlink()
            else:
                logger.error(f"Decryption failed for {fp.name}")
                if out.exists():
                    try:
                        out.unlink()
                    except Exception:
                        pass

    def _stream_task_key(self, stream) -> str:
        if stream.type == "video":
            return self._video_task_key
        lang = (stream.resolved_language or stream.language or "und").lower()
        return f"aud_{lang.split('-')[0]}"

    def _make_stream_dir(self, stream, protocol: str) -> Path:
        proto = protocol.lower()
        bw    = str(stream.bitrate or 0)
        sid   = _safe(stream.id or "", maxlen=24)

        if stream.type == "video":
            res  = _safe(stream.resolution or "unknown")
            name = f"{proto}_video_{res}_{sid}_{bw}"
        else:
            lang = _safe(stream.language or "und")
            name = f"{proto}_audio_{lang}_{sid}_{bw}"

        d = self._tmp_dir / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _download_stream(self, stream, bar_manager: DownloadBarManager) -> None:
        effective_live = self._session_live_decrypt
        if self.manifest_type == "HLS":
            self._download_hls_stream(stream, bar_manager, effective_live)
        else:
            self._download_dash_stream(stream, bar_manager, effective_live)

    def _download_stream_generic(self, dl_segs: List[Dict], stream, protocol: str, default_ext: str, bar_manager: DownloadBarManager, live_decryption: bool = False) -> None:
        task_key   = self._stream_task_key(stream)
        total      = len(dl_segs)
        stream_dir = self._make_stream_dir(stream, protocol)
        all_headers = self._build_headers()
        key_cache: Dict[str, bytes] = {}

        def _progress(done: int, total_: int, total_bytes: int, speed_bps: float) -> None:
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
                    "_task_key": task_key,
                    "pct":       pct,
                    "segments":  f"{done}/{total_}",
                    "size":      size_display,
                    "speed":     _fmt_speed(speed_bps),
                }
            )

        paths = self._run_dl(dl_segs, stream_dir, all_headers, key_cache, _progress, live_decryption=live_decryption)
        if self._stop_check() or not paths:
            return

        sample_url = dl_segs[0]["url"] if dl_segs else ""
        ext = _detect_seg_ext(sample_url, default=default_ext)
        if ext == "m4s":
            ext = "mp4"

        out_path = self.output_dir / self._out_filename(stream, ext)
        binary_merge_segments(paths, out_path, merge_logger=logger)

        if out_path.exists() and out_path.stat().st_size > 0:
            logger.info(
                f"{protocol.upper()} merged {len(paths):>4} segs -> "
                f"{out_path.name}  ({out_path.stat().st_size // 1024} KB)"
            )
            _progress(total, total, out_path.stat().st_size, 0.0)
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

        base_url  = _hls_base_url(playlist_url)
        media_segs, init_url = _parse_hls_variant_playlist(playlist_content, base_url)

        if not media_segs and not init_url:
            logger.error(f"HLS variant playlist has no segments: {playlist_url}")
            return

        dl_segs: List[Dict] = []
        if init_url:
            dl_segs.append({"url": init_url, "number": 0, "enc": {"method": "NONE"}})
        offset = len(dl_segs)
        for seg in media_segs:
            dl_segs.append({
                    "url":    seg["url"],
                    "number": seg["number"] + offset,
                    "enc":    seg["enc"],
            })

        # Apply max_segments limit if specified
        if self.max_segments is not None and self.max_segments > 0:
            
            # Keep init segment + first max_segments media segments
            if init_url:
                dl_segs = dl_segs[:1 + self.max_segments]
            else:
                dl_segs = dl_segs[:self.max_segments]
            logger.info(f"Limiting HLS download to {len(dl_segs)} segments (max_segments={self.max_segments})")

        self._download_stream_generic(dl_segs, stream, "hls", "ts", bar_manager, live_decryption=live_decryption)

    def _download_dash_stream(self, stream, bar_manager: DownloadBarManager, live_decryption: bool = False) -> None:
        if not stream.segments:
            logger.error(f"DASH stream has no segments: {stream}")
            return

        all_headers = self._build_headers()
        chunk_size  = max(8 * 1024 * 1024, 1 * 1024 * 1024)

        media_segments       = [s for s in stream.segments if s.seg_type == "media"]
        is_single_file_media = len(media_segments) == 1

        dl_segs: List[Dict] = []
        next_num = 0
        for seg in stream.segments:
            if seg.byte_range:
                dl_segs.append(
                    {
                        "url":     seg.url,
                        "number":  next_num,
                        "enc":     {"method": "NONE"},
                        "headers": {"Range": f"bytes={seg.byte_range}"},
                    }
                )
                next_num += 1

            elif is_single_file_media and seg.seg_type == "media":
                ranged = self._build_dash_ranged_segments(seg.url, all_headers, chunk_size)
                if ranged:
                    for part in ranged:
                        part["number"] = next_num
                        dl_segs.append(part)
                        next_num += 1
                    continue
                else:
                    dl_segs.append({"url": seg.url, "number": next_num, "enc": {"method": "NONE"}})
                    next_num += 1
            else:
                dl_segs.append({"url": seg.url, "number": next_num, "enc": {"method": "NONE"}})
                next_num += 1

        # Apply max_segments limit if specified
        if self.max_segments is not None and self.max_segments > 0:
            dl_segs = dl_segs[:self.max_segments]
            logger.info(f"Limiting DASH download to {len(dl_segs)} segments (max_segments={self.max_segments})")

        self._download_stream_generic(dl_segs, stream, "dash", "mp4", bar_manager, live_decryption=live_decryption)

    def _build_dash_ranged_segments(self, media_url: str, headers: Dict, chunk_size: int) -> List[Dict]:
        """Return synthetic DASH chunk segments using HTTP Range, when supported."""
        try:
            with create_client(headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                r = c.head(media_url)
                r.raise_for_status()

            content_len   = int((r.headers.get("content-length") or "0").strip() or "0")
            accept_ranges = (r.headers.get("accept-ranges") or "").lower()

            if content_len <= chunk_size or "bytes" not in accept_ranges:
                return []

            ranges = _split_http_ranges(content_len, chunk_size)
            logger.info(
                f"DASH range-split | url={media_url} | size={content_len} "
                f"| chunk={chunk_size} | parts={len(ranges)}"
            )
            return [
                {
                    "url":     media_url,
                    "number":  0,
                    "enc":     {"method": "NONE"},
                    "headers": {"Range": f"bytes={start}-{end}"},
                }
                for start, end in ranges
            ]
        except Exception as exc:
            logger.info(f"DASH range-split skipped for {media_url}: {exc}")
            return []

    def _run_dl(self, segs: List[Dict], out_dir: Path, headers: Dict, key_cache: Dict, progress_cb, live_decryption: bool = False) -> List[Path]:
        """Dispatch to queue-based thread pool."""
        try:
            return _download_segments_threaded(
                segs,
                out_dir,
                headers,
                concurrency=THREAD_COUNT,
                progress_cb=progress_cb,
                stop_check=self._stop_check,
                retry=RETRY_COUNT,
                timeout_s=REQUEST_TIMEOUT,
                key_cache=key_cache,
                key=self.key,
                live_decryption=live_decryption,
            )
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
        lang      = re.sub(r"[^\w\-]", "_", (stream.language or "und").lower())
        audio_ext = "webm" if ext == "webm" else "m4a"
        return f"{self.filename}.{lang}.{audio_ext}"