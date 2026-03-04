# 10.01.26

import re
from typing import List, Dict, Any


# Internal utilities
from ..utils.object import StreamInfo
from ..utils.trans_codec import get_video_codec_name
from ..utils.trans_codec import get_audio_codec_name
from ..utils.trans_codec import get_subtitle_codec_name


def _video_codec(raw: str) -> str:
    try:
        return get_video_codec_name(raw)
    except Exception:
        return raw or ""

def _audio_codec(raw: str) -> str:
    try:
        return get_audio_codec_name(raw)
    except Exception:
        return raw or ""

def _subtitle_codec(raw: str) -> str:
    try:
        return get_subtitle_codec_name(raw)
    except Exception:
        return raw or ""

def _bw_str(bps: int) -> str:
    if bps <= 0:
        return "N/A"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mbps"
    if bps >= 1_000:
        return f"{bps / 1_000:.0f} kbps"
    return f"{bps} bps"


_NONE_VALUES = {"none", "", None}

def _detect_stream_type(fmt: Dict[str, Any]):
    """
    Return ("Video"|"Audio"|None) for a format entry.

    Priority:
      1. vcodec / acodec fields (when explicitly set and not "none")
      2. video_ext / audio_ext fallback (handles HLS tracks where codecs are absent)
    """
    vcodec = fmt.get("vcodec") or "none"
    acodec = fmt.get("acodec") or "none"

    has_video_codec = vcodec.lower() not in _NONE_VALUES
    has_audio_codec = acodec.lower() not in _NONE_VALUES

    if has_video_codec and not has_audio_codec:
        return "Video", vcodec, acodec
    if has_audio_codec and not has_video_codec:
        return "Audio", vcodec, acodec
    if has_video_codec and has_audio_codec:
        return None, vcodec, acodec

    # Fallback: use *_ext fields (common for HLS tracks that omit codec strings)
    video_ext = (fmt.get("video_ext") or "none").lower()
    audio_ext = (fmt.get("audio_ext") or "none").lower()

    has_video_ext = video_ext not in _NONE_VALUES
    has_audio_ext = audio_ext not in _NONE_VALUES

    if has_video_ext and not has_audio_ext:
        return "Video", vcodec, acodec
    if has_audio_ext and not has_video_ext:
        return "Audio", vcodec, acodec
    if has_video_ext and has_audio_ext:
        return None, vcodec, acodec

    return None, vcodec, acodec


_UNKNOWN_EXTS = {"unknown_video", "unknown_audio", "unknown", "none", ""}

def _clean_sub_ext(raw: str) -> str:
    """Return empty string for meaningless subtitle ext values."""
    return "" if (raw or "").lower() in _UNKNOWN_EXTS else raw


def parse_ytdlp_json(json_data: Dict[str, Any]) -> List[StreamInfo]:
    """
    Parse yt-dlp JSON (from `yt-dlp -j`) into StreamInfo objects.
    Order: videos (highest→lowest bitrate) → audios → subtitles.
    """
    videos:    List[StreamInfo] = []
    audios:    List[StreamInfo] = []
    subtitles: List[StreamInfo] = []

    # ── formats → video / audio ────────────────────────────────────────────────
    for fmt in json_data.get("formats", []):
        fmt_id = fmt.get("format_id", "")
        ext    = fmt.get("ext", "")

        # skip storyboard / image tracks
        if ext in ("mhtml", "jpg", "jpeg", "png", "webp"):
            continue

        stream_type, vcodec, acodec = _detect_stream_type(fmt)
        if stream_type is None:
            continue

        # bitrate — prefer tbr, fall back to vbr/abr
        tbr = int((fmt.get("tbr") or fmt.get("vbr") or fmt.get("abr") or 0) * 1_000)
        lang     = fmt.get("language") or None

        if stream_type == "Video":
            width  = fmt.get("width")  or 0
            height = fmt.get("height") or 0
            res    = f"{width}x{height}" if width and height else ""
            fps    = float(fmt.get("fps") or 0)

            s = StreamInfo(type_="Video")
            s.id            = fmt_id
            s.extension     = ext
            s.resolution    = res
            s.frame_rate    = fps
            s.bandwidth     = _bw_str(tbr)
            s.raw_bandwidth = str(tbr)
            s.codec         = _video_codec(vcodec) if vcodec.lower() not in _NONE_VALUES else ""
            s.language      = lang
            s.selected      = False
            s.descriptor    = "yt-dlp"
            videos.append(s)

        else:  # Audio
            channels = str(fmt.get("audio_channels") or "")
            abr      = int((fmt.get("abr") or 0) * 1_000)

            s = StreamInfo(type_="Audio")
            s.id            = fmt_id
            s.extension     = ext
            s.resolution    = ""
            s.frame_rate    = 0.0
            s.bandwidth     = _bw_str(abr or tbr)
            s.raw_bandwidth = str(abr or tbr)
            s.codec         = _audio_codec(acodec) if acodec.lower() not in _NONE_VALUES else ""
            s.language      = lang
            s.channels      = channels
            s.selected      = False
            s.descriptor    = "yt-dlp"
            audios.append(s)

    # ── subtitles / automatic_captions ─────────────────────
    seen_langs: set = set()

    def _add_subtitle_langs(sub_dict: Dict, auto: bool):
        for lang, url_list in sub_dict.items():
            if not url_list:
                continue

            # Skip duplicate lang
            lang_key = lang.lower()
            if lang_key in seen_langs:
                continue
            seen_langs.add(lang_key)

            # Prefer srt > vtt > ttml > first
            ext_order = ["srt", "vtt", "ttml", "dfxp", "ass"]
            best_fmt  = None
            for want_ext in ext_order:
                best_fmt = next((u for u in url_list if u.get("ext") == want_ext), None)
                if best_fmt:
                    break
            if not best_fmt:
                best_fmt = url_list[0]

            raw_ext   = best_fmt.get("ext", "")
            clean_ext = _clean_sub_ext(raw_ext)
            label     = f"{lang} (auto)" if auto else lang

            s = StreamInfo(type_="Subtitle")
            s.id            = f"sub-{lang}"
            s.extension     = clean_ext          # empty when unknown_video
            s.resolution    = ""
            s.frame_rate    = 0.0
            s.bandwidth     = "N/A"
            s.raw_bandwidth = "0"
            s.codec         = _subtitle_codec(clean_ext) if clean_ext else ""
            s.language      = lang
            s.name          = label
            s.selected      = False
            s.descriptor    = "yt-dlp"
            s._subtitle_urls = url_list
            subtitles.append(s)

    _add_subtitle_langs(json_data.get("subtitles", {}),           auto=False)
    _add_subtitle_langs(json_data.get("automatic_captions", {}),  auto=True)

    # ── Sort ───────────────────────────────────────────────────────────────────
    videos.sort(key=lambda s: int(s.raw_bandwidth or 0), reverse=True)
    audios.sort(key=lambda s: int(s.raw_bandwidth or 0), reverse=True)

    return videos + audios + subtitles


