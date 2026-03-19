# 17.01.25

import os
import json
import subprocess

from rich.console import Console

from VibraVid.setup import get_ffprobe_path, get_ffmpeg_path
from VibraVid.source.utils.codec import get_short_codec


console = Console()
_CODEC_CONTAINER_COMPAT = {

    # Subtitle codecs
    'eia_608':      {'mp4'},
    'eia_708':      {'mp4'},
    'mov_text':     {'mp4', 'm4v'},
    'dvd_subtitle': {'mkv', 'ts'},
    'hdmv_pgs_subtitle': {'mkv', 'ts'},
    'subrip':       {'mkv', 'mp4', 'ts'},
    'ass':          {'mkv', 'ts'},
    'webvtt':       {'mkv', 'mp4'},

    # Video codecs
    'h264':         {'mkv', 'mp4', 'ts', 'avi'},
    'hevc':         {'mkv', 'mp4', 'ts'},
    'av1':          {'mkv', 'mp4'},
    'vp9':          {'mkv', 'webm'},
    'vp8':          {'mkv', 'webm'},
    'mpeg2video':   {'mkv', 'mp4', 'ts', 'avi'},
    'mpeg4':        {'mkv', 'mp4', 'avi'},

    # Audio codecs
    'aac':          {'mkv', 'mp4', 'ts', 'm4a'},
    'mp3':          {'mkv', 'mp4', 'avi', 'ts'},
    'ac3':          {'mkv', 'mp4', 'ts'},
    'eac3':         {'mkv', 'mp4', 'ts'},
    'dts':          {'mkv', 'ts'},
    'flac':         {'mkv'},
    'opus':         {'mkv', 'webm'},
    'vorbis':       {'mkv', 'webm'},
    'pcm_s16le':    {'mkv', 'avi', 'wav'},
}
_PREFERRED_ORDER = ['mkv', 'mp4', 'ts', 'avi', 'webm']


