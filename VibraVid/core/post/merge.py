# 31.01.24

import os
import subprocess
import logging
from typing import List, Dict, Optional

from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.setup import binary_paths, get_ffmpeg_path
from VibraVid.source.style.tracker import  context_tracker

from .helper.video import detect_ts_timestamp_issues, convert_ts_to_mp4, resolve_compatible_extension
from .helper.audio import check_duration_v_a, has_audio
from .helper.sub import convert_subtitle
from .capture import capture_ffmpeg_real_time


console = Console()
logger = logging.getLogger(__name__)
os_type = binary_paths._detect_system()
USE_GPU = config_manager.config.get_bool("PROCESS", "use_gpu")
PARAM_VIDEO = config_manager.config.get_list("PROCESS", "param_video")
PARAM_AUDIO = config_manager.config.get_list("PROCESS", "param_audio")
PARAM_FINAL = config_manager.config.get_list("PROCESS", "param_final")
FORCE_SUBTITLE = config_manager.config.get("PROCESS", "force_subtitle")
SUBTITLE_DISPOSITION_LANGUAGE = config_manager.config.get("PROCESS", "subtitle_disposition_language")


def add_encoding_params(ffmpeg_cmd: List[str]):
    """
    Add encoding parameters to the ffmpeg command.
    
    Parameters:
        ffmpeg_cmd (List[str]): List of the FFmpeg command to modify
    """
    if PARAM_FINAL:
        ffmpeg_cmd.extend(PARAM_FINAL)
    else:
        ffmpeg_cmd.extend(PARAM_VIDEO)
        ffmpeg_cmd.extend(PARAM_AUDIO)


def detect_gpu_device_type() -> str:
    """
    Detects the GPU device type available on the system.
    
    Returns:
        str: The type of GPU device detected ('cuda', 'vaapi', 'qsv', or 'none').
    """
    try:
        if os_type == 'linux':
            result = subprocess.run(['lspci'], capture_output=True, text=True, check=True)
            output = result.stdout.lower()
        elif os_type == 'windows':
            try:
                result = subprocess.run(['wmic', 'path', 'win32_videocontroller', 'get', 'name'], capture_output=True, text=True, check=True)
                output = result.stdout.lower()

            except (subprocess.CalledProcessError, FileNotFoundError):
                # Fallback to PowerShell if wmic is not available
                try:
                    result = subprocess.run(['powershell', '-Command', 'Get-WmiObject win32_videocontroller | Select-Object -ExpandProperty Name'], capture_output=True, text=True, check=True)
                    output = result.stdout.lower()
                except (subprocess.CalledProcessError, FileNotFoundError):
                    return 'none'
                
        elif os_type == 'darwin':  # macOS
            result = subprocess.run(['system_profiler', 'SPDisplaysDataType'], capture_output=True, text=True, check=True)
            output = result.stdout.lower()

        else:
            return 'none'
        
        if 'nvidia' in output:
            return 'cuda'
        elif 'intel' in output:
            return 'vaapi'
        elif 'amd' in output or 'ati' in output:
            return 'vaapi'
        else:
            return 'none'
    except (subprocess.CalledProcessError, FileNotFoundError):
        return 'none'


def _apply_compatible_extension(video_path: str, out_path: str) -> str:
    """
    Checks codec compatibility between the source video and the desired output path extension.

    Parameters:
        - video_path (str): Source file used to probe codecs.
        - out_path (str): Desired output path.

    Returns:
        str: Output path with a guaranteed compatible extension.
    """
    base, ext = os.path.splitext(out_path)
    desired_ext = ext.lstrip('.')
    compatible_ext = resolve_compatible_extension(video_path, desired_ext)

    if compatible_ext != desired_ext:
        out_path = f"{base}.{compatible_ext}"

    return out_path


def join_video(video_path: str, out_path: str, log_path: Optional[str] = None):
    """
    Mux video file using FFmpeg.
    
    Parameters:
        - video_path (str): The path to the video file.
        - out_path (str): The path to save the output file.
        - log_path (str, optional): Path to save the FFmpeg log.

    Returns:
        tuple: (out_path, result_json)
    """
    out_path = _apply_compatible_extension(video_path, out_path)
    ffmpeg_cmd = [get_ffmpeg_path()]

    # Enabled the use of gpu
    if USE_GPU:
        gpu_type_hwaccel = detect_gpu_device_type()
        console.print(f"[yellow]FFMPEG [cyan]Detected GPU for video join: [red]{gpu_type_hwaccel}")
        ffmpeg_cmd.extend(['-hwaccel', gpu_type_hwaccel])

    # Add mpegts to force to detect input file as ts file
    if video_path.lower().endswith('.ts'):
        ffmpeg_cmd.extend(['-f', 'mpegts'])

    # Insert input video path
    ffmpeg_cmd.extend(['-i', video_path])

    # Add encoding parameters
    add_encoding_params(ffmpeg_cmd)

    # Output file and overwrite
    ffmpeg_cmd.extend([out_path, '-y'])
    logger.info(f"Running Join Video command: {' '.join(ffmpeg_cmd)}")
    result_json = capture_ffmpeg_real_time(ffmpeg_cmd, "[yellow]FFMPEG [cyan]Join video", log_path)
    if context_tracker.should_print:
        print()

    return out_path, result_json


