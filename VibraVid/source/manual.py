# 19.03.26

import re
import time
import queue
import asyncio
import logging
import platform
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.core.manifest.m3u8 import HLSParser
from VibraVid.core.manifest.mpd import DashParser
from VibraVid.core.manifest.stream import Stream
from VibraVid.utils.tmdb_client import tmdb_client
from VibraVid.utils.http_client import create_client, get_headers

from VibraVid.source.style.tracker import download_tracker
from VibraVid.source.style.bar_manager import DownloadBarManager
from VibraVid.source.utils.selector import StreamSelector, N3u8dlFormatter
from VibraVid.source.style.ui import build_table
from VibraVid.source.utils.language import resolve_locale, LANGUAGE_MAP
from VibraVid.core.downloader.subtitle import download_external_tracks_with_progress, build_ext_track_label, is_valid_format, ext_from_url
from VibraVid.source.utils.codec import VIDEO_EXTENSIONS, AUDIO_EXTENSIONS
from VibraVid.source.utils.decrypt import Decryptor, KeysManager

try:
    from Cryptodome.Cipher import AES as _AES
    from Cryptodome.Util.Padding import unpad as _unpad
    _HAS_AES = True
except ImportError:
    try:
        from Crypto.Cipher import AES as _AES
        from Crypto.Util.Padding import unpad as _unpad
        _HAS_AES = True
    except ImportError:
        _HAS_AES = False


console = Console(force_terminal=True if platform.system().lower() != "windows" else None)
logger  = logging.getLogger("manual")
CONCURRENT_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "concurrent_download")
THREAD_COUNT = config_manager.config.get_int("DOWNLOAD", "thread_count")
RETRY_COUNT = config_manager.config.get_int("REQUESTS", "max_retry")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")
USE_PROXY = config_manager.config.get_bool("REQUESTS", "use_proxy")
PROXY_CFG = config_manager.config.get_dict("REQUESTS", "proxy")


