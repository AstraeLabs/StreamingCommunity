from concurrent.futures import ThreadPoolExecutor
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Any

from VibraVid.utils import config_manager
from VibraVid.source.utils.tracker import SingletonMeta, download_tracker, context_tracker

@dataclass
class DownloadJob:
    id: str                                         # Unique identifier for download job
    title: str                                      # Display name
    site: str                                       # Site name (if needed)
    media_type: str                                 # Film, TV Show, etc
    func: Callable[..., Any]                        # The function to execute for the download (download_episode/download_film/...)
    args: tuple = ()                                # Positional arguments for func
    kwargs: dict = field(default_factory=dict)       # Keyword arguments for func
    status: str = "queued"                          # "queued" | "running" | "completed" | "failed" | "cancelled"
    added_at: float = field(default_factory=time.time)  # Timestamp when enqueued

class DownloadManager(metaclass=SingletonMeta):
    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self._lock = threading.Lock()          # Protects: queue, active_jobs, _executor
            self._queue: List[DownloadJob] = []    # Jobs waiting to start
            self._active_jobs: Dict[str, DownloadJob] = {}  # Currently running, keyed by job.id
            self._completed_jobs: List[DownloadJob] = []     # Finished (for display)
            self._executor: ThreadPoolExecutor = None         # Created when start_all() is called
            self._max_workers: int = config_manager.config.get_int("DOWNLOAD", "max_concurrent_jobs", default=3)
            self._on_state_change: Callable[[], None] | None = None  # Callback for forced display refresh

    def set_on_state_change(self, callback: Callable[[], None]):
        """Register a callback that gets called whenever a job changes state."""
        self._on_state_change = callback

    def _notify_state_change(self):
        """Trigger a forced display refresh."""
        if self._on_state_change:
            self._on_state_change()
            
    def enqueue(self, job: DownloadJob):
        """Add a new download job to the queue"""
        with self._lock:
            self._queue.append(job)
        self._notify_state_change()
    
    def remove(self, job_id: str) -> bool:
        """Remove a queued (not yet running) job by ID"""
        with self._lock:
            for i, job in enumerate(self._queue):
                if job.id == job_id:
                    self._queue.pop(i)
                    return True
            return False
            
    def get_queue(self) -> List[DownloadJob]:
        """Get a snapshot of the current queue (for display)"""
        with self._lock:
            return list(self._queue)
    
    def get_active(self) -> List[DownloadJob]:
        """Get a list of active working jobs"""
        with self._lock:
            return list(self._active_jobs.values())
    
    def get_completed(self) -> List[DownloadJob]:
        """Get a list of completed jobs (for display)"""
        with self._lock:
            return list(self._completed_jobs)
        
    def is_finished(self) -> bool:
        """Check if all the jobs are completed"""
        with self._lock:
            return len(self._queue) == 0 and len(self._active_jobs) == 0

    def cancel(self, job_id: str):
        """Cancel a running job. Delegates to DownloadTracker's stop mechanism."""
        download_tracker.request_stop(job_id)

    def shutdown(self):
        """Gracefully shutdown: cancel all active, clear queue, shutdown thread pool."""
        with self._lock:
            self._queue.clear()
            
            for job_id in list(self._active_jobs.keys()):
                download_tracker.request_stop(job_id)
        
        # Tell the tracker to kill all registered subprocesses
        download_tracker.shutdown()
        
        # Shutdown the thread pool, wait for running threads to finish
        if self._executor:
            self._executor.shutdown(wait=False)
            self._executor = None

    def _run_job(self, job: DownloadJob):
        """Runs in a worker thread. Sets up context, calls the download function."""
        # 1. Set thread-local context so downloaders know who we are
        context_tracker.download_id = job.id
        context_tracker.site_name = job.site
        context_tracker.media_type = job.media_type
        context_tracker.is_parallel_cli = True
        
        # 2. Register with DownloadTracker (activates all 'if download_id:' guards)
        download_tracker.start_download(job.id, job.title, job.site, job.media_type)
        
        # 3. Move from queue to active
        with self._lock:
            if job in self._queue:
                self._queue.remove(job)
            job.status = "running"
            self._active_jobs[job.id] = job
        self._notify_state_change()
        
        # 4. Execute the download function
        try:
            job.func(*job.args, **job.kwargs)
            job.status = "completed"
            # Safety net: downloader should have called this already, but ensure it's marked
            download_tracker.complete_download(job.id, success=True)
        except Exception as e:
            job.status = "failed"
            download_tracker.complete_download(job.id, success=False, error=str(e))
        finally:
            # 5. Move from active to completed
            with self._lock:
                self._active_jobs.pop(job.id, None)
                self._completed_jobs.append(job)
            self._notify_state_change()

    def start_all(self):
        """Submit all queued jobs to the thread pool. Non-blocking."""
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="DownloadWorker"
        )
        with self._lock:
            for job in self._queue:
                self._executor.submit(self._run_job, job)


# Global instance (same pattern as download_tracker/context_tracker)
download_manager = DownloadManager()


# ---------------------------------------------------------------------------
# Monkey-patch Rich Console output to be thread-aware.
# In parallel CLI mode (is_parallel_cli=True on the worker thread),
# console.print / console.log are silenced so only the DownloadDisplay
# table is visible.  The main thread (where is_parallel_cli=False) is
# unaffected, so Rich Live rendering keeps working normally.
# ---------------------------------------------------------------------------
from rich.console import Console as _RichConsole  # noqa: E402

_original_print = _RichConsole.print
_original_log   = _RichConsole.log

def _quiet_print(self, *args, **kwargs):
    if context_tracker.should_print:
        _original_print(self, *args, **kwargs)

def _quiet_log(self, *args, **kwargs):
    if context_tracker.should_print:
        _original_log(self, *args, **kwargs)

_RichConsole.print = _quiet_print   # type: ignore[assignment]
_RichConsole.log   = _quiet_log     # type: ignore[assignment]