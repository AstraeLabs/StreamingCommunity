# 17.01.25

import os
import json
import subprocess
import logging
from pathlib import Path

from rich.console import Console

from VibraVid.setup import get_ffprobe_path, get_ffmpeg_path
from VibraVid.core.utils.codec import get_short_codec


console = Console()
logger = logging.getLogger(__name__)
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


def _merge_fmt_size(nb: int) -> str:
    if nb >= 1_073_741_824:
        return f"{nb / 1_073_741_824:.2f}GB"
    if nb >= 1_048_576:
        return f"{nb / 1_048_576:.1f}MB"
    if nb >= 1_024:
        return f"{nb / 1024:.0f}KB"
    return f"{nb}B"


def _segment_number(path: Path) -> int:
    try:
        stem = path.stem
        if stem.startswith("seg_"):
            return int(stem[4:])
    except (ValueError, IndexError):
        pass
    return 999_999_999


def binary_merge_segments(paths: list[Path], output_path: Path, merge_logger: logging.Logger | None = None) -> None:
    """Merge downloaded segments using direct raw binary concatenation."""
    logger.info("Binary merge v2 ...")
    log = merge_logger or logger
    valid = [(p, _segment_number(p)) for p in paths if p.exists() and p.stat().st_size > 0]
    valid.sort(key=lambda item: item[1])
    if not valid:
        log.error("[binary_merge] No valid segments found")
        return

    log.info(f"[binary_merge] raw concat {len(valid)} segments -> {output_path.name}")
    total_written = 0
    with open(output_path, "wb") as out_f:
        for seg_path, _ in valid:
            chunk = seg_path.read_bytes()
            out_f.write(chunk)
            total_written += len(chunk)

    if output_path.exists() and output_path.stat().st_size > 0:
        log.info(f"[binary_merge] raw concat OK: {output_path.name} ({_merge_fmt_size(total_written)})")
    else:
        log.error(f"[binary_merge] output is empty or missing: {output_path}")


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
        logger.error(f"ffprobe error while reading codecs for file {file_path}: {result.stderr.strip()}")
        return []

    try:
        info = json.loads(result.stdout)
        return [{'codec_name': s.get('codec_name', '').lower(), 'codec_type': s.get('codec_type', '').lower()} for s in info.get('streams', []) if s.get('codec_name')]
    except json.JSONDecodeError:
        logger.error(f"JSON decode error while parsing ffprobe output for file {file_path}: {result.stdout.strip()}")
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
        logger.warning(f"Could not read streams from {file_path}, keeping desired extension '{desired_ext}'")
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
        logger.warning(f"Codec {codec} ({codec_type}) is not compatible with .{desired_ext}. Allowed containers: {', '.join(sorted(allowed))}")
        console.print(f"[yellow]    WARN [cyan]Codec [red]{codec} [cyan]({codec_type}) [cyan]is not compatible with [red].{desired_ext}[cyan]. Allowed containers: [red]{', '.join(sorted(allowed))}")

    # Pick the best compatible container in preferred order
    for preferred in _PREFERRED_ORDER:
        if preferred in compatible_containers:
            return preferred
    
    logger.warning(f"No fully compatible container found for file {file_path} with codecs {[s['codec_name'] for s in streams]}. Falling back to .mp4")
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
    cmd = [
        get_ffprobe_path(),
        '-v', 'error',
        '-show_packets',
        '-select_streams', 'v:0',
        '-read_intervals', '%+#1',
        '-print_format', 'json',
        file_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)

    if result.returncode != 0 or 'pts_time' not in result.stdout:
        logger.warning(f"ffprobe could not read timestamps for {file_path}: {result.stderr.strip()}")
        return True

    try:
        info = json.loads(result.stdout)
        packets = info.get('packets', [])
        for packet in packets:
            if packet.get('pts') is None or packet.get('pts') == 'N/A':
                return True
    except json.JSONDecodeError:
        logger.error(f"JSON decode error during timestamp check: {result.stdout.strip()}")
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

    if result.returncode != 0:
        logger.error(f"convert_ts_to_mp4 failed: {result.stderr}")

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
            logger.error(f"ffprobe error while extracting metadata for file {file_path}: {result.stderr.strip()}")
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