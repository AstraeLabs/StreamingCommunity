# 23-01-26

import time
import threading
from typing import Dict, Any, List


class SingletonMeta(type):
    _instances = {}
    _lock = threading.Lock()

    def __call__(cls, *args, **kwargs):
        with cls._lock:
            if cls not in cls._instances:
                cls._instances[cls] = super().__call__(*args, **kwargs)
            return cls._instances[cls]


class DownloadTracker(metaclass=SingletonMeta):
    def __init__(self):
        if not hasattr(self, '_initialized'):
            self._initialized = True
            self._init_tracker()
    
    def _init_tracker(self):
        self.downloads: Dict[str, Dict[str, Any]] = {}
        self.history: List[Dict[str, Any]] = []
        self.stop_events: Dict[str, threading.Event] = {}
        self.active_processes: Dict[str, List[any]] = {} 
        self._lock = threading.Lock()
        
    def start_download(self, download_id: str, title: str, site: str, media_type: str = "Film", path: str = None):
        hook_context = None
        with self._lock:
            self.stop_events[download_id] = threading.Event()
            self.active_processes[download_id] = []
            self.downloads[download_id] = {
                "id": download_id,
                "title": title,
                "site": site,
                "type": media_type,
                "status": "starting",
                "path": path,
                "progress": 0,
                "speed": "0B/s",
                "size": "0B/0B",
                "segments": "0/0",
                "quality": "",
                "language": "",
                "start_time": time.time(),
                "last_update": time.time(),
                "tasks": {} # For multi-stream downloads (video, audio, etc)
            }
            hook_context = {
                "download_id": download_id,
                "download_title": title,
                "download_site": site,
                "download_media_type": media_type,
                "download_status": "starting",
                "download_path": path,
                "success": "",
                "download_error": "",
            }

        try:
            from VibraVid.utils.hooks import execute_hooks
            execute_hooks("pre_download", context=hook_context)
        except Exception:
            pass
            
    def update_progress(self, download_id: str, task_key: str, progress: float = None, speed: str = None, size: str = None, segments: str = None, status: str = None):
        with self._lock:
            if download_id in self.downloads:
                dl = self.downloads[download_id]
                dl["status"] = status or "downloading"
                dl["last_update"] = time.time()
                
                # Get or create task state
                if task_key not in dl["tasks"]:
                    dl["tasks"][task_key] = {
                        "progress": 0.0,
                        "speed": "0B/s",
                        "size": "0B/0B",
                        "segments": "0/0"
                    }
                
                task = dl["tasks"][task_key]
                
                # Update task fields if new values are provided
                if progress is not None:
                    try:
                        task["progress"] = float(progress)
                    except (ValueError, TypeError):
                        pass

                if speed: 
                    task["speed"] = speed
                if size: 
                    task["size"] = size
                if segments: 
                    task["segments"] = segments
                
                # Update main download state based on all active tasks
                video_audio_tasks = [t for k, t in dl["tasks"].items() if "video" in k.lower() or "audio" in k.lower() or "vid" in k.lower() or "aud" in k.lower()]
                
                if video_audio_tasks:
                    dl["progress"] = sum(t["progress"] for t in video_audio_tasks) / len(video_audio_tasks)
                    v_task = next((t for k, t in dl["tasks"].items() if "video" in k.lower() or "vid" in k.lower()), video_audio_tasks[0])
                    dl["speed"] = v_task["speed"]
                    dl["size"] = v_task["size"]
                    dl["segments"] = v_task["segments"]
                else:
                    dl["progress"] = task["progress"]
                    dl["speed"] = task["speed"]
                    dl["size"] = task["size"]
                    dl["segments"] = task["segments"]
                    
    def update_info(self, download_id: str, quality: str = None, language: str = None):
        """Update quality/language display info for a download."""
        with self._lock:
            if download_id in self.downloads:
                if quality is not None:
                    self.downloads[download_id]["quality"] = quality
                if language is not None:
                    self.downloads[download_id]["language"] = language
                self.downloads[download_id]["last_update"] = time.time()

    def update_status(self, download_id: str, status: str):
        with self._lock:
            if download_id in self.downloads:
                self.downloads[download_id]["status"] = status
                self.downloads[download_id]["last_update"] = time.time()

    def request_stop(self, download_id: str):
        """Signal a download to stop and terminate its processes."""
        with self._lock:
            if download_id in self.stop_events:
                self.stop_events[download_id].set()
            
            if download_id in self.downloads:
                self.downloads[download_id]["status"] = "cancelling..."

            # Terminate registered processes
            if download_id in self.active_processes:
                for proc in self.active_processes[download_id]:
                    try:
                        if hasattr(proc, 'terminate'):
                            proc.terminate()
                        elif hasattr(proc, 'cancel'):
                            proc.cancel()
                    except Exception:
                        pass

    def is_stopped(self, download_id: str) -> bool:
        """Check if a stop has been requested for this download."""
        with self._lock:
            event = self.stop_events.get(download_id)
            return event.is_set() if event else False

    def register_process(self, download_id: str, process: Any):
        """Register a subprocess or task to be terminated if download is cancelled."""
        with self._lock:
            if download_id and download_id in self.active_processes:
                self.active_processes[download_id].append(process)

    def shutdown(self):
        """Shutdown all active downloads and kill their processes."""
        print("Shutting down DownloadTracker, stopping all active downloads...")
        with self._lock:
            for download_id in list(self.downloads.keys()):
                self.request_stop(download_id)
            
            # Kill all registered processes
            for processes in self.active_processes.values():
                for proc in processes:
                    try:
                        if hasattr(proc, 'terminate'):
                            proc.terminate()
                        elif hasattr(proc, 'cancel'):
                            proc.cancel()
                    except Exception:
                        pass

    def complete_download(self, download_id: str, success: bool = True, error: str = None, path: str = None):
        hook_context = None
        with self._lock:
            if download_id in self.downloads:
                dl = self.downloads.pop(download_id)
                
                # Cleanup signals and processes
                self.stop_events.pop(download_id, None)
                self.active_processes.pop(download_id, None)

                dl["status"] = "completed" if success else "failed"
                if error == "cancelled":
                    dl["status"] = "cancelled"
                
                dl["end_time"] = time.time()
                dl["error"] = error
                dl["path"] = path
                dl["progress"] = 100 if success else dl["progress"]
                self.history.append(dl)
                hook_context = {
                    "download_id": dl.get("id"),
                    "download_title": dl.get("title"),
                    "download_site": dl.get("site"),
                    "download_media_type": dl.get("type"),
                    "download_status": dl.get("status"),
                    "download_path": path or dl.get("path"),
                    "success": success,
                    "download_error": error or "",
                }

                # Limit history size
                if len(self.history) > 50:
                    self.history.pop(0)

        if hook_context:
            try:
                from VibraVid.utils.hooks import execute_hooks, remember_hook_context

                if hook_context.get("success") and hook_context.get("download_path"):
                    remember_hook_context("post_run", hook_context)
                execute_hooks("post_download", context=hook_context)
            except Exception:
                pass

    def get_active_downloads(self) -> List[Dict[str, Any]]:
        with self._lock:

            # Clean up old downloads that haven't been updated for a while (e.g. 5 minutes)
            now = time.time()
            to_remove = []
            for did, dl in self.downloads.items():
                if now - dl["last_update"] > 300: # 5 minutes timeout
                    to_remove.append(did)
            
            for did in to_remove:
                dl = self.downloads.pop(did)
                dl["status"] = "timed_out"
                self.history.append(dl)
                
            return list(self.downloads.values())

    def get_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(reversed(self.history))

    def clear_history(self):
        """Clear all download history."""
        with self._lock:
            self.history.clear()