def join_audios(video_path: str, audio_tracks: List[Dict[str, str]], out_path: str, limit_duration_diff: float = 3, log_path: Optional[str] = None):
    """
    Joins audio tracks with a video file using FFmpeg.
    
    Parameters:
        - video_path (str): The path to the video file.
        - audio_tracks (list[dict[str, str]]): A list of dictionaries containing information about audio tracks.
            Each dictionary should contain the 'path' and 'name' keys.
        - out_path (str): The path to save the output file.
        - limit_duration_diff (float): Maximum duration difference in seconds.
        - log_path (str, optional): Path to save FFmpeg log.
    """
    use_shortest = False
    
    # Check and convert audio tracks if TS with issues
    temp_audio_paths = []
    for audio_track in audio_tracks:
        audio_path = audio_track.get('path')
        if audio_path.lower().endswith('.ts') and detect_ts_timestamp_issues(audio_path):
            temp_audio_path = audio_path + '.temp.m4a'
            if convert_ts_to_mp4(audio_path, temp_audio_path):
                audio_track['path'] = temp_audio_path
                temp_audio_paths.append(temp_audio_path)
            else:
                console.print(f"[red]Failed to convert audio TS {audio_path} to M4A")
    
    for audio_track in audio_tracks:
        audio_path = audio_track.get('path')
        audio_lang = audio_track.get('name', 'unknown')
        _, diff, video_duration, audio_duration = check_duration_v_a(video_path, audio_path)
        diff_str = f"+{(video_duration - audio_duration):.2f}s" if (video_duration - audio_duration) >= 0 else f"{(video_duration - audio_duration):.2f}s"
        console.print(f"[yellow]    - [cyan]Audio lang [red]{audio_lang}, [cyan]Video: [red]{video_duration:.2f}s, [cyan]Diff: [red]{diff_str}")
        
        # If any audio track has a significant duration difference, use -shortest
        if diff > limit_duration_diff:
            console.print(f"[yellow]    WARN [cyan]Audio lang: [red]'{audio_lang}' [cyan]has a duration difference of [red]{diff:.2f}s [cyan]which exceeds the limit of [red]{limit_duration_diff}s.")
            use_shortest = True

    # Start command with locate ffmpeg
    ffmpeg_cmd = [get_ffmpeg_path()]

    # Enabled the use of gpu
    if USE_GPU:
        ffmpeg_cmd.extend(['-hwaccel', detect_gpu_device_type()])

    # Insert input video path with TS format
    if video_path.lower().endswith('.ts'):
        ffmpeg_cmd.extend(['-f', 'mpegts'])
    ffmpeg_cmd.extend(['-i', video_path])

    # Add audio tracks as input with TS format
    for i, audio_track in enumerate(audio_tracks):
        if audio_track.get('path', '').lower().endswith('.ts'):
            ffmpeg_cmd.extend(['-f', 'mpegts'])
        ffmpeg_cmd.extend(['-i', audio_track.get('path')])

    # Map the video and audio streams
    ffmpeg_cmd.extend(['-map', '0:v'])
    
    for i in range(1, len(audio_tracks) + 1):
        ffmpeg_cmd.extend(['-map', f'{i}:a'])

    # Add language metadata for each audio track
    for i, audio_track in enumerate(audio_tracks):
        lang_code = audio_track.get('name', 'unknown')
        
        # Extract language code (e.g., "ita" from "ita - Italian")
        ffmpeg_cmd.extend([f'-metadata:s:a:{i}', f'language={lang_code}'])
        ffmpeg_cmd.extend([f'-metadata:s:a:{i}', f'title={audio_track.get("name", "unknown")}'])

    # Add encoding parameters
    add_encoding_params(ffmpeg_cmd)

    # Use shortest input path if any audio track has significant difference
    if use_shortest:
        ffmpeg_cmd.extend(['-shortest', '-strict', 'experimental'])

    # Output file and overwrite
    ffmpeg_cmd.extend([out_path, '-y'])
    logger.info(f"Running Join Audio command: {' '.join(ffmpeg_cmd)}")
    result_json = capture_ffmpeg_real_time(ffmpeg_cmd, "[yellow]FFMPEG [cyan]Join audio", log_path)
    if context_tracker.should_print:
        print()

    # Clean up temp audio files
    for temp_path in temp_audio_paths:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

    return out_path, use_shortest, result_json


