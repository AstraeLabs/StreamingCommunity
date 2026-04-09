# 16.04.24

import re
import logging
import threading
import subprocess
from typing import Optional

from rich.console import Console

from VibraVid.utils.os import internet_manager
from VibraVid.core.ui.tracker import context_tracker, download_tracker


console = Console()
logger = logging.getLogger(__name__)
terminate_flag = threading.Event()


class ProgressData:
    """Class to store the last progress data"""
    def __init__(self):
        self.last_data = None
        self.lock = threading.Lock()

    def update(self, data):
        with self.lock:
            self.last_data = data

    def get(self):
        with self.lock:
            return self.last_data


def _parse_time_to_seconds(time_str: str) -> Optional[float]:
    """Convert HH:MM:SS time string to seconds."""
    try:
        parts = time_str.strip().split(':')
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + int(s)
    except Exception:
        pass
    return None


def _format_eta(eta_seconds: float) -> str:
    """Format ETA seconds into a human-readable string."""
    eta_seconds = max(0, int(eta_seconds))
    h = eta_seconds // 3600
    m = (eta_seconds % 3600) // 60
    s = eta_seconds % 60

    if h > 0:
        return f"{h}h {m:02d}m"
    elif m > 0:
        return f"{m}m {s:02d}s"
    else:
        return f"{s}s"


def capture_output(process: subprocess.Popen, description: str, progress_data: ProgressData, terminate_flag: threading.Event = None, total_duration: Optional[float] = None) -> None:
    """
    Function to capture and print output from a subprocess.

    Parameters:
        - process (subprocess.Popen): The subprocess whose output is captured.
        - description (str): Description of the command being executed.
        - progress_data (ProgressData): Object to store the last progress data.
        - log_path (Optional[str]): Path to log file to write output.
        - terminate_flag (threading.Event): Per-invocation flag to signal termination.
        - total_duration (Optional[float]): Total video duration in seconds, used to compute ETA.
    """
    if terminate_flag is None:
        terminate_flag = threading.Event()

    try:
        max_length = 0

        for line in iter(process.stdout.readline, ''):
            try:
                line = line.strip()
                logger.info(f"[ffmpeg] {line}")

                if not line:
                    continue

                if terminate_flag.is_set():
                    logger.info("FFmpeg process cancelled")
                    break

                if "size=" in line:
                    try:
                        data = parse_output_line(line)

                        if 'q' in data:
                            is_end = (float(data.get('q', -1.0)) == -1.0)
                            size_key = 'Lsize' if is_end else 'size'
                            byte_size = int(re.findall(r'\d+', data.get(size_key, '0'))[0]) * 1000
                        else:
                            byte_size = int(re.findall(r'\d+', data.get('size', '0'))[0]) * 1000

                        speed   = data.get('speed', 'N/A')
                        bitrate = data.get('bitrate', 'N/A')
                        time_processed = data.get('time', 'N/A')

                        # Compute ETA from total_duration and time already processed
                        eta_str = 'N/A'
                        if total_duration and total_duration > 0:
                            processed_sec = _parse_time_to_seconds(time_processed)
                            if processed_sec is not None and processed_sec > 0:
                                remaining_sec = total_duration - processed_sec
                                eta_str = _format_eta(remaining_sec)

                        json_data = {
                            'speed': speed,
                            'bitrate': bitrate,
                            'time': time_processed,
                            'eta': eta_str,
                        }
                        progress_data.update(json_data)

                        if context_tracker.is_parallel_cli and context_tracker.download_id:
                            download_tracker.update_progress(
                                context_tracker.download_id,
                                "ffmpeg_join",
                                speed=f"{speed}",
                                size=internet_manager.format_file_size(byte_size),
                                status="joining",
                            )
                        elif context_tracker.should_print:
                            progress_string = (
                                f"{description}[white]: "
                                f"([dim]speed:[/] [yellow]{speed}[/], "
                                f"[dim]size:[/] [yellow]{internet_manager.format_file_size(byte_size)}[/], "
                                f"[dim]bitrate:[/] [yellow]{bitrate}[/], "
                                f"[dim]ETA:[/] [yellow]{eta_str}[/])"
                            )
                            max_length = max(max_length, len(progress_string))
                            console.print(progress_string.ljust(max_length), end="\r")

                    except Exception as e:
                        logging.error(f"Error parsing output line: {line} - {e}")

            except Exception as e:
                logging.error(f"Error processing line from subprocess: {e}")

    except Exception as e:
        logging.error(f"Error in capture_output: {e}")

    finally:
        try:
            terminate_process(process)
        except Exception as e:
            logging.error(f"Error terminating process: {e}")


def parse_output_line(line: str) -> dict:
    """
    Function to parse the output line and extract relevant information.

    Parameters:
        - line (str): The output line to parse.

    Returns:
        dict: A dictionary containing parsed information.
    """
    try:
        data = {}
        parts = line.replace("  ", "").replace("= ", "=").split()

        for part in parts:
            key_value = part.split('=')

            if len(key_value) == 2:
                key = key_value[0]
                value = key_value[1]

                if key == 'time' and isinstance(value, str) and '.' in value:
                    value = value.split('.')[0]
                data[key] = value

        return data

    except Exception as e:
        logging.error(f"Error parsing line: {line} - {e}")
        return {}


def terminate_process(process):
    """
    Function to terminate a subprocess if it's still running.

    Parameters:
        - process (subprocess.Popen): The subprocess to terminate.
    """
    try:
        if process.poll() is None:
            process.kill()
    except Exception as e:
        logging.error(f"Failed to terminate process: {e}")


def capture_ffmpeg_real_time(ffmpeg_command: list, description: str, total_duration: Optional[float] = None) -> dict:
    """
    Function to capture real-time output from ffmpeg process.

    Parameters:
        - ffmpeg_command (list): The command to execute ffmpeg.
        - description (str): Description of the command being executed.
        - total_duration (Optional[float]): Total video duration in seconds, used to compute ETA.

    Returns:
        dict: JSON dictionary with the last progress data.
    """
    terminate_flag = threading.Event()
    terminate_flag.clear()

    progress_data = ProgressData()

    _parent_download_id = context_tracker.download_id
    _parent_is_parallel = context_tracker.is_parallel_cli

    try:
        process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)

        def _output_worker():
            context_tracker.download_id = _parent_download_id
            context_tracker.is_parallel_cli = _parent_is_parallel
            capture_output(process, description, progress_data, terminate_flag, total_duration)

        output_thread = threading.Thread(target=_output_worker)
        output_thread.start()

        try:
            process.wait()
        except KeyboardInterrupt:
            logging.error("Terminating ffmpeg process...")
        except Exception as e:
            logging.error(f"Error in ffmpeg process: {e}")
        finally:
            terminate_flag.set()
            output_thread.join()

    except Exception as e:
        logging.error(f"Failed to start ffmpeg process: {e}")

    return progress_data.get()