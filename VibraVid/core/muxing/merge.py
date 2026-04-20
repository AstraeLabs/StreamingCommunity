# 31.01.24

import os
import subprocess
import logging
from typing import List, Dict

from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.setup import binary_paths, get_ffmpeg_path
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.utils.language import resolve_iso639_2

from .helper.video import detect_ts_timestamp_issues, convert_ts_to_mp4, resolve_compatible_extension
from .helper.audio import check_duration_v_a, has_audio, get_video_duration
from .helper.sub import convert_subtitle
from .capture import capture_ffmpeg_real_time


console = Console()
logger = logging.getLogger(__name__)
USE_GPU = config_manager.config.get_bool("PROCESS", "use_gpu")
FORCE_SUBTITLE = config_manager.config.get("PROCESS", "force_subtitle")
SUBTITLE_DISPOSITION_LANGUAGE = config_manager.config.get("PROCESS", "subtitle_disposition_language")
_GPU_TYPE_CACHE = None


def _get_param_video() -> list:
    return config_manager.config.get_list("PROCESS", "param_video")


def _get_param_audio() -> list:
    return config_manager.config.get_list("PROCESS", "param_audio")


def _get_param_final() -> list:
    return config_manager.config.get_list("PROCESS", "param_final")


def detect_gpu_device_type() -> str:
    """
    Detects the GPU device type available on the system.

    Returns:
        str: The type of GPU device detected ('cuda', 'vaapi', 'qsv', or 'none').
    """
    global _GPU_TYPE_CACHE
    if _GPU_TYPE_CACHE is not None:
        return _GPU_TYPE_CACHE

    os_type = binary_paths._detect_system()

    try:
        if os_type == 'linux':
            result = subprocess.run(['lspci'], capture_output=True, text=True, check=True)
            output = result.stdout.lower()

        elif os_type == 'windows':
            try:
                result = subprocess.run(['wmic', 'path', 'win32_videocontroller', 'get', 'name'], capture_output=True, text=True, check=True)
                output = result.stdout.lower()
            except (subprocess.CalledProcessError, FileNotFoundError):
                try:
                    result = subprocess.run(['powershell', '-Command', 'Get-WmiObject win32_videocontroller | Select-Object -ExpandProperty Name'], capture_output=True, text=True, check=True)
                    output = result.stdout.lower()
                except (subprocess.CalledProcessError, FileNotFoundError):
                    return 'none'

        elif os_type == 'darwin':
            result = subprocess.run(['system_profiler', 'SPDisplaysDataType'], capture_output=True, text=True, check=True)
            output = result.stdout.lower()

        else:
            _GPU_TYPE_CACHE = 'none'
            return _GPU_TYPE_CACHE

        if 'nvidia' in output:
            _GPU_TYPE_CACHE = 'cuda'
            return _GPU_TYPE_CACHE
        elif 'intel' in output:
            _GPU_TYPE_CACHE = 'qsv'
            return _GPU_TYPE_CACHE
        elif 'amd' in output or 'ati' in output:
            _GPU_TYPE_CACHE = 'vaapi'
            return _GPU_TYPE_CACHE
        else:
            _GPU_TYPE_CACHE = 'none'
            return _GPU_TYPE_CACHE

    except (subprocess.CalledProcessError, FileNotFoundError):
        _GPU_TYPE_CACHE = 'none'
        return _GPU_TYPE_CACHE


def add_encoding_params(ffmpeg_cmd: List[str]):
    """
    Add encoding parameters to the FFmpeg command.

    Logic:
        - If PARAM_FINAL is set (non-empty), it takes full precedence (e.g. ["-c", "copy"]).
        - Otherwise, PARAM_VIDEO and PARAM_AUDIO from config are used directly.
          The user is responsible for setting the correct encoder in param_video
        - If PARAM_VIDEO or PARAM_AUDIO are empty, safe defaults are applied.

    Parameters:
        ffmpeg_cmd (List[str]): FFmpeg command list to extend in-place.
    """
    param_final = _get_param_final()
    param_video = _get_param_video()
    param_audio = _get_param_audio()

    if param_final:
        ffmpeg_cmd.extend(param_final)
        return

    if param_video:
        ffmpeg_cmd.extend(param_video)
    else:
        logger.warning("No video encoding parameters set in config. Using default: libx265 with CRF 22.")
        ffmpeg_cmd.extend(['-c:v', 'libx265', '-crf', '22', '-preset', 'medium'])

    if param_audio:
        ffmpeg_cmd.extend(param_audio)
    else:
        logger.warning("No audio encoding parameters set in config. Using default: libopus with 128k bitrate.")
        ffmpeg_cmd.extend(['-c:a', 'libopus', '-b:a', '128k'])


