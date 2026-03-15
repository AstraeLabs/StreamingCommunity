# 10.01.26

from __future__ import annotations

import re
from typing import List, Optional

from VibraVid.core.manifest.stream import Stream


def audio_matches_filter(language: str, codec: str, filter_str: str) -> bool:
    """
    Return True if the audio stream matches the given filter string.

    Filter syntax examples:
        lang=en|it
        codecs=aac|mp4a
        lang=it:codecs=ec-3

    Empty / falsy filter always matches.
    """
    if not filter_str or filter_str.lower() in ("false", "none", "best", ""):
        return True

    lang_m = re.search(r"lang=['\"]?([^'\":\s]+)['\"]?", filter_str)
    codec_m = re.search(r"codecs?=['\"]?([^'\":\s]+)['\"]?", filter_str)

    lang_ok = True
    codec_ok = True

    if lang_m:
        tokens = lang_m.group(1).lower().split("|")
        lang_ok = any(t in (language or "").lower() for t in tokens)

    if codec_m:
        tokens = codec_m.group(1).lower().split("|")
        codec_ok = any(t in (codec or "").lower() for t in tokens)

    return lang_ok and codec_ok


def pick_best_stream(streams: list, stream_type: str, filter_str: str) -> Optional["Stream"]:
    """
    Select the best matching stream from a list of Stream objects.

    - Video: supports ``res=HEIGHT`` filter (e.g. ``res=1080``), falls back to
      highest bitrate.
    - Audio/other: selects by highest bitrate.
    """
    if not streams:
        return None

    if not filter_str or filter_str.lower() in ("best", "none", "false", ""):
        return max(streams, key=lambda s: s.bitrate or 0)

    if stream_type.lower() != "video":
        return max(streams, key=lambda s: s.bitrate or 0)

    # Video-specific: handle res=HEIGHT
    res_m = re.search(r"res=(\d+)", filter_str)
    if res_m:
        target_h = int(res_m.group(1))
        best, min_diff = None, float("inf")
        for s in streams:
            if not s.resolution:
                continue
            try:
                # resolution can be "1920x1080" or "1080p" or just "1080"
                parts = s.resolution.lower().replace("p", "").split("x")
                h = int(parts[-1])
                diff = abs(h - target_h)
                if diff < min_diff or (
                    diff == min_diff
                    and (s.bitrate or 0) > (best.bitrate if best else 0)
                ):
                    min_diff, best = diff, s
            except (ValueError, AttributeError, IndexError):
                continue
        if best:
            return best

    return max(streams, key=lambda s: s.bitrate or 0)


def parse_lang_list(filter_str: str) -> List[str]:
    """Return deduplicated, ordered language tokens from a filter string."""
    m = re.search(r"lang=['\"]?([^'\":\s]+)['\"]?", filter_str)
    if not m:
        return []
    seen, langs = set(), []
    for token in m.group(1).split("|"):
        t = token.strip().lower()
        if t and t not in seen:
            seen.add(t)
            langs.append(t)
    return langs


def prefer_ext_from_codec(codec_token: str) -> str:
    """Translate a codec name/token to a preferred file extension."""
    c = codec_token.lower()
    if "mp4a" in c or "aac" in c:
        return "m4a"
    if "opus" in c:
        return "webm"
    return ""