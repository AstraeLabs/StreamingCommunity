# 10.04.26


import re
import time
import threading
import logging
from pathlib import Path
from typing import Any, List, Optional

from rich.console import Console

from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.core.decryptor import KeysManager


console = Console()
logger = logging.getLogger("manual")


def detect_seg_ext(url: str, default: str = "ts") -> str:
    """Detect the media-segment container format from a URL path."""
    path = url.split("?")[0].lower()
    for ext in ("mp4", "m4s", "m4v", "m4a", "ts", "aac", "webm", "vtt", "srt"):
        if path.endswith(f".{ext}"):
            return ext
    return default


def safe_name(s: str, maxlen: int = 32) -> str:
    """Sanitise *s* for use as a file/directory name component."""
    cleaned = re.sub(r"[^\w\-]", "_", s or "").strip("_")
    return (cleaned or "x")[:maxlen]


def describe_key_for_log(value: Any) -> str:
    """Return a safe, non-sensitive textual description of a decryption key value."""
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


def join_interruptible(threads: List[threading.Thread], stop_event: threading.Event, poll: float = 0.25, hard_timeout: float = 7200.0) -> None:
    """
    Join *threads* in a polling loop so ``KeyboardInterrupt`` is always
    deliverable (unlike a plain ``thread.join()`` with a long timeout).

    The loop exits as soon as all threads finish, *stop_event* is set, or
    *hard_timeout* seconds elapse — whichever comes first.
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


def collect_failed_segments(dl_segs: list, downloaded_paths: list, stream_dir, default_ext: str) -> list:
    """
    Return a list of (seg_number, url) tuples for segments that were not
    successfully downloaded (missing file or zero-byte file).
    """
    downloaded_set = {
        str(p.resolve()).casefold()
        for p in (downloaded_paths or [])
        if p.exists() and p.stat().st_size > 0
    }

    failed = []
    for seg in dl_segs:
        seg_ext = detect_seg_ext(seg.get("url", ""), default=default_ext)
        if seg_ext == "m4s":
            seg_ext = "mp4"
        
        expected_path = Path(stream_dir) / f"seg_{seg['number']:05d}.{seg_ext}"
        key = str(expected_path.resolve()).casefold()
        if key not in downloaded_set:
            failed.append((seg["number"], seg.get("url", "N/A")))

    return failed


def print_failed_segments_report(failed_by_stream: list) -> None:
    """
    Print a summary of all failed segments after all progress bars are gone.
    """
    if not failed_by_stream:
        return

    console.print()
    for stream_label, failed in failed_by_stream:
        if not failed:
            continue
        
        logger.error(f"Failed segments for {stream_label!r}: {len(failed)} missing")
        console.print(f"[bold yellow]Failed segments for:[/bold yellow] [bold white]{stream_label}[/bold white] [red]({len(failed)} missing)[/red]")


class SilentDownloadBarManager(DownloadBarManager):
    """
    A no-op drop-in for ``DownloadBarManager`` that skips all Rich
    Live/Progress setup.  Used when ``show_progress=False`` is passed to
    ``MediaDownloader.start_download()``.
    """
    def __init__(self, download_id: Optional[str] = None) -> None:
        # Intentionally skip super().__init__() — we do not want Rich objects.
        self.download_id = download_id
        self.progress    = None

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