def get_stream_codecs(file_path: str) -> list[dict]:
    """
    Returns a list of stream info dicts (codec_name, codec_type) for the given file.

    Parameters:
        - file_path (str): Path to the media file.

    Returns:
        list[dict]: e.g. [{'codec_name': 'h264', 'codec_type': 'video'}, ...]
    """
    cmd = [
        get_ffprobe_path(),
        '-v', 'error',
        '-show_streams',
        '-print_format', 'json',
        file_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.returncode != 0:
        console.print(f"[red]ffprobe error while reading codecs: {result.stderr.strip()}")
        return []

    try:
        info = json.loads(result.stdout)
        return [{'codec_name': s.get('codec_name', '').lower(), 'codec_type': s.get('codec_type', '').lower()} for s in info.get('streams', []) if s.get('codec_name')]
    except json.JSONDecodeError:
        return []


def resolve_compatible_extension(file_path: str, desired_ext: str) -> str:
    """
    Checks whether the desired output extension is compatible with all codecs in the source file. If not, returns the most compatible extension instead.

    Parameters:
        - file_path (str): Path to the source media file.
        - desired_ext (str): Desired output extension, with or without dot (e.g. 'mkv' or '.mkv').

    Returns:
        str: The extension to use (without dot), e.g. 'mkv' or 'mp4'.
    """
    desired_ext = desired_ext.lstrip('.').lower()
    streams = get_stream_codecs(file_path)

    if not streams:
        console.print(f"[yellow]    Warning: Could not read streams from {os.path.basename(file_path)}, keeping desired extension '{desired_ext}'")
        return desired_ext

    incompatible_codecs = []
    compatible_containers = set(_PREFERRED_ORDER)

    for stream in streams:
        codec = stream['codec_name']
        if codec in _CODEC_CONTAINER_COMPAT:
            allowed = _CODEC_CONTAINER_COMPAT[codec]
            if desired_ext not in allowed:
                incompatible_codecs.append((stream['codec_type'], codec, allowed))
            compatible_containers &= allowed

    # If everything is compatible with the desired extension, use it
    if not incompatible_codecs:
        return desired_ext

    # Report what's incompatible
    for codec_type, codec, allowed in incompatible_codecs:
        console.print(f"[yellow]    WARN [cyan]Codec [red]{codec} [cyan]({codec_type}) [cyan]is not compatible with [red].{desired_ext}[cyan]. Allowed containers: [red]{', '.join(sorted(allowed))}")

    # Pick the best compatible container in preferred order
    for preferred in _PREFERRED_ORDER:
        if preferred in compatible_containers:
            return preferred
    
    console.print("[yellow]    WARN Could not find a fully compatible container, falling back to mp4")
    return 'mp4'


def detect_ts_timestamp_issues(file_path):
    """
    Detect if a TS file has timestamp issues by checking for unset timestamps.

    Parameters:
        - file_path (str): Path to the TS file.

    Returns:
        bool: True if timestamp issues are detected, False otherwise.
    """
    cmd = [get_ffprobe_path(), '-v', 'error', '-show_packets', '-select_streams', 'v:0', '-read_intervals', '0%+#1', '-print_format', 'json', file_path]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    
    if result.returncode != 0 or 'pts_time' not in result.stdout:
        return True  # Assume issues if probe fails or no pts_time
    
    # Parse JSON and check for packets without pts
    try:
        info = json.loads(result.stdout)
        packets = info.get('packets', [])
        for packet in packets:
            if packet.get('pts') is None or packet.get('pts') == 'N/A':
                return True
    except json.JSONDecodeError:
        return True
    
    return False


def convert_ts_to_mp4(input_path, output_path):
    """
    Convert a TS file to MP4 to regenerate timestamps.

    Parameters:
        - input_path (str): Path to the input TS file.
        - output_path (str): Path to the output MP4 file.

    Returns:
        bool: True if conversion succeeded, False otherwise.
    """
    cmd = [
        get_ffmpeg_path(),
        '-fflags', '+genpts+igndts+discardcorrupt',
        '-avoid_negative_ts', 'make_zero',
        '-i', input_path,
        '-c', 'copy',
        '-y', output_path
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return result.returncode == 0

def get_media_metadata(file_path: str) -> dict:
    """
    Extract quality (resolution), languages, and codecs from a media file using ffprobe.
    
    Returns:
        dict: {'quality': str, 'language': str, 'video_codec': str, 'audio_codec': str}
    """
    cmd = [get_ffprobe_path(), '-v', 'error', '-show_streams', '-print_format', 'json', file_path]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            return {"quality": "", "language": "", "video_codec": "", "audio_codec": ""}
            
        info = json.loads(result.stdout)
        streams = info.get('streams', [])
        quality_val = ""
        vcodec_val = ""

        for s in streams:
            if s.get('codec_type') == 'video':
                height = s.get('height')
                if height:
                    if height >= 2160: 
                        quality_val = "2160p"
                    elif height >= 1440: 
                        quality_val = "1440p"
                    elif height >= 1080: 
                        quality_val = "1080p"
                    elif height >= 720: 
                        quality_val = "720p"
                    elif height >= 480: 
                        quality_val = "480p"
                    else: 
                        quality_val = f"{height}p"
                
                raw_vcodec = s.get('codec_name', '')
                vcodec_val = get_short_codec("video", raw_vcodec)
                break
        
        languages_found = []
        acodecs_found = []
        for s in streams:
            if s.get('codec_type') == 'audio':
                lang = s.get('tags', {}).get('language')
                if lang:
                    lang = lang.upper()
                    if lang not in languages_found:
                        languages_found.append(lang)
                
                raw_acodec = s.get('codec_name', '')
                short_acodec = get_short_codec("audio", raw_acodec)
                if short_acodec and short_acodec not in acodecs_found:
                    acodecs_found.append(short_acodec)
        
        return {
            "quality": quality_val,
            "language": "-".join(languages_found) if languages_found else "",
            "video_codec": vcodec_val,
            "audio_codec": "-".join(acodecs_found) if acodecs_found else ""
        }
        
    except Exception:
        return {"quality": "", "language": "", "video_codec": "", "audio_codec": ""}