def _apply_compatible_extension(video_path: str, out_path: str) -> str:
    """
    Checks codec compatibility between the source video and the desired output path extension.
    If not compatible, returns the most compatible extension instead.

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


def join_video(video_path: str, out_path: str):
    """
    Mux video file using FFmpeg.

    Parameters:
        - video_path (str): The path to the video file.
        - out_path (str): The path to save the output file.

    Returns:
        tuple: (out_path, result_json)
    """
    out_path = _apply_compatible_extension(video_path, out_path)
    ffmpeg_cmd = [get_ffmpeg_path()]

    if USE_GPU:
        gpu_type_hwaccel = detect_gpu_device_type()
        console.print(f'[yellow]FFMPEG [cyan]Detected GPU for video join: [red]{gpu_type_hwaccel}')
        ffmpeg_cmd.extend(['-hwaccel', gpu_type_hwaccel])

    # Detect timestamp issues and add regeneration flag
    has_ts_issues = detect_ts_timestamp_issues(video_path)
    if has_ts_issues:
        logger.info("[join_video] Detected timestamp issues, adding -fflags +genpts")
        ffmpeg_cmd.extend(['-fflags', '+genpts+igndts+discardcorrupt', '-avoid_negative_ts', 'make_zero'])

    if video_path.lower().endswith('.ts'):
        ffmpeg_cmd.extend(['-f', 'mpegts'])

    ffmpeg_cmd.extend(['-i', video_path])
    add_encoding_params(ffmpeg_cmd)
    ffmpeg_cmd.extend([out_path, '-y'])

    total_duration = get_video_duration(video_path)
    logger.info(f"Running Join Video command: {' '.join(ffmpeg_cmd)}")
    result_json = capture_ffmpeg_real_time(ffmpeg_cmd, '[yellow]FFMPEG [cyan]Join video', total_duration)
    if context_tracker.should_print:
        print()

    return out_path, result_json


def join_audios(video_path: str, audio_tracks: List[Dict[str, str]], out_path: str, limit_duration_diff: float = 3):
    """
    Joins audio tracks with a video file using FFmpeg.

    Parameters:
        - video_path (str): The path to the video file.
        - audio_tracks (list[dict[str, str]]): A list of dicts with 'path' and 'name' keys.
        - out_path (str): The path to save the output file.
        - limit_duration_diff (float): Maximum duration difference in seconds.

    Returns:
        tuple: (out_path, use_shortest, result_json)
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
                console.print(f'[red]Failed to convert audio TS {audio_path} to M4A')

    for audio_track in audio_tracks:
        audio_path = audio_track.get('path')
        audio_lang = audio_track.get('name', 'unknown')
        _, diff, video_duration, audio_duration = check_duration_v_a(video_path, audio_path)
        diff_str = (f"+{(video_duration - audio_duration):.2f}s" if (video_duration - audio_duration) >= 0 else f"{(video_duration - audio_duration):.2f}s")
        console.print(f'[yellow]    - [cyan]Audio lang [red]{audio_lang}, [cyan]Video: [red]{video_duration:.2f}s, [cyan]Diff: [red]{diff_str}')

        if diff > limit_duration_diff:
            console.print(f'[yellow]    WARN [cyan]Audio lang: [red]{audio_lang!r} [cyan]has a duration difference of [red]{diff:.2f}s [cyan]which exceeds the limit of [red]{limit_duration_diff}s.')
            use_shortest = True

    ffmpeg_cmd = [get_ffmpeg_path()]

    if USE_GPU:
        ffmpeg_cmd.extend(['-hwaccel', detect_gpu_device_type()])

    if video_path.lower().endswith('.ts'):
        ffmpeg_cmd.extend(['-f', 'mpegts'])
    ffmpeg_cmd.extend(['-i', video_path])

    for audio_track in audio_tracks:
        if audio_track.get('path', '').lower().endswith('.ts'):
            ffmpeg_cmd.extend(['-f', 'mpegts'])
        ffmpeg_cmd.extend(['-i', audio_track.get('path')])

    ffmpeg_cmd.extend(['-map', '0:v'])
    for i in range(1, len(audio_tracks) + 1):
        ffmpeg_cmd.extend(['-map', f'{i}:a'])

    for i, audio_track in enumerate(audio_tracks):
        lang_source = audio_track.get('language') or audio_track.get('name', 'unknown')
        lang_code = resolve_iso639_2(lang_source)
        ffmpeg_cmd.extend([f'-metadata:s:a:{i}', f'language={lang_code}'])
        ffmpeg_cmd.extend([f'-metadata:s:a:{i}', f'title={audio_track.get("name", "unknown")}'])

    add_encoding_params(ffmpeg_cmd)

    if use_shortest:
        ffmpeg_cmd.extend(['-shortest', '-strict', 'experimental'])

    ffmpeg_cmd.extend([out_path, '-y'])

    total_duration = get_video_duration(video_path)
    logger.info(f"Running Join Audio command: {' '.join(ffmpeg_cmd)}")
    result_json = capture_ffmpeg_real_time(ffmpeg_cmd, '[yellow]FFMPEG [cyan]Join audio', total_duration)
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


