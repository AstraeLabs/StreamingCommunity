# 09.06.24

import os
import time
import signal
import logging
from functools import partial
import threading

from rich.console import Console
from rich.prompt import Prompt
from rich.progress import Progress, TextColumn

from VibraVid.utils.http_client import create_client, get_userAgent
from VibraVid.utils import config_manager, os_manager, internet_manager
from VibraVid.cli.run import execute_hooks
from VibraVid.source.N_m3u8 import CustomBarColumn
from VibraVid.core.post.helper.nfo import create_nfo
from VibraVid.source.utils.tracker import download_tracker, context_tracker


msg = Prompt()
console = Console()
REQUEST_VERIFY = config_manager.config.get_bool('REQUESTS', 'verify')
CREATE_NFO_FILES = config_manager.config.get_bool('PROCESS', 'generate_nfo', default=False)
SKIP_DOWNLOAD = config_manager.config.get_bool('DOWNLOAD', 'skip_download')


class InterruptHandler:
    def __init__(self):
        self.interrupt_count = 0
        self.last_interrupt_time = 0
        self.kill_download = False
        self.force_quit = False


def signal_handler(signum, frame, interrupt_handler, original_handler):
    """Enhanced signal handler for multiple interrupt scenarios"""
    current_time = time.time()
    
    # Reset counter if more than 2 seconds have passed since last interrupt
    if current_time - interrupt_handler.last_interrupt_time > 2:
        interrupt_handler.interrupt_count = 0
    
    interrupt_handler.interrupt_count += 1
    interrupt_handler.last_interrupt_time = current_time

    if interrupt_handler.interrupt_count == 1:
        interrupt_handler.kill_download = True
        console.print("\n[yellow]First interrupt received. Download will complete and save. Press Ctrl+C three times quickly to force quit.")
    
    elif interrupt_handler.interrupt_count >= 3:
        interrupt_handler.force_quit = True
        console.print("\n[red]Force quit activated. Saving partial download...")
        signal.signal(signum, original_handler)