class ContextTracker:
    _global_is_gui = False

    def __init__(self):
        self.local = threading.local()
    
    @property
    def download_id(self):
        return getattr(self.local, 'download_id', None)
    
    @download_id.setter
    def download_id(self, value):
        self.local.download_id = value

    @property
    def media_type(self):
        return getattr(self.local, 'media_type', 'Film')
    
    @media_type.setter
    def media_type(self, value):
        self.local.media_type = value

    @property
    def site_name(self):
        return getattr(self.local, 'site_name', None)
    
    @site_name.setter
    def site_name(self, value):
        self.local.site_name = value

    @property
    def is_gui(self):
        return getattr(self.local, 'is_gui', self._global_is_gui)
    
    @is_gui.setter
    def is_gui(self, value):
        self.local.is_gui = value
        ContextTracker._global_is_gui = value

    @property
    def is_cancelled_callback(self):
        return getattr(self.local, 'is_cancelled_callback', None)

    @is_cancelled_callback.setter
    def is_cancelled_callback(self, value):
        self.local.is_cancelled_callback = value
        
    @property
    def is_parallel_cli(self):
        return getattr(self.local, 'is_parallel_cli', False)
    
    @is_parallel_cli.setter
    def is_parallel_cli(self, value):
        self.local.is_parallel_cli = value

    @property
    def should_print(self) -> bool:
        """Returns False when console output should be suppressed (parallel CLI or GUI)."""
        return not self.is_gui and not self.is_parallel_cli


# Global instance
download_tracker = DownloadTracker()
context_tracker = ContextTracker()