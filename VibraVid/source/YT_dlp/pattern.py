# 10.01.26

import re
from typing import Tuple, List

from ..utils.stream_filters import (
    pick_best_stream,
    parse_lang_list,
    prefer_ext_from_codec,
)


def pick_best_video(video_streams: list, filter_str: str):
    """Backwards-compatible alias for pick_best_stream() for video."""
    return pick_best_stream(video_streams, "Video", filter_str)

def _parse_lang_list(filter_str: str) -> List[str]:
    """Backwards-compatible alias for parse_lang_list()."""
    return parse_lang_list(filter_str)

def _prefer_ext_from_codec(codec_token: str) -> str:
    """Backwards-compatible alias for prefer_ext_from_codec()."""
    return prefer_ext_from_codec(codec_token)

def translate_to_ytdlp_format(v_filter: str, a_filter: str) -> str:
    """
    Build a yt-dlp -f selector string (used only in fallback / non-interactive mode).
    Every alternative combines video+audio with + to prevent video-only output.
    """
    res_m = None
    if v_filter and v_filter.lower() not in ("best", "none", "false", ""):
        res_m = re.search(r"res=(\d+)", v_filter)

    if res_m:
        h        = res_m.group(1)
        v_part   = f"bestvideo[height<={h}]"
        v_fallbk = "bestvideo"
    else:
        v_part   = "bv*"
        v_fallbk = None

    prefer_ext = ""
    if a_filter and a_filter.lower() not in ("best", "none", "false", ""):
        cm = re.search(r"codecs?=['\"]?([^'\":\s]+)['\"]?", a_filter)
        if cm:
            prefer_ext = _prefer_ext_from_codec(cm.group(1))

    langs = _parse_lang_list(a_filter) if a_filter else []

    parts: List[str] = []

    def _add(vp: str, ap: str):
        parts.append(f"{vp}+{ap}")

    if langs:
        for lang in langs:
            if prefer_ext:
                _add(v_part, f"ba[language={lang}][ext={prefer_ext}]")
            _add(v_part, f"ba[language={lang}]")
        if v_fallbk:
            for lang in langs:
                if prefer_ext:
                    _add(v_fallbk, f"ba[language={lang}][ext={prefer_ext}]")
                _add(v_fallbk, f"ba[language={lang}]")

    if prefer_ext:
        _add(v_part, f"ba[ext={prefer_ext}]")
    _add(v_part, "ba")
    if v_fallbk:
        _add(v_fallbk, "ba")

    parts.append("b")
    return "/".join(parts)


def translate_to_ytdlp_subtitle_args(s_filter: str) -> Tuple[List[str], bool]:
    """Convert subtitle filter to (lang_list, use_auto)."""
    if not s_filter or s_filter.lower() in ("none", "false", ""):
        return [], False
    langs = _parse_lang_list(s_filter)
    return (langs if langs else ["all"]), False