def MP4_Downloader(url: str, path: str, referer: str = None, headers_: dict = None, show_final_info: bool = True, download_id: str = None, site_name: str = None):
    """
    Downloads an MP4 video with enhanced interrupt handling.
    - Single Ctrl+C: Completes download gracefully
    - Triple Ctrl+C: Saves partial download and exits
    """
    url = str(url).strip()
    path = os_manager.get_sanitize_path(path)
    
    # Get tracking IDs from context if not provided
    download_id = download_id or context_tracker.download_id
    site_name = site_name or context_tracker.site_name
    media_type = context_tracker.media_type or "Film"

    if SKIP_DOWNLOAD:
        console.print("[yellow]Download skipped due to configuration. Returning intended file path.")
        return path, False

    if os.path.exists(path):
        console.print("[yellow]File already exists.")
        return path, False

    if not (url.lower().startswith('http://') or url.lower().startswith('https://')):
        logging.error(f"Invalid URL: {url}")
        console.print(f"[red]Invalid URL: {url}")
        return None, False

    # Start tracking in GUI
    if download_id:
        filename = os.path.basename(path)
        download_tracker.start_download(download_id, filename, site_name or "Unknown", media_type, path=os.path.abspath(path))
        download_tracker.update_status(download_id, "Downloading ...")

    # Set headers
    headers = {}
    if referer:
        headers['Referer'] = referer
    
    if headers_:
        headers.update(headers_)
    else:
        headers['User-Agent'] = get_userAgent()

    # Set interrupt handler (only in main thread)
    temp_path = f"{path}.temp"
    interrupt_handler = InterruptHandler()

    try:
        if threading.current_thread() is threading.main_thread():
            previous_handler = signal.getsignal(signal.SIGINT)
            signal.signal(
                signal.SIGINT,
                partial(
                    signal_handler,
                    interrupt_handler=interrupt_handler,
                    original_handler=previous_handler,
                ),
            )

    except Exception:
        pass

    # Ensure the output directory exists
    os.makedirs(os.path.dirname(path), exist_ok=True)

    with create_client() as client:
        try:
            head = client.head(url, headers=headers)
            head.raise_for_status()
            content_type = (head.headers.get('content-type') or '').lower()
        except Exception:
            content_type = ''

        # If HEAD indicates HTML/JSON, attempt a GET without Range/If-Range as fallback
        if 'text/html' in content_type or 'application/json' in content_type:
            console.print('[yellow]HEAD indicates non-video; retrying GET without Range/If-Range...')

            try:
                resp_check = client.get(url, headers=headers)
                resp_check.raise_for_status()
                preview_text = None

                try:
                    preview = resp_check.content[:2000]
                    preview_text = preview.decode('utf-8', errors='replace')
                except Exception:
                    preview_text = '<could not read body>'
                    return None, False
                
                console.print("\n[red]--- body preview ---")
                console.print(preview_text)
                return None, False

            except Exception as e:
                console.print(f"[red]Fallback GET failed: {e}")
                return None, False

        # Open the streaming response using the effective headers
        with client.stream("GET", url, headers=headers) as response:
            response.raise_for_status()

            # Respect content-length when provided; otherwise treat as unknown (streaming/chunked)
            content_length = response.headers.get('content-length')
            try:
                total = int(content_length) if content_length is not None else None
            except Exception:
                total = None

            if total is None:
                console.print("[yellow]No Content-Length received; streaming until peer closes connection.")
 
            start_time = time.time()
            downloaded = 0
            incomplete_error = False

            # Use NullContext if in GUI mode to avoid live table conflicts for GUI
            from contextlib import nullcontext
            progress_ctx = nullcontext() if context_tracker.is_gui else Progress(
                TextColumn("[yellow]MP4[/yellow] [cyan]Downloading[/cyan]: "),
                CustomBarColumn(),
                TextColumn("[bright_green]{task.fields[downloaded]}[/bright_green] [bright_magenta]{task.fields[downloaded_unit]}[/bright_magenta][dim]/[/dim][bright_cyan]{task.fields[total_size]}[/bright_cyan] [bright_magenta]{task.fields[total_unit]}[/bright_magenta]"),
                TextColumn("[dim]\\\\[[/dim][bright_yellow]{task.fields[elapsed]}[/bright_yellow][dim] < [/dim][bright_cyan]{task.fields[eta]}[/bright_cyan][dim]][/dim]"),
                TextColumn("[bright_magenta]@[/bright_magenta]"),
                TextColumn("[bright_cyan]{task.fields[speed]}[/bright_cyan]"),
                console=console,
                refresh_per_second=10.0
            )

            with progress_ctx as progress_bars:
                if not context_tracker.is_gui:
                    if total:
                        total_size_value, total_size_unit = internet_manager.format_file_size(total).split(" ")
                        task_total = total
                    else:
                        total_size_value, total_size_unit = "--", ""
                        task_total = None

                    task_id = progress_bars.add_task("download", total=task_total, downloaded="0.00", downloaded_unit="B", total_size=total_size_value, total_unit=total_size_unit, elapsed="0s", eta="--", speed="-- B/s")

                with open(temp_path, 'wb') as file:
                    try:
                        for chunk in response.iter_bytes(chunk_size=65536):
                            if interrupt_handler.force_quit or (download_id and download_tracker.is_stopped(download_id)):
                                console.print("\n[red]Force quitting... Saving partial download.")
                                if download_id and download_tracker.is_stopped(download_id):
                                    incomplete_error = "cancelled"
                                break

                            if chunk:
                                size = file.write(chunk)
                                downloaded += size

                                # Calculate stats
                                elapsed = time.time() - start_time
                                elapsed_str = internet_manager.format_time(elapsed)

                                # Calculate speed and ETA (only if total known)
                                if elapsed > 0:
                                    speed = downloaded / elapsed
                                    speed_str = internet_manager.format_transfer_speed(speed)
                                else:
                                    speed_str = "-- B/s"

                                if total:
                                    remaining_bytes = max(total - downloaded, 0)
                                    eta_seconds = remaining_bytes / speed if (elapsed > 0 and speed > 0) else 0
                                    eta_str = internet_manager.format_time(eta_seconds)
                                else:
                                    eta_str = "--"

                                # Format downloaded size
                                downloaded_value, downloaded_unit = internet_manager.format_file_size(downloaded).split(" ")
                                
                                # GUI Update
                                if download_id:
                                    percent = (downloaded / total * 100) if total else 0
                                    total_size_str = f"{(total / 1024 / 1024):.2f}MB" if total else "Unknown"
                                    download_tracker.update_progress(
                                        download_id, 
                                        "video", 
                                        progress=percent, 
                                        speed=speed_str, 
                                        size=f"{downloaded_value}{downloaded_unit}/{total_size_str if total else '??'}"
                                    )

                                # Update progress if not GUI
                                if not context_tracker.is_gui:
                                    progress_bars.update(
                                        task_id,
                                        completed=downloaded,
                                        downloaded=downloaded_value,
                                        downloaded_unit=downloaded_unit,
                                        elapsed=elapsed_str,
                                        eta=eta_str,
                                        speed=speed_str
                                    )

                    except (KeyboardInterrupt):
                        if not interrupt_handler.force_quit:
                            interrupt_handler.kill_download = True
                            
                    except Exception as e:
                        incomplete_error = True
                        interrupt_handler.kill_download = True
                        console.print(f"\n[red]Download error: {e}. Saving partial download.")

                    finally:
                        try:
                            file.flush()
                            os.fsync(file.fileno())
                        except Exception:
                            pass
                
    if os.path.exists(temp_path):
        if incomplete_error == "cancelled":
            if download_id:
                download_tracker.complete_download(download_id, success=False, error="cancelled")
            return None, True

        last_exc = None
        for attempt in range(10):
            try:
                os.replace(temp_path, path)
                last_exc = None
                break

            except PermissionError as e:
                last_exc = e
                console.log(f"[yellow]Rename attempt {attempt+1}/10 failed: {e}")
                time.sleep(0.5)
                import gc
                gc.collect()

        if last_exc:
            console.print(f"[red]Could not rename temp file after retries: {last_exc}")
            return None, interrupt_handler.kill_download
 
    if os.path.exists(path):
        if show_final_info:
            file_size = internet_manager.format_file_size(os.path.getsize(path))
            console.print("\n[green]Output:")
            console.print(f"  [cyan]Path: [red]{os.path.abspath(path)}")
            console.print(f"  [cyan]Size: [red]{file_size}")

            if incomplete_error or (total and os.path.getsize(path) < total):
                console.print("[yellow]Warning: download was incomplete (partial file saved).")

        if CREATE_NFO_FILES:
            create_nfo(path)

        if download_id:
            abs_path = os.path.abspath(path)
            download_tracker.complete_download(download_id, success=True, path=abs_path)

        execute_hooks('post_run')
        return path, interrupt_handler.kill_download
    
    else:
        console.print("[red]Download failed or file is empty.")
        if download_id:
            download_tracker.complete_download(download_id, success=False, error="File missing or empty")
        return None, interrupt_handler.kill_download