# 16.04.24

import os
import base64
import json
import logging
import subprocess
import urllib.request
from pathlib import Path
from typing import List, Optional

from mutagen.mp4 import MP4, MP4Cover
from mutagen.flac import FLAC, Picture
from mutagen.oggopus import OggOpus
from mutagen.id3 import (
    ID3, ID3NoHeaderError,
    TIT2, TPE1, TALB, TDRC, TRCK, APIC, TCON,
)
from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.setup import get_ffmpeg_path, get_ffprobe_path
from VibraVid.core.muxing.capture import capture_ffmpeg_real_time


console = Console()
logger = logging.getLogger(__name__)
ffmpeg_params = config_manager.config.get_list("PROCESS", "param_song_ffmpeg", default=None)

_CODEC_TO_EXT: dict[str, str] = {
    'aac':        'm4a',
    'libmp3lame': 'mp3',
    'flac':       'flac',
    'libopus':    'opus',
    'libvorbis':  'ogg',
    'alac':       'm4a',
}

_AUDIO_CODEC_EXT = {
    "flac": "flac",
    "alac": "m4a", 
    "aac": "m4a", 
    "mp4a": "m4a", 
    "aac_latm": "m4a",
    "ac3": "ac3", 
    "ac-3": "ac3",
    "eac3": "eac3", 
    "ec-3": "eac3",
    "opus": "opus",
    "vorbis": "ogg",
    "mp3": "mp3", 
    "mp3float": "mp3",
    "dts": "dts", 
    "dca": "dts",
    "pcm_s16le": "wav", 
    "pcm_s24le": "wav",
}


def audio_ext_for_codec(codec: str) -> Optional[str]:
    """Mappa un codec audio (es. 'flac') all'estensione container nativa ('flac')."""
    if not codec:
        return None
    return _AUDIO_CODEC_EXT.get(codec.strip().lower())


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