def join_subtitles(video_path: str, subtitles_list: List[Dict[str, str]], out_path: str):
    """
    Joins subtitles with a video file using FFmpeg.

    Parameters:
        - video_path (str): The path to the video file.
        - subtitles_list (list[dict[str, str]]): A list of dicts with 'path', 'language' keys.
        - out_path (str): The path to save the output file.

    Returns:
        tuple: (out_path, result_json)
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
    if output_ext == '.mp4':
        subtitle_codec = 'mov_text'
    elif output_ext == '.mkv':
        subtitle_codec = 'srt'
    else:
        subtitle_codec = 'copy'

    ffmpeg_cmd += ['-i', video_path]
    for subtitle in subtitles_list:
        ffmpeg_cmd += ['-i', subtitle['path']]

    ffmpeg_cmd += ['-map', '0:v']
    if has_audio(video_path):
        ffmpeg_cmd += ['-map', '0:a']

    for idx, subtitle in enumerate(subtitles_list):
        sub_path = subtitle['path']
        sub_ext = os.path.splitext(sub_path)[1].lower().lstrip('.')
        lang_display = subtitle.get('lang', subtitle.get('language', 'unknown'))
        console.print(f'[yellow]    - [cyan]Subtitle lang [red]{lang_display}.{sub_ext}')
        ffmpeg_cmd += ['-map', f'{idx + 1}:s']

        if output_ext == '.mp4':
            ffmpeg_cmd += [f'-c:s:{idx}', 'mov_text']
        elif output_ext == '.mkv':
            if sub_ext in ['srt', 'vtt']:
                ffmpeg_cmd += [f'-c:s:{idx}', 'srt']
            elif sub_ext in ['ass', 'ssa']:
                ffmpeg_cmd += [f'-c:s:{idx}', 'ass']
            else:
                ffmpeg_cmd += [f'-c:s:{idx}', 'copy']
        else:
            ffmpeg_cmd += [f'-c:s:{idx}', 'copy']

        ffmpeg_cmd += [f'-metadata:s:s:{idx}', f'title={lang_display}']
        ffmpeg_cmd += [f'-metadata:s:s:{idx}', f"language={lang_display.split('-')[0].strip()}"]

    # For subtitle muxing, video and audio streams are always copied
    ffmpeg_cmd.extend(['-c:v', 'copy', '-c:a', 'copy', '-c:s', subtitle_codec])

    # Set all subtitle dispositions to disabled by default
    for idx in range(len(subtitles_list)):
        ffmpeg_cmd.extend([f'-disposition:s:{idx}', '0'])

    # Set disposition for matching subtitle language if configured
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

    ffmpeg_cmd += [out_path, '-y']

    total_duration = get_video_duration(video_path)
    logger.info(f"Running Join Subtitle command: {' '.join(ffmpeg_cmd)}")
    result_json = capture_ffmpeg_real_time(ffmpeg_cmd, '[yellow]FFMPEG [cyan]Join subtitle', total_duration)
    if context_tracker.should_print:
        print()

    return out_path, result_json