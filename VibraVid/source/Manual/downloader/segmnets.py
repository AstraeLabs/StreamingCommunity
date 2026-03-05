# 19.05.25

import os
import time
import signal
import logging
import threading

from rich.console import Console
from rich.text import Text
from rich.progress import Progress, TextColumn, ProgressColumn
from concurrent.futures import ThreadPoolExecutor, as_completed

from VibraVid.utils import config_manager
from VibraVid.utils import internet_manager
from VibraVid.utils.http_client import create_client, get_headers, get_userAgent
from VibraVid.source.utils.tracker import download_tracker, context_tracker

from ..utils.file_size import format_size


logger = logging.getLogger(__name__)
console = Console()
failed_segments = set()
failed_segments_lock = threading.Lock()
shutdown_flag = threading.Event()
TIMEOUT = config_manager.config.get_int('REQUESTS', 'timeout')
MAX_WORKERS = config_manager.config.get_int('DOWNLOAD', 'thread_count')
MAX_RETRIES = config_manager.config.get_int('REQUESTS', 'max_retry')


class CustomBarColumn(ProgressColumn):
    def __init__(self, bar_width=40):
        super().__init__()
        self.bar_width = bar_width
    
    def render(self, task):
        completed = task.completed
        total = task.total or 100
        
        bar_width = int((completed / total) * self.bar_width) if total > 0 else 0
        bar_width = min(bar_width, self.bar_width)
        
        text = Text()
        if bar_width > 0:
            text.append("█" * bar_width, style="bright_magenta")
        if bar_width < self.bar_width:
            text.append("░" * (self.bar_width - bar_width), style="dim white")
        
        return text


class ColoredSegmentColumn(ProgressColumn):
    """Segment count column with colors"""
    def render(self, task):
        segment = task.fields.get("progress", "0/0")
        if "/" in segment:
            current, total = segment.split("/")
            return Text.from_markup(f"[green]{current}[/green][dim]/[/dim][cyan]{total}[/cyan]")
        return Text(segment, style="yellow")


class ColoredSpeedColumn(ProgressColumn):
    """Speed column with green color"""
    def render(self, task):
        speed = task.fields.get("speed", "0 MB/s")
        return Text(speed, style="green")


class ColoredSizeColumn(ProgressColumn):
    """Size column with dim/green colors"""
    def render(self, task):
        size = task.fields.get("size", "0 MB / ? MB")
        if "/" in size:
            current, total = size.split("/", 1)
            return Text.from_markup(f"[dim]{current.strip()}[/dim] /[green]{total.strip()}[/green]")
        return Text(size, style="green")


class CompactTimeColumn(ProgressColumn):
    """Elapsed time column"""
    def render(self, task):
        elapsed = task.finished_time if task.finished else task.elapsed
        if elapsed is None:
            return Text("--:--", style="yellow")
        return Text(internet_manager.format_time(elapsed), style="yellow")


class CompactTimeRemainingColumn(ProgressColumn):
    """Remaining time column"""
    def render(self, task):
        remaining = task.time_remaining
        if remaining is None:
            return Text("--:--", style="cyan")
        return Text(internet_manager.format_time(remaining), style="cyan")