# ------------ Tagging
def _fetch_cover(url: str) -> Optional[bytes]:
    """Download cover image bytes from a URL."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.read()
    except Exception as e:
        logger.warning(f"Could not fetch cover: {e}")
        return None


def _tag_flac(path: Path, title: str, artist: str, album: str, year: str, track_number: Optional[int], genre: str, cover_url: Optional[str]) -> None:
    """Write Vorbis comment tags + cover art to a FLAC file."""
    audio = FLAC(str(path))

    audio['title']  = [title]
    audio['artist'] = [artist]

    if album:
        audio['album'] = [album]
    if year:
        audio['date'] = [str(year)]
    if track_number is not None:
        audio['tracknumber'] = [str(track_number)]
    if genre:
        audio['genre'] = [genre]

    if cover_url:
        cover_data = _fetch_cover(cover_url)
        if cover_data:
            pic = Picture()
            pic.type = 3            # 3 = Cover (front)
            pic.mime = 'image/jpeg'
            pic.desc = 'Cover'
            pic.data = cover_data
            audio.clear_pictures()
            audio.add_picture(pic)
            logger.info("Cover art embedded (FLAC): %s", cover_url)
        else:
            logger.warning("Cover download returned empty bytes (FLAC)")

    audio.save()


def _tag_mp4(path: Path, title: str, artist: str, album: str, year: str, track_number: Optional[int], genre: str, cover_url: Optional[str]) -> None:
    """Write atom tags + cover art to an M4A/MP4/AAC file."""
    audio = MP4(str(path))
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags

    tags['\xa9nam'] = [title]
    tags['\xa9ART'] = [artist]

    if album:
        tags['\xa9alb'] = [album]
    if year:
        tags['\xa9day'] = [str(year)]
    if track_number is not None:
        tags['trkn'] = [(int(track_number), 0)]
    if genre:
        tags['\xa9gen'] = [genre]

    if cover_url:
        cover_data = _fetch_cover(cover_url)
        if cover_data:
            tags['covr'] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
            logger.info("Cover art embedded (MP4): %s", cover_url)
        else:
            logger.warning("Cover download returned empty bytes (MP4)")

    audio.save()


def _tag_opus(path: Path, title: str, artist: str, album: str, year: str, track_number: Optional[int], genre: str, cover_url: Optional[str]) -> None:
    """Write Vorbis comment tags + cover art to an Opus file."""
    audio = OggOpus(str(path))
    if audio.tags is None:
        audio.add_tags()

    audio['title'] = [title]
    audio['artist'] = [artist]
    if album:
        audio['album'] = [album]
    if year:
        audio['date'] = [str(year)]
    if track_number is not None:
        audio['tracknumber'] = [str(track_number)]
    if genre:
        audio['genre'] = [genre]

    if cover_url:
        cover_data = _fetch_cover(cover_url)
        if cover_data:
            pic = Picture()
            pic.type = 3
            pic.mime = 'image/jpeg'
            pic.desc = 'Cover'
            pic.data = cover_data
            audio['metadata_block_picture'] = [base64.b64encode(pic.write()).decode('ascii')]
            logger.info("Cover art embedded (Opus): %s", cover_url)
        else:
            logger.warning("Cover download returned empty bytes (Opus)")

    audio.save()


def _tag_mp3(path: Path, title: str, artist: str, album: str, year: str, track_number: Optional[int], genre: str, cover_url: Optional[str]) -> None:
    """Write ID3 tags + cover art to an MP3 file."""
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()

    tags.delall('TIT2')
    tags['TIT2'] = TIT2(encoding=3, text=title)
    tags.delall('TPE1')
    tags['TPE1'] = TPE1(encoding=3, text=artist)

    if album:
        tags.delall('TALB')
        tags['TALB'] = TALB(encoding=3, text=album)
    if year:
        tags.delall('TDRC')
        tags['TDRC'] = TDRC(encoding=3, text=str(year))
    if track_number is not None:
        tags.delall('TRCK')
        tags['TRCK'] = TRCK(encoding=3, text=str(track_number))
    if genre:
        tags.delall('TCON')
        tags['TCON'] = TCON(encoding=3, text=genre)

    if cover_url:
        cover_data = _fetch_cover(cover_url)
        if cover_data:
            tags.delall('APIC')
            tags['APIC'] = APIC(
                encoding=3,
                mime='image/jpeg',
                type=3,
                desc='Cover',
                data=cover_data,
            )
            logger.info("Cover art embedded (MP3): %s", cover_url)
        else:
            logger.warning("Cover download returned empty bytes (MP3)")

    tags.save(str(path), v2_version=3)


def tag_track(file_path: str, title: str, artist: str, album: str = "", year: str = "", track_number: Optional[int] = None, genre: str = "", cover_url: Optional[str] = None) -> bool:
    """
    Write tags (+ optional cover art) to an MP3, FLAC, or M4A/AAC file.

    Routing:
        .flac          → mutagen.flac.FLAC  (Vorbis comments + Picture block)
        .mp3           → mutagen.id3.ID3
        .m4a / .mp4    → mutagen.mp4.MP4

    Returns True on success.
    """
    path = Path(file_path)
    if not path.exists():
        logger.error("File not found for tagging: %s", file_path)
        return False

    suffix = path.suffix.lower()

    try:
        if suffix == '.flac':
            _tag_flac(path, title, artist, album, year, track_number, genre, cover_url)

        elif suffix == '.mp3':
            _tag_mp3(path, title, artist, album, year, track_number, genre, cover_url)

        elif suffix == '.opus':
            _tag_opus(path, title, artist, album, year, track_number, genre, cover_url)

        else:
            # .m4a, .mp4, .aac, …
            _tag_mp4(path, title, artist, album, year, track_number, genre, cover_url)

        logger.info("Tags written to: %s", path.name)
        return True

    except Exception as exc:
        logger.error("Tagging failed for %s: %s", path.name, exc, exc_info=True)
        return False


# ------------ Conversion
def _detect_output_ext(ffmpeg_params: List[str], fallback_ext: str) -> str:
    """
    Derive the output file extension from the -c:a codec in ffmpeg_params.
    Falls back to fallback_ext if -c:a is absent or unrecognised.
    """
    try:
        idx = ffmpeg_params.index('-c:a') + 1
        codec = ffmpeg_params[idx]
        return _CODEC_TO_EXT.get(codec, fallback_ext)
    except (ValueError, IndexError):
        return fallback_ext


def convert_audio(input_path: str, ffmpeg_params: List[str]) -> Optional[str]:
    """
    Re-encode an audio file using custom FFmpeg parameters from config.

    The output extension is derived automatically from the -c:a codec in
    ffmpeg_params (see _CODEC_TO_EXT). Falls back to the original extension
    if the codec is not recognised.

    Args:
        input_path:    Path to the input audio file.
        ffmpeg_params: FFmpeg parameter list from config, e.g.
                       ["-map", "0", "-c:a", "aac", "-q:a", "2",
                        "-c:v", "copy", "-disposition:v", "attached_pic",
                        "-map_metadata", "0"]

    Returns:
        Output file path on success, None on failure.
    """
    input_path_obj = Path(input_path)
    if not input_path_obj.exists():
        logger.error(f"Input file not found: {input_path}")
        return None

    ffmpeg_path = get_ffmpeg_path()
    if not ffmpeg_path:
        logger.error("FFmpeg not found")
        return None

    # Derive output extension from codec, fall back to original extension
    fallback_ext = input_path_obj.suffix.lstrip('.')
    output_ext   = _detect_output_ext(ffmpeg_params, fallback_ext)
    output_path  = input_path_obj.with_suffix(f".{output_ext}")

    # Avoid overwriting the source when the extension does not change
    if output_path == input_path_obj:
        output_path = input_path_obj.with_stem(input_path_obj.stem + '_converted')

    ffmpeg_cmd = [ffmpeg_path, '-i', str(input_path_obj)] + ffmpeg_params + ['-y', str(output_path)]
    logger.info(f"Running audio conversion: {' '.join(ffmpeg_cmd)}")

    # Get duration for progress tracking
    total_duration = get_video_duration(str(input_path_obj))
    capture_ffmpeg_real_time(ffmpeg_cmd, f'[cyan]Converting to {output_ext.upper()}', total_duration)

    if not output_path.exists():
        logger.error(f"Output file was not created: {output_path}")
        return None

    logger.info(f"Audio converted successfully: {output_path}")
    return str(output_path)


def process_song(file_path: str, title: str, artist: str, album: str = "", year: str = "", track_number: Optional[int] = None, genre: str = "", cover_url: Optional[str] = None) -> str:
    """
    Full post-download pipeline for a music file.

    Steps:
        1. Tag     → write metadata + cover art via mutagen
        2. Convert → re-encode with FFmpeg if ffmpeg_params is set
        3. Cleanup → remove original file if conversion produced a new path

    Returns:
        - str: Final file path (converted path, or original if no conversion).
    """
    path = file_path

    # Step 1 — tag the downloaded file
    tag_track(
        file_path=path,
        title=title,
        artist=artist,
        album=album,
        year=year,
        track_number=track_number,
        genre=genre,
        cover_url=cover_url,
    )

    # Step 2 — convert if the user configured ffmpeg params
    if not ffmpeg_params:
        return path

    output_ext = _detect_output_ext(ffmpeg_params, Path(path).suffix.lstrip('.'))
    logger.info(f"Output extension for conversion: {output_ext}")

    converted = convert_audio(path, ffmpeg_params)
    if not converted:
        logger.warning("Audio conversion failed — keeping original file.")
        return path

    # Step 3 — remove original only after successful conversion
    if converted != path:
        try:
            os.remove(path)
            logger.info(f"Removed original file: {path}")
        except Exception as e:
            logger.warning(f"Could not remove original file: {e}")

        # Re-tag converted file: ffmpeg transfers text tags but not picture blocks (e.g. opus)
        tag_track(
            file_path=converted,
            title=title, artist=artist, album=album, year=year,
            track_number=track_number, genre=genre, cover_url=cover_url,
        )

    print("\n")
    return converted
