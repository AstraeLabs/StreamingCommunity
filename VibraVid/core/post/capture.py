# 16.04.24

import re
import time
import logging
import threading
import subprocess
from typing import Optional

from rich.console import Console

from VibraVid.utils.os import internet_manager
from VibraVid.source.style.tracker import  context_tracker, download_tracker


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


def capture_output(process: subprocess.Popen, description: str, progress_data: ProgressData, log_path: Optional[str] = None, terminate_flag: threading.Event = None) -> None:
    """
    Function to capture and print output from a subprocess.

    Parameters:
        - process (subprocess.Popen): The subprocess whose output is captured.
        - description (str): Description of the command being executed.
        - progress_data (ProgressData): Object to store the last progress data.
        - log_path (Optional[str]): Path to log file to write output.
        - terminate_flag (threading.Event): Per-invocation flag to signal termination.
    """
    if terminate_flag is None:
        terminate_flag = threading.Event()
    
    try:
        max_length = 0
        start_time = time.time()

        for line in iter(process.stdout.readline, ''):          
            try:
                line = line.strip()
                logger.info(f"[ffmpeg] {line}")

                if not line:
                    continue

                # Check if termination is requested
                if terminate_flag.is_set():
                    logger.info("FFmpeg process cancelled")
                    break

                if "size=" in line:
                        try:
                            elapsed_time = time.time() - start_time
                            data = parse_output_line(line)

                            if 'q' in data:
                                is_end = (float(data.get('q', -1.0)) == -1.0)
                                size_key = 'Lsize' if is_end else 'size'
                                byte_size = int(re.findall(r'\d+', data.get(size_key, '0'))[0]) * 1000
                            else:
                                byte_size = int(re.findall(r'\d+', data.get('size', '0'))[0]) * 1000

                            # Extract additional information
                            fps = data.get('fps', 'N/A')
                            time_processed = data.get('time', 'N/A')
                            bitrate = data.get('bitrate', 'N/A')
                            speed = data.get('speed', 'N/A')

                            # Format elapsed time as HH:MM:SS
                            elapsed_formatted = internet_manager.format_time(elapsed_time, add_hours=True)

                            # Store progress data as JSON
                            json_data = {'fps': fps,'speed': speed, 'time': time_processed,'bitrate': bitrate}
                            progress_data.update(json_data)

                            if context_tracker.is_parallel_cli and context_tracker.download_id:
                                # Route progress through tracker for the Live display
                                download_tracker.update_progress(
                                    context_tracker.download_id,
                                    "ffmpeg_join",
                                    speed=f"{speed}",
                                    size=internet_manager.format_file_size(byte_size),
                                    status="joining",
                                )
                            elif context_tracker.should_print:
                                # Construct the progress string with formatted output information
                                progress_string = (
                                    f"{description}[white]: "
                                    f"([dim]fps:[/] [yellow]{fps}[/], "
                                    f"[dim]speed:[/] [yellow]{speed}[/], "
                                    f"[dim]size:[/] [yellow]{internet_manager.format_file_size(byte_size)}[/], "
                                    f"[dim]time:[/] [yellow]{time_processed}[/], "
                                    f"[dim]bitrate:[/] [yellow]{bitrate}[/], "
                                    f"[dim]elapsed:[/] [yellow]{elapsed_formatted}[/])"
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

                # Remove milliseconds from time value
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


def capture_ffmpeg_real_time(ffmpeg_command: list, description: str, log_path: Optional[str] = None) -> dict:
    """
    Function to capture real-time output from ffmpeg process.

    Parameters:
        - ffmpeg_command (list): The command to execute ffmpeg.
        - description (str): Description of the command being executed.

    Returns:
        dict: JSON dictionary with the last progress data containing
    """
    # Per-invocation terminate flag (thread-safe for parallel downloads)
    terminate_flag = threading.Event()

    # Clear the terminate_flag before starting a new capture
    terminate_flag.clear()

    # Create progress data storage
    progress_data = ProgressData()
    
    # Capture parent thread context so the output thread inherits it
    _parent_download_id = context_tracker.download_id
    _parent_is_parallel = context_tracker.is_parallel_cli

    try:

        # Start the ffmpeg process with subprocess.Popen
        process = subprocess.Popen(ffmpeg_command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)

        # Start a thread to capture and print output
        def _output_worker():
            # Propagate parent context into this child thread
            context_tracker.download_id = _parent_download_id
            context_tracker.is_parallel_cli = _parent_is_parallel
            capture_output(process, description, progress_data, log_path, terminate_flag)

        output_thread = threading.Thread(target=_output_worker)
        output_thread.start()

        try:
            # Wait for ffmpeg process to complete
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

    # Return the last captured progress data
    return progress_data.get()