# 13.03.26

from __future__ import annotations


VIDEO_CODEC_MAP: dict[str, str] = {
    "avc1": "H.264",
    "h264": "H.264",
    "x264": "H.264",
    "hvc1": "H.265",
    "hev1": "H.265",
    "hevc": "H.265",
    "h265": "H.265",
    "x265": "H.265",
    "vp8": "VP8",
    "vp80": "VP8",
    "vp9": "VP9",
    "vp09": "VP9",
    "vp90": "VP9",
    "av1": "AV1",
    "av01": "AV1",
    "dvhe": "Dolby Vision",
    "dvh1": "Dolby Vision",
    "mp4v": "MPEG-4",
    "mpeg4": "MPEG-4",
    "vc1": "VC-1",
    "wmv3": "WMV",
    "mjpeg": "MJPEG",
    "prores": "ProRes",
}

AUDIO_CODEC_MAP: dict[str, str] = {
    "mp4a": "AAC",
    "aac": "AAC",
    "mp3": "MP3",
    "mp4a.69": "MP3",
    "mp4a.6b": "MP3",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "vorb": "Vorbis",
    "ac3": "AC-3",
    "ac-3": "AC-3",
    "eac3": "E-AC-3",
    "ec-3": "E-AC-3",
    "dts": "DTS",
    "dtsc": "DTS",
    "dtse": "DTS",
    "dtsh": "DTS",
    "flac": "FLAC",
    "alac": "ALAC",
    "pcm": "PCM",
    "lpcm": "PCM",
    "pcm_s16le": "PCM",
    "wma": "WMA",
    "wmav2": "WMA",
    "amr": "AMR",
    "speex": "Speex",
}

SUBTITLE_CODEC_MAP: dict[str, str] = {
    "stpp.ttml.im1t": "TTML",
    "stpp": "TTML",
    "ttml": "TTML",
    "wvtt": "VTT",
    "vtt": "VTT",
    "webvtt": "VTT",
    "srt": "SRT",
    "tx3g": "SRT",
    "ass": "ASS",
    "ssa": "SSA",
}

CHANNEL_MAP: dict[str, str] = {
    "1": "Mono",
    "2": "Stereo",
    "4": "4.0",
    "6": "5.1",
    "8": "7.1",

    # DASH hex codes
    "A000": "Stereo",
    "A001": "Mono",
    "A002": "2.1",
    "F801": "5.1",
    "F803": "7.1",
    "F805": "7.1",
    "F809": "5.1",
}


_VIDEO_CODEC_TOKEN: dict[str, str] = {
    "h264": "avc1",
    "h.264": "avc1",
    "avc": "avc1",
    "avc1": "avc1",
    "h265": "hvc1",
    "h.265": "hvc1",
    "hevc": "hvc1",
    "hvc1": "hvc1",
    "hev1": "hvc1",
    "av1": "av01",
    "av01": "av01",
    "vp9": "vp09",
    "vp09": "vp09",
    "vp8": "vp08",
    "vp08": "vp08",
    "dvhe": "dvhe",
    "dolby vision": "dvhe",
}

_AUDIO_CODEC_TOKEN: dict[str, str] = {
    "aac": "mp4a",
    "mp4a": "mp4a",
    "mp3": "mp4a.69",
    "ac3": "ac-3",
    "ac-3": "ac-3",
    "eac3": "ec-3",
    "e-ac-3": "ec-3",
    "ec-3": "ec-3",
    "ddplus": "ec-3",
    "opus": "opus",
    "vorbis": "vorbis",
    "flac": "flac",
    "alac": "alac",
    "dts": "dtsc",
}


def get_codec_token(user_codec: str, stream_type: str) -> str:
    """Map user label (e.g. 'H265', 'AAC') to downloader token (e.g. 'hvc1', 'mp4a')."""
    if not user_codec:
        return ""
    c = user_codec.strip().lower()
    table = _VIDEO_CODEC_TOKEN if stream_type == "video" else _AUDIO_CODEC_TOKEN
    return table.get(c, user_codec)


def _lookup(codec_map: dict, codec_str: str) -> str:
    """Exact match, then prefix match (e.g. 'avc1.640028' → 'H.264')."""
    if not codec_str:
        return ""
    c = codec_str.strip()
    c_lo = c.lower()

    # Exact match (case-insensitive key)
    for k, v in codec_map.items():
        if c_lo == k.lower():
            return v
    # Prefix match
    for k, v in codec_map.items():
        if c_lo.startswith(k.lower()):
            return v
    return c


