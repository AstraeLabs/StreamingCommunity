# 16.04.24

import json
import logging
import subprocess

from VibraVid.core.muxing.helper._ffprobe_cache import ffprobe_cached
from VibraVid.core.utils.codec import get_short_codec
from VibraVid.core.utils.language import language_variants
from VibraVid.setup import get_ffprobe_path

logger = logging.getLogger(__name__)

_RESOLUTION_TIERS = [
    (4320, 7680, "4320p"),
    (2880, 5120, "2880p"),
    (2160, 3840, "2160p"),
    (1440, 2560, "1440p"),
    (1080, 1920, "1080p"),
    (900, 1600, "900p"),
    (768, 1366, "768p"),
    (720, 1280, "720p"),
    (540, 960, "540p"),
    (480, 854, "480p"),
    (360, 640, "360p"),
    (240, 426, "240p"),
    (144, 256, "144p"),
]


def _classify_resolution(width, height) -> str:
    for min_h, min_w, label in _RESOLUTION_TIERS:
        if (height and height >= min_h) or (width and width >= min_w):
            return label
    if height:
        return f"{height}p"
    return ""


_EMPTY_METADATA = {
    "quality": "",
    "language": "",
    "video_codec": "",
    "audio_codec": "",
    "audio_tracks": [],
    "audio_flags": "",
    "sub_language": "",
    "sub_flags": "",
    "subtitle_tracks": [],
}


def _disposition_flags(disposition: dict) -> list:
    """Return the flag names (upper-case) that are set to 1 in an ffprobe stream disposition dict."""
    order = ("forced", "hearing_impaired", "visual_impaired", "comment", "default")
    labels = {"hearing_impaired": "SDH", "visual_impaired": "AD"}
    return [labels.get(name, name.upper()) for name in order if disposition.get(name)]


@ffprobe_cached
def get_media_metadata(file_path: str) -> dict:
    """Extract quality (resolution), languages, codecs and flags from a media file using ffprobe."""
    cmd = [get_ffprobe_path(), "-v", "error", "-show_streams", "-print_format", "json", file_path]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            logger.error(f"ffprobe error while extracting metadata for file {file_path}: {result.stderr.strip()}")
            return dict(_EMPTY_METADATA)

        info = json.loads(result.stdout)
        streams = info.get("streams", [])
        quality_val = ""
        vcodec_val = ""

        for s in streams:
            if s.get("codec_type") == "video":
                quality_val = _classify_resolution(s.get("width"), s.get("height"))

                raw_vcodec = s.get("codec_name", "")
                vcodec_val = get_short_codec("video", raw_vcodec)
                break

        languages_found = []
        acodecs_found = []
        audio_tracks = []
        audio_flags_found = []
        for s in streams:
            if s.get("codec_type") == "audio":
                lang = s.get("tags", {}).get("language") or ""
                raw_acodec = s.get("codec_name", "")
                short_acodec = get_short_codec("audio", raw_acodec)

                if lang:
                    lang_up = lang.upper()
                    if lang_up not in languages_found:
                        languages_found.append(lang_up)
                    if short_acodec and short_acodec not in acodecs_found:
                        acodecs_found.append(short_acodec)

                    flags = _disposition_flags(s.get("disposition", {}) or {})
                    audio_flags_found.extend(f for f in flags if f not in audio_flags_found)
                    audio_tracks.append(
                        {
                            "language": lang_up,
                            "codec": short_acodec,
                            "flags": flags,
                            **language_variants(lang),
                        }
                    )

        sub_languages_found = []
        sub_flags_found = []
        subtitle_tracks = []
        for s in streams:
            if s.get("codec_type") == "subtitle":
                lang = s.get("tags", {}).get("language") or ""
                if not lang:
                    continue

                lang_up = lang.upper()
                if lang_up not in sub_languages_found:
                    sub_languages_found.append(lang_up)

                disposition = s.get("disposition", {}) or {}
                forced = bool(disposition.get("forced"))
                sdh = bool(disposition.get("hearing_impaired"))
                cc = sdh  # muxer only exposes a single hearing_impaired flag; CC and SDH share it
                flags = _disposition_flags(disposition)
                sub_flags_found.extend(f for f in flags if f not in sub_flags_found)

                subtitle_tracks.append(
                    {
                        "language": lang_up,
                        "forced": forced,
                        "sdh": sdh,
                        "cc": cc,
                        "flags": flags,
                        **language_variants(lang),
                    }
                )

        return {
            "quality": quality_val,
            "language": "-".join(languages_found) if languages_found else "",
            "video_codec": vcodec_val,
            "audio_codec": "-".join(acodecs_found) if acodecs_found else "",
            "audio_tracks": audio_tracks,
            "audio_flags": "-".join(audio_flags_found),
            "sub_language": "-".join(sub_languages_found) if sub_languages_found else "",
            "sub_flags": "-".join(sub_flags_found),
            "subtitle_tracks": subtitle_tracks,
        }

    except Exception:
        return dict(_EMPTY_METADATA)