def _resolve_subtitle_url_sync(url: str, headers: Dict) -> Tuple[str, str]:
    """Synchronously probe *url* to determine the real subtitle format.

    If the response is an HLS manifest (``#EXTM3U``), the first media segment
    URL is extracted and its extension is used.  Returns ``(final_url, ext)``
    where *ext* may be an empty string if nothing recognisable was found.
    """
    try:
        hdrs = dict(headers)
        hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))
        logger.info(f"_resolve_subtitle_url_sync: probing subtitle URL {url!r} with headers {hdrs}")
        with create_client(headers=hdrs, timeout=15, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            text = resp.text.strip()
    except Exception as exc:
        logger.info(f"_resolve_subtitle_url_sync: request failed for {url!r}: {exc}")
        return url, ext_from_url(url, "")

    if text.startswith("#EXTM3U"):
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                resolved_ext = ext_from_url(line, "")
                logger.info(f"Resolved HLS subtitle manifest -> segment {line!r} (ext={resolved_ext!r})")
                return line, resolved_ext
        logger.info(f"_resolve_subtitle_url_sync: manifest at {url!r} had no segments")
        return url, ""

    content_type = resp.headers.get("content-type", "").lower()
    for mime, ext in (("vtt", "vtt"), ("webvtt", "vtt"), ("srt", "srt"), ("ttml", "ttml"), ("xml", "xml"), ("dfxp", "dfxp")):
        if mime in content_type:
            return url, ext
    return url, ext_from_url(url, "")


def _lang_variants(normalized_lang: str) -> Set[str]:
    if not normalized_lang:
        return set()
    variants: Set[str] = {normalized_lang, normalized_lang.lower()}
    variants.add(normalized_lang.split("-")[0].lower())
    for key, value in LANGUAGE_MAP.items():
        if value == normalized_lang or value.lower() == normalized_lang.lower():
            variants.add(key)
            variants.add(key.lower())
    return variants


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


def _binary_merge(paths: List[Path], output_path: Path) -> None:
    with open(output_path, "wb") as out_f:
        for p in paths:
            if p.exists() and p.stat().st_size > 0:
                out_f.write(p.read_bytes())


def _fmt_size(nb: int) -> str:
    if nb >= 1_048_576:
        return f"{nb / 1_048_576:.1f}MB"
    return f"{nb / 1024:.0f}KB"


def _fmt_speed(bps: float) -> str:
    if bps >= 1_048_576:
        return f"{bps / 1_048_576:.2f}MBps"
    return f"{bps / 1024:.0f}KBps"


def _download_segments_threaded(segments: List[Dict], out_dir: Path, headers: Dict, concurrency: int = 8, progress_cb=None,
    stop_check=None, retry: int = 3, timeout_s: int = 30, key_cache: Optional[Dict[str, bytes]] = None,
) -> List[Path]:
    """
    Queue-based threaded segment downloader.

    Each worker thread owns its own curl_cffi session — no shared state, no asyncio/executor overhead, no ThreadPoolExecutor exhaustion.
    """
    if key_cache is None:
        key_cache = {}

    total = len(segments)
    if total == 0:
        return []

    logger.info(f"start — {total} segs, {concurrency} workers, timeout={timeout_s}s, retry={retry}")

    work_q: queue.Queue = queue.Queue()
    for seg in segments:
        work_q.put(seg)

    results: Dict[int, Path] = {}
    lock = threading.Lock()
    key_lock = threading.Lock()
    done_count = 0
    total_bytes = 0
    t_start = time.monotonic()

    # Per-worker state table — watchdog reads this without holding any lock
    # Values: "idle" | "connecting:N" | "reading:N" | "decrypting:N" | "writing:N" | "done"
    worker_state: Dict[int, str] = {}
    worker_seg:   Dict[int, int] = {}   # wid -> current segment number

    # ---------- key helper -------------------------------------------------
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

    # ---------- watchdog ---------------------------------------------------
    watchdog_stop = threading.Event()

    def _watchdog() -> None:
        """Log worker states every 3 s so stalls are immediately visible."""
        while not watchdog_stop.wait(3.0):
            elapsed = time.monotonic() - t_start
            with lock:
                dc = done_count
                tb = total_bytes
            q_size = work_q.qsize()
            speed  = tb / max(elapsed, 0.001)
            states = ", ".join(f"W{wid}={worker_state.get(wid, '?')}" for wid in sorted(worker_state))
            logger.info(f"t={elapsed:.1f}s  done={dc}/{total}  queue={q_size}  speed={_fmt_speed(speed)}  [{states}]")

            # Highlight any worker stuck on the same segment for >10 s
            now = time.monotonic()
            for wid, seg_num in list(worker_seg.items()):
                state = worker_state.get(wid, "")
                if state.startswith(("connecting", "reading")) and ":" in state:

                    # state encodes start time as "phase:segnum:start_ts"
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

    # ---------- worker loop ------------------------------------------------
    def _worker(wid: int) -> None:
        nonlocal done_count, total_bytes

        worker_state[wid] = "idle"
        logger.info(f"W{wid} started")

        # Each worker owns its own curl_cffi session — no handle contention
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

                num = seg["number"]
                url = seg["url"]
                enc = seg.get("enc", {})
                seg_path = out_dir / f"seg_{num:08d}.bin"
                worker_seg[wid] = num

                # Resume: skip already-complete segments
                if seg_path.exists() and seg_path.stat().st_size > 0:
                    logger.info(f"W{wid} seg {num} already on disk — skip")
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
                        # KEY FIX: plain blocking GET (no stream=True).
                        # curl_cffi enforces `timeout` on the full body transfer,
                        # so a stalled server is killed after timeout_s seconds.
                        # stream=True only enforces it on the TCP connect, which
                        # is why every worker froze after ~4 s on stalled reads.
                        worker_state[wid] = f"connecting:{num}:{t_seg:.3f}"
                        logger.info(f"W{wid} seg {num} attempt {attempt+1}/{retry} GET {url[:80]}")

                        resp = client.get(url)
                        resp.raise_for_status()
                        data = resp.content

                        elapsed_get = time.monotonic() - t_seg
                        logger.info(f"W{wid} seg {num} downloaded {len(data)} bytes in {elapsed_get:.2f}s")

                        # AES-128 decryption
                        method = enc.get("method", "NONE").upper()
                        if method in ("AES-128", "AES-128-CBC"):
                            key_url = enc.get("key_url")
                            if key_url:
                                worker_state[wid] = f"decrypting:{num}:{time.monotonic():.3f}"
                                key_data = _fetch_key(client, key_url)
                                data = _decrypt_aes128(data, key_data, enc.get("iv"), num)
                                logger.info(f"W{wid} seg {num} decrypted OK")
                            else:
                                logger.error(f"W{wid} seg {num}: AES-128 but no key URI — writing raw")

                        worker_state[wid] = f"writing:{num}:{time.monotonic():.3f}"
                        seg_path.write_bytes(data)
                        nb = len(data)

                        with lock:
                            results[num]  = seg_path
                            done_count   += 1
                            total_bytes  += nb
                            elapsed_total = max(time.monotonic() - t_start, 0.001)
                            speed = total_bytes / elapsed_total
                            if progress_cb:
                                progress_cb(done_count, total, total_bytes, speed)

                        worker_state[wid] = "idle"
                        break  # success

                    except Exception as exc:
                        elapsed_fail = time.monotonic() - t_seg
                        if attempt < retry - 1:
                            is_503 = "503" in str(exc) or "Service Unavailable" in str(exc)
                            wait   = min(3 * (2 ** attempt), 30) if is_503 else 0.5 * (2 ** attempt)
                            logger.warning(f"W{wid} seg {num} attempt {attempt+1}/{retry} FAILED after {elapsed_fail:.2f}s ({type(exc).__name__}: {exc}) — retry in {wait:.1f}s")
                            worker_state[wid] = f"retry_wait:{num}:{time.monotonic():.3f}"
                            time.sleep(wait)
                        else:
                            logger.error(f"W{wid} seg {num} GAVE UP after {retry} attempts ({elapsed_fail:.2f}s last attempt) — {exc}")
                            worker_state[wid] = "idle"

                work_q.task_done()

        finally:
            worker_state[wid] = "done"
            logger.info(f"W{wid} exiting")
            try:
                client.close()
            except Exception:
                pass

    # ---------- launch workers (gradual ramp-up for first 5s) ---------------
    n_workers = min(concurrency, total)
    logger.info(f"[DL] launching {n_workers} workers for {total} segments (ramp-up over 5s)")
    workers = [
        threading.Thread(target=_worker, args=(i,), daemon=True, name=f"dl-worker-{i}")
        for i in range(n_workers)
    ]
    
    # Ramp-up: launch workers gradually over 5 seconds to prevent 503 errors
    # Each worker starts at an interval, allowing server to stabilize load
    ramp_up_duration = 5.0
    ramp_up_interval = ramp_up_duration / max(n_workers, 1)
    
    for idx, w in enumerate(workers):
        if idx > 0:
            time.sleep(ramp_up_interval)
        w.start()
        logger.info(f"[DL] W{idx} started (ramp-up: {idx + 1}/{n_workers})")

    # Poll so KeyboardInterrupt / stop_check fires promptly
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

    elapsed = time.monotonic() - t_start
    logger.info(f"[DL] finished — {len(results)}/{total} segs  {_fmt_size(total_bytes)}  {elapsed:.1f}s  avg {_fmt_speed(total_bytes / max(elapsed, 0.001))}")
    return [results[n] for n in sorted(results)]


def _join_interruptible(threads: List[threading.Thread], stop_event: threading.Event, poll: float = 0.25, hard_timeout: float = 7200.0) -> None:
    """
    Join *threads* in a tight polling loop so KeyboardInterrupt is always deliverable.
    """
    deadline = time.monotonic() + hard_timeout
    while True:
        alive = [t for t in threads if t.is_alive()]
        if not alive:
            break
        if stop_event.is_set() or time.monotonic() >= deadline:
            break
        for t in alive:
            t.join(timeout=poll)


class MediaDownloader:
    def __init__(self, url: str, output_dir: str, filename: str, headers: Optional[Dict] = None, key: Optional[Any] = None, cookies: Optional[Dict] = None, decrypt_preference: str = "shaka", download_id: Optional[str] = None, site_name: Optional[str] = None):
        self.url = url
        self.output_dir = Path(output_dir)
        self.filename = filename
        self.headers = headers or {}
        self.key = key
        self.cookies = cookies or {}
        self.decrypt_preference = decrypt_preference.strip().lower()
        self.download_id = download_id
        self.site_name = site_name
        self.manual_concurrency = 8

        self.streams: List[Stream] = []
        self.manifest_type: str = "Unknown"
        self.raw_m3u8: Optional[Path] = None
        self.raw_mpd:  Optional[Path] = None
        self.status:   Optional[dict] = None

        self._sv: str = "best"
        self._sa: str = "best"
        self._ss: str = "all"

        self.external_subtitles: list = []
        self.external_audios: list = []
        self.custom_filters: Optional[Dict] = None
        self.license_url: Optional[str] = None
        self.drm_type: Optional[str] = None

        # Progress-bar label tables
        self._video_label: str = ""
        self._video_task_key: str = "vid_main"
        self._has_video: bool = True
        self._audio_labels: Dict[str, str] = {}
        self._audio_task_keys: List[Tuple[str, str]] = []
        self._sub_labels: Dict[str, str] = {}

        # --- Cancellation state -------------------------------------------
        self._stop_event: threading.Event = threading.Event()
        self._active_loops: List[asyncio.AbstractEventLoop] = []
        self._loops_lock: threading.Lock  = threading.Lock()

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_dir = self.output_dir / f"{self.filename}_tmp"
        self._tmp_dir.mkdir(exist_ok=True)

        if self.download_id:
            _type = (
                "Movie" if config_manager.config.get("OUTPUT", "movie_folder_name") in str(self.output_dir)
                else "TV" if config_manager.config.get("OUTPUT", "serie_folder_name")  in str(self.output_dir)
                else "Anime" if config_manager.config.get("OUTPUT", "anime_folder_name")  in str(self.output_dir)
                else "other"
            )
            download_tracker.start_download(self.download_id, self.filename, self.site_name or "Unknown", _type)

    def set_key(self, key: Any) -> None:
        self.key = key.get_keys_list() if isinstance(key, KeysManager) else key

    def parse_stream(self, show_table: bool = True) -> List[Stream]:
        """Fetch manifest -> parse streams -> apply selection -> print table."""
        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing ...")

        url_lower = self.url.lower().split("?")[0]
        parser = (DashParser(self.url, self.headers) if url_lower.endswith(".mpd") else HLSParser(self.url, self.headers))
        if not parser.fetch_manifest():
            logger.error("MediaDownloader: manifest fetch failed")
            return []

        if isinstance(parser, DashParser):
            self.raw_mpd = parser.save_raw(self._tmp_dir)
            self.manifest_type = "DASH"
        else:
            self.raw_m3u8 = parser.save_raw(self._tmp_dir)
            self.manifest_type = "HLS"

        self.streams = [s for s in parser.parse_streams() if s.type != "image"]
        self._apply_selection()

        for ext in self.external_subtitles:
            lang     = ext.get("language", "")
            selected = self._ext_track_matches(ext, "subtitle")
            ext["_selected"] = selected
            fake = Stream(type="subtitle", language=lang, name=ext.get("name", ""), selected=selected, is_external=True,)
            fake.id = "EXT"
            self.streams.append(fake)

        for ext in self.external_audios:
            lang = ext.get("language", "")
            selected = self._ext_lang_matches(lang, "audio")
            ext["_selected"] = selected
            fake = Stream(type="audio", language=lang, name=ext.get("name", ""), selected=selected, is_external=True)
            fake.id = "EXT"
            self.streams.append(fake)

        if show_table and self.streams:
            console.print(build_table(self.streams))

        return self.streams

    parser_stream = parse_stream

    def get_metadata(self) -> Tuple[str, str, str]:
        return (str(self.raw_m3u8), str(self.raw_mpd), "")

    def start_download(self) -> Dict[str, Any]:
        """
        Download all selected streams, decrypt if needed, return status dict.
        """
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")

        # Promote HLS embedded subtitles to external list
        new_ext_subs: List[Dict] = []
        for s in self.streams:
            if s.type == "subtitle" and s.selected and not s.is_external:
                sub_url = s.playlist_url or (s.segments[0].url if s.segments else None)
                if not sub_url:
                    continue

                ext = ext_from_url(sub_url, "")
                if not ext or not is_valid_format(ext, "subtitle"):
                    resolved_url, ext = _resolve_subtitle_url_sync(sub_url, self.headers)
                    if not ext or not is_valid_format(ext, "subtitle"):
                        logger.info(f"Skipping external subtitle (unsupported format): {s.language} url={sub_url}")
                        s.selected = False
                        continue
                    sub_url = resolved_url

                new_ext_subs.append({
                    "url":       sub_url,
                    "language":  s.language or "und",
                    "name":      s.name or "",
                    "forced":    s.forced,
                    "sdh":       s.is_sdh,
                    "cc":        s.is_cc,
                    "default":   s.default,
                    "type":      ext,
                    "_selected": True,
                })
                logger.info(f"Subtitle -> external: {s.language}  url={sub_url[:80]}")
                s.selected = False

        self.external_subtitles.extend(new_ext_subs)
        if new_ext_subs:
            logger.info(f"Moved {len(new_ext_subs)} HLS subtitle(s) to external download")

        self._prepare_labels()

        ext_result: Dict[str, Any] = {"ext_subs": [], "ext_auds": []}
        selected_media = [
            s for s in self.streams
            if s.selected and not s.is_external and s.type in ("video", "audio")
        ]

        try:
            with DownloadBarManager(self.download_id) as bar_manager:
                bar_manager.add_prebuilt_tasks(self._get_prebuilt_tasks())

                for _track, _ttype in (
                    [(s, "subtitle") for s in self.external_subtitles if s.get("_selected", True)]
                    + [(a, "audio")  for a in self.external_audios    if a.get("_selected", True)]
                ):
                    _label    = build_ext_track_label(_track, _ttype)
                    _lang     = _track.get("language", "und")
                    _task_key = f"ext_{_ttype}_{_lang}_{id(_track)}"
                    _track["_task_key"] = _task_key
                    _track["_label"]    = _label
                    bar_manager.add_external_track_task(_label, _task_key)

                # --- External tracks thread (subtitle.py) -----------------
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
                            )
                        )
                        ext_result["ext_subs"] = subs
                        ext_result["ext_auds"] = auds
                    except Exception as exc:
                        logger.error(f"External downloads failed: {exc}")
                    finally:
                        self._unregister_loop(ext_loop)
                        ext_loop.close()

                # daemon=True: killed automatically when the main process exits
                ext_thread = threading.Thread(target=_run_externals, daemon=True)
                ext_thread.start()

                # --- Media stream threads (video + audio) -----------------
                media_threads: List[threading.Thread] = []

                for stream in selected_media:
                    def _run_stream(s=stream) -> None:
                        try:
                            self._download_stream(s, bar_manager)
                        except Exception as exc:
                            logger.error(f"Stream download error ({s.type}/{s.language}): {exc}", exc_info=True)

                    # daemon=True: automatic cleanup on hard exit (Ctrl+C, crash)
                    t = threading.Thread(target=_run_stream, daemon=True)
                    media_threads.append(t)
                    t.start()

                # Interruptible joins — short poll so Ctrl+C fires instantly
                _join_interruptible(media_threads, self._stop_event)
                bar_manager.finish_all_tasks()
                _join_interruptible([ext_thread], self._stop_event, hard_timeout=300.0)

                ext_subs = ext_result["ext_subs"]
                ext_auds = ext_result["ext_auds"]

        except KeyboardInterrupt:
            # 1. Signal cooperative stop to all async loops
            self._stop_event.set()
            self._cancel_all_loops()

            # 2. Tell the tracker so hls.py / dash.py see "cancelled"
            if self.download_id:
                download_tracker.request_stop(self.download_id)
            
            # 3. Re-raise immediately — daemon threads die with the process
            raise

        # Cancellation check (stop requested from outside, e.g. GUI)
        if self._stop_event.is_set() or (self.download_id and download_tracker.is_stopped(self.download_id)):
            return {"error": "cancelled"}

        self.status = self._build_status(ext_subs, ext_auds)

        if self.key:
            self._decrypt_check(self.status)

        return self.status

    def get_status(self) -> Dict:
        return self.status or self._build_status([], [])

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
        """Schedule a stop on every live asyncio loop from the main thread."""
        with self._loops_lock:
            for loop in list(self._active_loops):
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass  # loop already closed

    def _stop_check(self) -> bool:
        """Single source of truth for cancellation state."""
        return self._stop_event.is_set() or bool(self.download_id and download_tracker.is_stopped(self.download_id))

    def _stream_task_key(self, stream: Stream) -> str:
        if stream.type == "video":
            return self._video_task_key
        lang = (stream.resolved_language or stream.language or "und").lower()
        return f"aud_{lang.split('-')[0]}"

    def _make_stream_dir(self, stream: Stream, protocol: str) -> Path:
        """
        Build a guaranteed-unique temp directory for one stream's segments.

        Naming scheme
        -------------
        HLS video  :  hls_video_{res}_{sid}_{bw}
        HLS audio  :  hls_audio_{lang}_{sid}_{bw}
        DASH video :  dash_video_{res}_{sid}_{bw}
        DASH audio :  dash_audio_{lang}_{sid}_{bw}
        """
        proto = protocol.lower()
        bw = str(stream.bitrate or 0)
        sid = _safe(stream.id or "", maxlen=24)

        if stream.type == "video":
            res = _safe(stream.resolution or "unknown")
            name = f"{proto}_video_{res}_{sid}_{bw}"
        else:
            lang = _safe(stream.language or "und")
            name = f"{proto}_audio_{lang}_{sid}_{bw}"

        d = self._tmp_dir / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _download_stream(self, stream: Stream, bar_manager: DownloadBarManager) -> None:
        if self.manifest_type == "HLS":
            self._download_hls_stream(stream, bar_manager)
        else:
            self._download_dash_stream(stream, bar_manager)

    def _download_hls_stream(self, stream: Stream, bar_manager: DownloadBarManager) -> None:
        task_key     = self._stream_task_key(stream)
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
            dl_segs.append({"url": init_url, "number": 0, "enc": {"method": "NONE"}})
        offset = len(dl_segs)
        for seg in media_segs:
            dl_segs.append(
                {
                    "url":    seg["url"],
                    "number": seg["number"] + offset,
                    "enc":    seg["enc"],
                }
            )

        total = len(dl_segs)
        stream_dir = self._make_stream_dir(stream, "hls")
        key_cache: Dict[str, bytes] = {}

        def _progress(done: int, total_: int, total_bytes: int, speed_bps: float) -> None:
            pct = int((done / total_) * 100) if total_ else 0
            bar_manager.handle_progress_line(
                {
                    "_task_key": task_key,
                    "pct":       pct,
                    "segments":  f"{done}/{total_}",
                    "size":      _fmt_size(total_bytes),
                    "speed":     _fmt_speed(speed_bps),
                }
            )

        paths = self._run_dl(dl_segs, stream_dir, all_headers, key_cache, _progress)
        if self._stop_check() or not paths:
            return

        sample_url = init_url or (media_segs[0]["url"] if media_segs else "")
        ext = _detect_seg_ext(sample_url, default="ts")
        if ext == "m4s":
            ext = "mp4"

        out_path = self.output_dir / self._out_filename(stream, ext)
        _binary_merge(paths, out_path)

        if out_path.exists():
            logger.info(
                f"HLS merged {len(paths):>4} segs -> {out_path.name}"
                f"  ({out_path.stat().st_size // 1024} KB)"
            )
            _progress(total, total, out_path.stat().st_size, 0.0)
        else:
            logger.error(f"HLS binary merge produced empty file: {out_path}")

    def _download_dash_stream(self, stream: Stream, bar_manager: DownloadBarManager) -> None:
        task_key = self._stream_task_key(stream)

        if not stream.segments:
            logger.error(f"DASH stream has no segments: {stream}")
            return

        dl_segs: List[Dict] = [
            {"url": seg.url, "number": idx, "enc": {"method": "NONE"}}
            for idx, seg in enumerate(stream.segments)
        ]

        total       = len(dl_segs)
        stream_dir  = self._make_stream_dir(stream, "dash")
        all_headers = self._build_headers()

        def _progress(done: int, total_: int, total_bytes: int, speed_bps: float) -> None:
            pct = int((done / total_) * 100) if total_ else 0
            bar_manager.handle_progress_line(
                {
                    "_task_key": task_key,
                    "pct":       pct,
                    "segments":  f"{done}/{total_}",
                    "size":      _fmt_size(total_bytes),
                    "speed":     _fmt_speed(speed_bps),
                }
            )

        paths = self._run_dl(dl_segs, stream_dir, all_headers, {}, _progress)
        if self._stop_check() or not paths:
            return

        sample_url = stream.segments[0].url if stream.segments else ""
        ext = _detect_seg_ext(sample_url, default="mp4")
        if ext in ("m4s", "ts"):
            ext = "mp4"

        out_path = self.output_dir / self._out_filename(stream, ext)
        _binary_merge(paths, out_path)

        if out_path.exists():
            logger.info(
                f"DASH merged {len(paths):>4} segs -> {out_path.name}"
                f"  ({out_path.stat().st_size // 1024} KB)"
            )
            _progress(total, total, out_path.stat().st_size, 0.0)
        else:
            logger.error(f"DASH binary merge produced empty file: {out_path}")

    def _run_dl(self, segs: List[Dict], out_dir: Path, headers: Dict, key_cache: Dict, progress_cb) -> List[Path]:
        """Download segments via queue-based thread pool (one session per worker)."""
        try:
            return _download_segments_threaded(segs, out_dir, headers, concurrency=THREAD_COUNT, progress_cb=progress_cb, stop_check=self._stop_check, retry=RETRY_COUNT, timeout_s=REQUEST_TIMEOUT, key_cache=key_cache)
        except Exception as exc:
            logger.error(f"_run_dl failed: {exc}", exc_info=True)
            return []

    def _build_headers(self) -> Dict:
        h = dict(self.headers)
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if "Referer" not in h and "referer" not in h:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(self.url)
                h["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
            except Exception:
                pass
        if "Accept" not in h and "accept" not in h:
            h["Accept"] = "*/*"
        if "Accept-Encoding" not in h and "accept-encoding" not in h:
            h["Accept-Encoding"] = "gzip, deflate"
        return h

    def _out_filename(self, stream: Stream, ext: str) -> str:
        """
        Build output filename so _build_status() can find it.

        Video -> ``{filename}.{ext}``           (stem == filename)
        Audio -> ``{filename}.{lang}.m4a``
        """
        if stream.type == "video":
            return f"{self.filename}.{ext}"
        lang      = re.sub(r"[^\w\-]", "_", (stream.language or "und").lower())
        audio_ext = "webm" if ext == "webm" else "m4a"
        return f"{self.filename}.{lang}.{audio_ext}"

    def _apply_selection(self) -> None:
        f = self.custom_filters or {}
        v_cfg = f.get("video")    or config_manager.config.get("DOWNLOAD", "select_video")
        a_cfg = f.get("audio")    or config_manager.config.get("DOWNLOAD", "select_audio")
        s_cfg = f.get("subtitle") or config_manager.config.get("DOWNLOAD", "select_subtitle")
        selector = StreamSelector(v_cfg, a_cfg, s_cfg, formatter=N3u8dlFormatter())
        self._sv, self._sa, self._ss = selector.apply(self.streams)
        logger.info(f"Selection -> video={self._sv!r}  audio={self._sa!r}  subtitle={self._ss!r}")

    def _ext_lang_matches(self, lang: str, track_type: str) -> bool:
        """Return True if the external track with the given *lang* tag should be downloaded."""
        cfg_key = "select_subtitle" if track_type == "subtitle" else "select_audio"
        cfg = config_manager.config.get("DOWNLOAD", cfg_key)
        if not cfg or cfg.lower() == "all":
            return True
        if cfg.lower() == "false":
            return False

        tokens = [t.strip().lower() for t in re.split(r"[|,]", cfg) if t.strip()]
        lang_l = lang.strip().lower()

        for token in tokens:

            # Strip flag suffixes (forced/cc/sdh/hi) to get the bare language token
            base_token = token.split("_")[0]
            if base_token in lang_l or lang_l.startswith(base_token):
                return True
            
            # ISO-639-2 three-letter -> two-letter prefix match ("ita" -> "it")
            if len(base_token) == 3 and base_token.isalpha() and lang_l.startswith(base_token[:2]):
                return True
        return False

    def _ext_track_matches(self, track: Dict, track_type: str) -> bool:
        """Return True if *track* (a full external track dict with flag fields) matches the configured selection filter, including flag requirements.
        """
        cfg_key = "select_subtitle" if track_type == "subtitle" else "select_audio"
        cfg = config_manager.config.get("DOWNLOAD", cfg_key)
        if not cfg or cfg.lower() == "all":
            return True
        if cfg.lower() == "false":
            return False

        lang = (track.get("language") or "").strip().lower()
        tokens = [t.strip().lower() for t in re.split(r"[|,]", cfg) if t.strip()]

        for token in tokens:
            parts = token.split("_")
            base_token = parts[0]
            req_flags  = {p for p in parts[1:] if p in {"forced", "cc", "sdh", "hi"}}
            if "hi" in req_flags:
                req_flags.discard("hi")
                req_flags.add("cc")

            # Language match
            lang_ok = (base_token in lang or lang.startswith(base_token) or (len(base_token) == 3 and base_token.isalpha() and lang.startswith(base_token[:2])))
            if not lang_ok:
                continue

            # Flag match: every requested flag must be present
            if req_flags:
                track_flags: set = set()
                if track.get("forced"):
                    track_flags.add("forced")
                if track.get("cc"):
                    track_flags.add("cc")
                if track.get("sdh"):
                    track_flags.add("sdh")
                if not req_flags.issubset(track_flags):
                    continue

            return True
        return False

    def _decrypt_check(self, status: Dict[str, Any]) -> None:
        if self.download_id:
            download_tracker.update_status(self.download_id, "Decrypting ...")

        decryptor = Decryptor(preference=self.decrypt_preference, license_url=self.license_url, drm_type=self.drm_type)
        keys = (self.key.get_keys_list() if isinstance(self.key, KeysManager) else ([self.key] if isinstance(self.key, str) else self.key))
        targets = []
        if status.get("video"):
            targets.append((status["video"], "video"))
        for aud in status.get("audios", []):
            targets.append((aud, "audio"))

        for target, stype in targets:
            fp = Path(target["path"])
            if not fp.exists():
                continue
            out = fp.with_suffix(fp.suffix + ".dec")
            if decryptor.decrypt(str(fp), keys, str(out), stream_type=stype):
                try:
                    fp.unlink()
                    out.rename(fp)
                    target["size"] = fp.stat().st_size
                except Exception as exc:
                    logger.error(f"Failed to replace encrypted file: {exc}")
                    if out.exists():
                        out.unlink()
            else:
                if out.exists():
                    try:
                        out.unlink()
                    except Exception:
                        pass

    def _build_status(self, ext_subs: List, ext_auds: List = None) -> Dict:
        status: Dict[str, Any] = {
            "video":              None,
            "audios":             [],
            "subtitles":          ext_subs or [],
            "external_subtitles": [],
            "external_audios":    ext_auds or [],
        }
        for f in sorted(self.output_dir.iterdir()):
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            fname_l = self.filename.lower()

            if ext in VIDEO_EXTENSIONS and f.stem.lower() == fname_l:
                if status["video"] is None:
                    status["video"] = {"path": str(f), "size": f.stat().st_size}
                continue

            if ext in AUDIO_EXTENSIONS and f.name.lower().startswith(fname_l):
                if status["video"] and Path(status["video"]["path"]).name == f.name:
                    continue
                track_name = f.stem[len(self.filename):].lstrip(".")
                status["audios"].append({"path": str(f), "name": track_name, "size": f.stat().st_size})

        return status

    def _prepare_labels(self) -> None:
        sel_video = [s for s in self.streams if s.type == "video"    and s.selected and not s.is_external]
        sel_audio = [s for s in self.streams if s.type == "audio"    and s.selected and not s.is_external]
        sel_subs  = [s for s in self.streams if s.type == "subtitle" and s.selected and not s.is_external]
        logger.info(f"Preparing labels -- {len(self.streams)} streams  type={self.manifest_type}")

        self._has_video = bool(sel_video)
        if sel_video:
            v = sel_video[0]
            codec = v.get_short_codec() or v.codecs or ""
            res = v.resolution or "main"
            parts = []
            if codec: 
                parts.append(f"[yellow]\\[{codec}][/yellow]")
            if res:   
                parts.append(f"[green]{res}[/green]")
            if v.bitrate: 
                parts.append(f"[blue]{v.bitrate_display}[/blue]")
            self._video_label = " ".join(parts)
            self._video_task_key = f"vid_{res}"
        else:
            self._video_label    = ""
            self._video_task_key = "vid_main"

        self._audio_labels    = {}
        self._audio_task_keys = []
        seen_normalized: Set[str] = set()

        for s in sel_audio:
            lang  = s.resolved_language or s.language or "und"
            codec = s.get_short_codec() or s.codecs or ""
            parts = []
            if codec: 
                parts.append(f"[yellow]\\[{codec}][/yellow]")
            parts.append(f"[bold white]{lang}[/bold white]")
            if s.bitrate: 
                parts.append(f"[blue]{s.bitrate_display}[/blue]")
            if s.default: 
                parts.append("[bold red][DEFAULT][/bold red]")
            label = " ".join(parts)

            raw = (s.language or "und").lower()
            normalized = resolve_locale(raw) if raw else ""
            task_lang  = normalized.split("-")[0].lower() if normalized else raw

            if task_lang in seen_normalized:
                logger.info(f"Audio {raw!r} already mapped as {task_lang!r} -- skip dup")
                continue
            seen_normalized.add(task_lang)

            self._audio_labels[raw] = label
            for variant in _lang_variants(normalized):
                self._audio_labels[variant] = label
            if s.id and ":" in s.id:
                self._audio_labels.setdefault(s.id.split(":")[0].lower(), label)

            self._audio_task_keys.append((task_lang, label))

        self._sub_labels = {}
        for s in sel_subs:
            label = self._sub_stream_label(s)
            raw = (s.language or "und").lower()
            name = (s.name or "").strip()
            if name:
                self._sub_labels[f"{raw}:{tmdb_client._slugify(name)}"] = label
            self._sub_labels.setdefault(raw, label)

        logger.info(f"Labels ready -- video={self._video_label!r} audio={list(self._audio_labels)[:4]}  subs={list(self._sub_labels)[:4]}")

    @staticmethod
    def _sub_stream_label(s: Stream) -> str:
        try:
            lang_raw = s.language or "und"
            sfx = re.search(r"[-_](forced|cc|sdh|hi)$", lang_raw, re.I)
            lang_sfx = sfx.group(1).lower() if sfx else ""
            forced = s.forced   or lang_sfx == "forced"
            cc = s.is_cc or lang_sfx == "cc"
            sdh = s.is_sdh or lang_sfx == "sdh"
            default  = s.default  and not forced

            lang  = s.resolved_language or lang_raw or "und"
            parts = [f"[bold white]{lang}[/bold white]"]
            flags = []
            if forced:
                flags.append("[FORCED]")
            if sdh:
                flags.append("[SDH]")
            if cc:
                flags.append("[CC]")
            if default:
                flags.append("[DEFAULT]")
            if flags:
                parts.append(f"[bold red]{' '.join(flags)}[/bold red]")

            ext = "vtt" if s.type == "subtitle" else "m4a"
            return f"[yellow]\\[{ext}][/yellow] {' '.join(parts)}"
        except Exception:
            return s.language or "und"

    def _get_prebuilt_tasks(self) -> List[Tuple[str, str]]:
        tasks: List[Tuple[str, str]] = []
        if self._has_video:
            tasks.append((self._video_task_key, f"[bold cyan]Vid[/bold cyan] {self._video_label}"))
        seen: Set[str] = set()
        for lang_code, label in self._audio_task_keys:
            key = f"aud_{lang_code}"
            if key not in seen:
                seen.add(key)
                tasks.append((key, f"[bold cyan]Aud[/bold cyan] {label}"))
        return tasks