def join_subtitles(video_path: str, subtitles_list: List[Dict[str, str]], out_path: str, log_path: Optional[str] = None):
    """
    Joins subtitles with a video file using FFmpeg.

    Parameters:
        - video_path (str): The path to the video file.
        - subtitles_list (list[dict[str, str]]): A list of dictionaries containing information about subtitles.
            Each dictionary should contain the 'path' key with the path to the subtitle file and the 'name' key with the name of the subtitle.
        - out_path (str): The path to save the output file.
        - log_path (str, optional): Path to save FFmpeg log.
    """
    for subtitle in subtitles_list:
        original_path = subtitle['path']
        corrected_path = convert_subtitle(original_path, FORCE_SUBTITLE)
        if not corrected_path:
            corrected_path = original_path
            
        subtitle['path'] = corrected_path
    
    ffmpeg_cmd = [get_ffmpeg_path()]
    output_ext = os.path.splitext(out_path)[1].lower()
    
    # Determine default subtitle codec based on output format
    # This acts as a fallback or baseline
    if output_ext == '.mp4':
        subtitle_codec = 'mov_text'
    elif output_ext == '.mkv':
        subtitle_codec = 'srt'
    else:
        subtitle_codec = 'copy'
    
    # Add input files
    ffmpeg_cmd += ["-i", video_path]
    for subtitle in subtitles_list:
        ffmpeg_cmd += ["-i", subtitle['path']]
    
    # Add maps for video and audio streams
    ffmpeg_cmd += ["-map", "0:v"]
    if has_audio(video_path):
        ffmpeg_cmd += ["-map", "0:a"]
    
    # Add subtitle maps and metadata
    for idx, subtitle in enumerate(subtitles_list):
        sub_path = subtitle['path']
        sub_ext = os.path.splitext(sub_path)[1].lower().lstrip('.')
        lang_display = subtitle.get('lang', subtitle.get('language', 'unknown'))
        console.print(f"[yellow]    - [cyan]Subtitle lang [red]{lang_display}.{sub_ext}")
        ffmpeg_cmd += ["-map", f"{idx + 1}:s"]
        
        if output_ext == '.mp4':
            ffmpeg_cmd += [f"-c:s:{idx}", "mov_text"]

        elif output_ext == '.mkv':
            if sub_ext in ['srt', 'vtt']:
                ffmpeg_cmd += [f"-c:s:{idx}", "srt"]
            elif sub_ext in ['ass', 'ssa']:
                ffmpeg_cmd += [f"-c:s:{idx}", "ass"]
            else:
                ffmpeg_cmd += [f"-c:s:{idx}", "copy"]
        else:
            ffmpeg_cmd += [f"-c:s:{idx}", "copy"]

        ffmpeg_cmd += [f"-metadata:s:s:{idx}", f"title={lang_display}"]
        ffmpeg_cmd += [f"-metadata:s:s:{idx}", f"language={lang_display.split('-')[0].strip()}"]
    
    # For subtitles, we always use copy for video/audio
    ffmpeg_cmd.extend(['-c:v', 'copy', '-c:a', 'copy', '-c:s', subtitle_codec])
    
    # Handle disposition: set all subtitles to 0 (disabled) by default
    for idx in range(len(subtitles_list)):
        ffmpeg_cmd.extend([f'-disposition:s:{idx}', '0'])
    
    # Set disposition if matching subtitle found
    if SUBTITLE_DISPOSITION_LANGUAGE and len(subtitles_list) > 0:
        config_lang = SUBTITLE_DISPOSITION_LANGUAGE.lower().strip()
        for idx, subtitle in enumerate(subtitles_list):
            subtitle_lang = subtitle.get('language', '').lower()
            if subtitle_lang == config_lang:
                console.print(f"[yellow]    Setting disposition for subtitle: [red]{subtitle.get('language')}")
                flags = 'default'
                if '_forced' in config_lang:
                    flags += '+forced'
                ffmpeg_cmd.extend([f'-disposition:s:{idx}', flags])
                break
    
    # Overwrite
    ffmpeg_cmd += [out_path, "-y"]
    logger.info(f"Running Join Subtitle command: {' '.join(ffmpeg_cmd)}")
    result_json = capture_ffmpeg_real_time(ffmpeg_cmd, "[yellow]FFMPEG [cyan]Join subtitle", log_path)
    if context_tracker.should_print:
        print()
    
    return out_path, result_json