class SegmentDownloader:
    def __init__(self, headers=None, max_workers=MAX_WORKERS, max_retries=MAX_RETRIES, download_id=None):
        self.headers = headers or get_headers()
        self.max_workers = max_workers
        self.max_retries = max_retries
        self.download_id = download_id
        if threading.current_thread() is threading.main_thread():
            signal.signal(signal.SIGINT, self._signal_handler)
    
    def _signal_handler(self, signum, frame):
        """Handle Ctrl+C gracefully"""
        shutdown_flag.set()
        raise KeyboardInterrupt("Download cancelled by user")
    
    def is_cancelled(self):
        """Check if download should be cancelled (signal or GUI stop)"""
        return shutdown_flag.is_set() or (self.download_id and download_tracker.is_stopped(self.download_id))
    
    def download_segment(self, segment):
        if self.is_cancelled():
            return False
        
        with failed_segments_lock:
            if segment.number in failed_segments:
                logger.info(f"Skipping segment {segment.number} (globally failed)")
                return False
        
        for attempt in range(1, self.max_retries + 1):
            if self.is_cancelled():
                return False
            
            try:
                # Generate new User-Agent for each segment request
                segment_headers = self.headers.copy()
                segment_headers['User-Agent'] = get_userAgent()
                with create_client(headers=segment_headers, timeout=TIMEOUT, follow_redirects=True) as client:
                    response = client.get(segment.url)
                    response.raise_for_status()
                    
                    content = response.content
                    segment.size = len(content)
                    segment.content = content
                    segment.downloaded = True
                    
                    logger.debug(f"Downloaded segment {segment.number} ({format_size(segment.size)})")
                    return True
                    
            except Exception as e:
                logger.warning(f"Segment {segment.number} failed (attempt {attempt}/{self.max_retries}): {e}")
                
                if attempt < self.max_retries:
                    time.sleep(1 * attempt)
                else:
                    logger.error(f"Segment {segment.number} permanently failed")
                    with failed_segments_lock:
                        failed_segments.add(segment.number)
                    return False
        
        return False
    
    def download_all(self, segments, output_dir, description="segments", stream_type="media", language="und", resolution="", encryption_method=None, key_data=None, iv=None, decryptor=None):
        os.makedirs(output_dir, exist_ok=True)
        
        total_segments = len(segments)
        total_size = 0
        downloaded_count = 0
        failed_count = 0
        start_time = time.time()
        
        # Format description based on stream type
        if stream_type == "video":
            display_desc = f"[red]Video {resolution}[/red]" if resolution else "[red]Video[/red]"
        elif stream_type == "audio":
            display_desc = f"[green]Audio {language}[/green]" if language else "[green]Audio[/green]"
        elif stream_type == "subtitle":
            display_desc = f"[yellow]Subtitle {language}[/yellow]" if language else "[yellow]Subtitle[/yellow]"
        else:
            display_desc = description
        
        # Use NullContext if in GUI mode to avoid live table conflicts for GUI
        from contextlib import nullcontext
        progress_ctx = nullcontext() if context_tracker.is_gui else Progress(
            TextColumn("{task.description}"),
            CustomBarColumn(bar_width=40),
            ColoredSegmentColumn(),
            TextColumn("│"),
            ColoredSpeedColumn(),
            TextColumn("│"),
            ColoredSizeColumn(),
            CompactTimeColumn(),
            TextColumn("/"),
            CompactTimeRemainingColumn(),
            console=console,
            refresh_per_second=10.0
        )

        with progress_ctx as progress:
            task = None
            if not context_tracker.is_gui:
                task = progress.add_task(
                    display_desc,
                    total=total_segments,
                    progress=f"0/{total_segments}",
                    speed="0 MB/s",
                    size="0 MB / ? MB"
                )
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {executor.submit(self.download_segment, seg): seg for seg in segments}
                
                for future in as_completed(futures):
                    if self.is_cancelled():
                        logger.info("Download interrupted by user")
                        executor.shutdown(wait=False, cancel_futures=True)
                        return False
                    
                    segment = futures[future]
                    
                    try:
                        success = future.result()
                        if success:
                            downloaded_count += 1
                            total_size += segment.size
                            
                            if segment.type == 'init':
                                filename = 'init.m4s'
                            else:
                                filename = f"seg_{segment.number:05d}.m4s"
                            
                            # Decrypt if needed
                            if encryption_method == 'AES-128' and key_data and iv and decryptor:
                                encrypted_path = os.path.join(output_dir, f"encrypted_{filename}")
                                decrypted_path = os.path.join(output_dir, filename)
                                
                                with open(encrypted_path, 'wb') as f:
                                    f.write(segment.content)
                                
                                if decryptor.decrypt_hls_segment(encrypted_path, key_data, iv, decrypted_path):
                                    os.unlink(encrypted_path)
                                else:
                                    os.rename(encrypted_path, decrypted_path)
                            else:
                                output_path = os.path.join(output_dir, filename)
                                with open(output_path, 'wb') as f:
                                    f.write(segment.content)
                            
                            elapsed = time.time() - start_time
                            speed = total_size / elapsed if elapsed > 0 else 0
                            progress_percent = (downloaded_count / total_segments * 100) if total_segments > 0 else 0
                            speed_str = f"{format_size(speed)}/s"
                            size_str = f"{format_size(total_size)} / {format_size(total_size * total_segments / max(downloaded_count, 1))}"
                            segments_str = f"{downloaded_count}/{total_segments}"
                            
                            if not context_tracker.is_gui:
                                progress.update(task, completed=downloaded_count + failed_count, progress=segments_str, speed=speed_str, size=size_str)
                            
                            if self.download_id:
                                download_tracker.update_progress(
                                    self.download_id,
                                    description,
                                    progress=progress_percent,
                                    speed=speed_str,
                                    size=size_str,
                                    segments=segments_str
                                )
                        else:
                            failed_count += 1
                            if not context_tracker.is_gui:
                                progress.update(task, completed=downloaded_count + failed_count)
                    
                    except Exception as e:
                        logger.error(f"Error downloading segment {segment.number}: {e}")
                        failed_count += 1
                        if not context_tracker.is_gui:
                            progress.update(task, completed=downloaded_count + failed_count)
        
        elapsed = time.time() - start_time
        
        if failed_count > 0:
            console.print(f"[yellow]{failed_count} segments failed.")
        
        return failed_count == 0