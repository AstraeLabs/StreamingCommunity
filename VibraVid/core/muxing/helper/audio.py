# 16.04.24

import os
import json
import subprocess
import logging

from rich.console import Console

from VibraVid.setup import get_ffprobe_path


console = Console()
logger = logging.getLogger(__name__)


def has_audio(file_path: str) -> bool:
    """Check if a media file has an audio stream using FFprobe."""
    try:
        ffprobe_cmd = [get_ffprobe_path(), '-v', 'error', '-show_streams', '-print_format', 'json', file_path]
        with subprocess.Popen(ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
            stdout, stderr = proc.communicate()

            if proc.returncode != 0:
                logger.error(f"Error has_audio: {stderr}")
                return False

            probe_result = json.loads(stdout)
            streams = probe_result.get('streams', [])
            for stream in streams:
                if stream.get('codec_type') == 'audio':
                    return True
            
            logger.info(f"No audio stream found in file: {file_path}")
            return False
        
    except Exception as e:
        logger.error(f"Exception in has_audio: {e}")
        return False


def get_video_duration(file_path: str, file_type: str = "file") -> float:
    """Get the duration of a media file (video or audio)."""
    if not os.path.exists(file_path):
        logger.error(f"[get_video_duration] File not found: {file_path}")
        return None
    
    if os.path.getsize(file_path) == 0:
        logger.error(f"[get_video_duration] File is empty: {file_path}")
        return None
    
    ffprobe_cmd = [get_ffprobe_path(), '-v', 'error', '-show_format', '-print_format', 'json', file_path]
    with subprocess.Popen(ffprobe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
        stdout, stderr = proc.communicate()

        if proc.returncode != 0:
            logger.error(f"Error get_video_duration: {stderr}")
            return None

        # Parse JSON output
        probe_result = json.loads(stdout)

        # Extract duration from the media information
        try:
            return float(probe_result['format']['duration'])
        except Exception:
            logger.error(f"Error extracting duration from ffprobe output: {probe_result}")
            return 1


def check_duration_v_a(video_path, audio_path, tolerance=1.0):
    """
    Check if the duration of the video and audio matches.

    Returns:
        - tuple: (bool, float, float, float) -> 
            - Bool: True if the duration of the video and audio matches within tolerance
            - Float: Difference in duration
            - Float: Video duration
            - Float: Audio duration
    """
    video_duration = get_video_duration(video_path, file_type="video")
    audio_duration = get_video_duration(audio_path, file_type="audio")

    # Check if either duration is None and specify which one is None
    if video_duration is None and audio_duration is None:
        console.print("[yellow]Warning: Both video and audio durations are None. Returning 0 as duration difference.")
        logger.warning(f"Both video and audio durations are None for files: {video_path}, {audio_path}")
        return False, 0.0, 0.0, 0.0
    
    elif video_duration is None:
        console.print("[yellow]Warning: Video duration is None. Using audio duration for calculation.")
        logger.warning(f"Video duration is None for file: {video_path}. Using audio duration: {audio_duration} for calculation.")
        return False, 0.0, 0.0, audio_duration
    
    elif audio_duration is None:
        console.print("[yellow]Warning: Audio duration is None. Using video duration for calculation.")
        logger.warning(f"Audio duration is None for file: {audio_path}. Using video duration: {video_duration} for calculation.")
        return False, 0.0, video_duration, 0.0
    
    # Calculate the duration difference
    duration_difference = abs(video_duration - audio_duration)

    # Check if the duration difference is within the tolerance
    if duration_difference <= tolerance:
        return True, duration_difference, video_duration, audio_duration
    else:
        return False, duration_difference, video_duration, audio_duration