# ── Fallback: parse plain `-F` text output ────────────────────────────────────
_SUBTITLE_EXTS = {"vtt", "srt", "ttml", "mhtml", "webvtt", "dfxp", "ass"}
_AUDIO_EXTS    = {"m4a", "aac", "mp3", "ac3", "opus", "ogg", "flac"}
_SEP_RE        = re.compile(r"\s*[│|]\s*")
_DIVIDER_RE    = re.compile(r"^[─\-]{10,}")


def parse_ytdlp_formats(output: str) -> List[StreamInfo]:
    """Parse `yt-dlp -F` text table as fallback when JSON is unavailable."""
    raw:      List[StreamInfo] = []
    in_table = False

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if _DIVIDER_RE.match(line):
            in_table = True
            continue
        if not in_table or not line:
            continue
        if line.startswith("[") or re.match(r"^ID\s", line, re.IGNORECASE):
            continue

        parts         = _SEP_RE.split(line)
        left_tokens   = parts[0].split()
        middle_tokens = parts[1].split() if len(parts) > 1 else []
        right_col     = parts[2] if len(parts) > 2 else ""

        if len(left_tokens) < 2:
            continue

        fmt_id, ext = left_tokens[0], left_tokens[1]
        ll = line.lower()

        if ext in ("mhtml",) or "storyboard" in ll:
            continue

        if "subtitle" in ll or "stpp" in ll or "wvtt" in ll or ext.lower() in _SUBTITLE_EXTS:
            stype = "Subtitle"
        elif "audio only" in ll or ext.lower() in _AUDIO_EXTS or (
            len(left_tokens) >= 3 and "audio" in left_tokens[2].lower()
        ):
            stype = "Audio"
        else:
            stype = "Video"

        tbr = 0
        for part in middle_tokens:
            p = part.lower()
            if re.match(r"^\d[\d.]*[km]$", p):
                try:
                    v = float(re.sub(r"[^0-9.]", "", p))
                    tbr = int(v * 1_000) if "k" in p else int(v * 1_000_000)
                except ValueError:
                    pass
                break

        m = re.search(r"\[([a-zA-Z]{2,8}(?:-[a-zA-Z0-9]{2,8})*)\]", right_col)
        lang = m.group(1) if m else None

        res, fps = "", 0.0
        if stype == "Video":
            if len(left_tokens) >= 3:
                res = left_tokens[2]
            if len(left_tokens) >= 4:
                try:
                    fps = float(left_tokens[3])
                except ValueError:
                    pass

        raw_codec = right_col.split()[0] if right_col.split() else ""
        if stype == "Video":
            codec = _video_codec(raw_codec)
        elif stype == "Audio":
            codec = _audio_codec(raw_codec)
        else:
            codec = _subtitle_codec(raw_codec)

        s = StreamInfo(type_=stype)
        s.id            = fmt_id
        s.extension     = ext
        s.resolution    = res
        s.frame_rate    = fps
        s.bandwidth     = _bw_str(tbr)
        s.raw_bandwidth = str(tbr)
        s.codec         = codec
        s.language      = lang
        s.selected      = False
        s.descriptor    = "yt-dlp"
        raw.append(s)

    vids = sorted([s for s in raw if s.type == "Video"],    key=lambda s: int(s.raw_bandwidth or 0), reverse=True)
    auds = sorted([s for s in raw if s.type == "Audio"],    key=lambda s: int(s.raw_bandwidth or 0), reverse=True)
    subs = [s for s in raw if s.type == "Subtitle"]
    return vids + auds + subs