def get_short_codec(stream_type: str, codec_str: str) -> str:
    """Return human-readable codec name given a stream type and codec string."""
    if not codec_str:
        return ""
    t = stream_type.lower()
    if t == "video":
        return _lookup(VIDEO_CODEC_MAP, codec_str)
    if t == "audio":
        return _lookup(AUDIO_CODEC_MAP, codec_str)
    if t in ("subtitle", "text"):
        return _lookup(SUBTITLE_CODEC_MAP, codec_str)
    return codec_str


def get_video_codec_name(codec_str: str) -> str:
    return _lookup(VIDEO_CODEC_MAP, codec_str)


def get_audio_codec_name(codec_str: str) -> str:
    return _lookup(AUDIO_CODEC_MAP, codec_str)


def get_subtitle_codec_name(codec_str: str) -> str:
    return _lookup(SUBTITLE_CODEC_MAP, codec_str)


def get_channel_label(channels: str) -> str:
    """Return human-readable channel layout label (e.g. '2' → 'Stereo', 'F801' → '5.1')."""
    if not channels:
        return ""
    ch = channels.strip()
    if ch in CHANNEL_MAP:
        return CHANNEL_MAP[ch]
    # Try parsing as plain integer (e.g. '6.0' → 6 → '5.1')
    try:
        n = int(float(ch))
        return CHANNEL_MAP.get(str(n), ch)
    except (ValueError, TypeError):
        return ch


_LANG_NAME_MAP: dict[str, str] = {
    "it": "Italian",
    "ita": "Italian",
    "en": "English",
    "eng": "English",
    "ja": "Japanese",
    "jpn": "Japanese",
    "de": "German",
    "ger": "German",
    "fr": "French",
    "fre": "French",
    "es": "Spanish",
    "spa": "Spanish",
    "pt": "Portuguese",
    "por": "Portuguese",
    "ru": "Russian",
    "rus": "Russian",
    "ar": "Arabic",
    "ara": "Arabic",
    "zh": "Chinese",
    "chi": "Chinese",
    "ko": "Korean",
    "kor": "Korean",
    "hi": "Hindi",
    "hin": "Hindi",
    "tr": "Turkish",
    "tur": "Turkish",
    "pl": "Polish",
    "pol": "Polish",
    "nl": "Dutch",
    "dut": "Dutch",
    "sv": "Swedish",
    "swe": "Swedish",
    "fi": "Finnish",
    "fin": "Finnish",
    "nb": "Norwegian",
    "nor": "Norwegian",
    "da": "Danish",
    "dan": "Danish",
    "ro": "Romanian",
    "rum": "Romanian",
    "cs": "Czech",
    "cze": "Czech",
    "hu": "Hungarian",
    "hun": "Hungarian",
    "el": "Greek",
    "gre": "Greek",
    "he": "Hebrew",
    "heb": "Hebrew",
    "uk": "Ukrainian",
    "ukr": "Ukrainian",
    "th": "Thai",
    "tha": "Thai",
    "vi": "Vietnamese",
    "vie": "Vietnamese",
    "id": "Indonesian",
    "ind": "Indonesian",
    "ms": "Malay",
    "may": "Malay",
}


def get_language_name(lang: str) -> str:
    """Return the full English language name for a code, or the code itself."""
    if not lang or lang.lower() in ("und", "n/a", ""):
        return ""
    return _LANG_NAME_MAP.get(lang.lower(), lang)


def codec_matches_stream(stream, filter_str: str) -> bool:
    """
    Return True if the stream's codec matches the filter string.
    Filter: comma/pipe-separated codec tokens, e.g. 'h264|avc', 'hevc'.
    Used by StreamSelector when filtering by codec.
    """
    if not filter_str:
        return True
    raw_codec = getattr(stream, "codecs", "") or ""
    short = get_short_codec(getattr(stream, "type", ""), raw_codec).lower()
    tokens = [
        t.strip().lower() for t in filter_str.replace(",", "|").split("|") if t.strip()
    ]
    return any(t in raw_codec.lower() or t in short for t in tokens)