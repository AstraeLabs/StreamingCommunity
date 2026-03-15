# 13.03.26

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from VibraVid.source.utils.codec import get_codec_token

logger = logging.getLogger(__name__)


def _height(s) -> int:
    h = getattr(s, "height", 0) or 0
    if h:
        return h
    res = getattr(s, "resolution", "") or ""
    try:
        return int(res.lower().replace("p", "").split("x")[-1])
    except (ValueError, IndexError):
        return 0


def _bitrate(s) -> int:
    return getattr(s, "bitrate", 0) or 0


def _language(s) -> str:
    return (getattr(s, "language", "") or "").strip().lower()


def _codecs(s) -> str:
    return (getattr(s, "codecs", "") or "").strip().lower()


@dataclass
class FilterSpec:
    """
    Structured representation of a user filter string.

    Accepted formats
    ----------------
    ``"best"`` / ``"worst"``  → select best or worst stream
    ``"all"``                 → select all streams
    ``"false"``               → drop all (no download)
    ``"1080"``                → height constraint  (video)
    ``"1920,H265"``           → height + codec     (video)
    ``",H265"``               → codec only         (video)
    ``"H265"``                → codec only (bare)  (video)
    ``"ita|it"``              → language tokens    (audio/sub)
    ``"ita|it,AAC"``          → language + codec   (audio)
    ``",AAC"``                → codec only         (audio)
    ``"res=1080:codecs=hvc1:for=best"``  → native n3u8dl passthrough
    """
    drop: bool = False
    select_all: bool = False
    select_best: bool = True  # True = best, False = worst (when not all/drop)

    # constraints
    res: Optional[str] = None  # height string, e.g. "1080"
    langs: Optional[str] = None  # pipe-sep lang tokens, e.g. "ita|it"
    codec: Optional[str] = None  # downloader token, e.g. "hvc1", "mp4a"
    extra: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: str, stream_type: str) -> "FilterSpec":
        spec = cls()
        r = (raw or "").strip()

        if not r or r.lower() == "false":
            spec.drop = True
            return spec
        if r.lower() == "all":
            spec.select_all = True
            return spec
        if r.lower() == "best":
            return spec
        if r.lower() == "worst":
            spec.select_best = False
            return spec

        # Native n3u8dl format (contains key=value)
        if "=" in r:
            spec._parse_native(r, stream_type)
            return spec

        # User shorthand:  "primary,codec"
        parts = r.split(",", 1)
        primary = parts[0].strip()
        codec_s = parts[1].strip() if len(parts) > 1 else ""

        if codec_s:
            spec.codec = get_codec_token(codec_s, stream_type)

        if not primary:
            return spec

        # Purely numeric → height
        if re.match(r"^\d+$", primary):
            spec.res = primary
            return spec

        # No pipe → check bare codec token
        if not codec_s and "|" not in primary:
            translated = get_codec_token(primary, stream_type)
            if translated.lower() != primary.lower():
                spec.codec = translated
                return spec

        # Otherwise → language tokens
        spec.langs = primary
        return spec

    def _parse_native(self, r: str, stream_type: str) -> None:
        for_val = None
        for seg in r.split(":"):
            if "=" not in seg:
                continue
            k, v = seg.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'\"")
            if k == "res":
                self.res = v
            elif k == "lang":
                self.langs = v
            elif k in ("codecs", "codec"):
                self.codec = v
            elif k == "for":
                for_val = v.lower()
            else:
                self.extra[k] = v

        if for_val == "all":
            self.select_all = True
            self.select_best = True
        elif for_val == "worst":
            self.select_best = False

@dataclass
class SelectionResult:
    """
    What the selector actually found after progressive fallback.
    Downloader-specific formatters read this to build their own arg strings.
    """
    streams: list = field(default_factory=list)

    # Which constraints were satisfied (None = constraint was relaxed away)
    matched_res: Optional[str] = None
    matched_langs: Optional[str] = None
    matched_codec: Optional[str] = None

    drop: bool = False
    select_all: bool = False
    select_best: bool = True  # False → worst


class N3u8dlFormatter:
    """
    Converts a SelectionResult to a single n3u8dl --select-* argument string.

    n3u8dl syntax:
        res=HEIGHT:lang='TOKENS':codecs=TOKEN:for=best|worst|all
    """

    @staticmethod
    def format(result: SelectionResult) -> str:
        if result.drop:
            return "false"

        if result.select_all and not result.matched_res and not result.matched_langs and not result.matched_codec:
            return "all"

        if not result.matched_res and not result.matched_langs and not result.matched_codec:
            return "best" if result.select_best else "worst"

        parts: List[str] = []
        if result.matched_res:
            parts.append(f"res={result.matched_res}")
        if result.matched_langs:
            parts.append(f"lang='{result.matched_langs}'")
        if result.matched_codec:
            parts.append(f"codecs={result.matched_codec}")

        for_val = "all" if result.select_all else ("best" if result.select_best else "worst")
        parts.append(f"for={for_val}")

        return ":".join(parts)


def _matches_res(s, res: str) -> bool:
    try:
        target = int(res)
    except (ValueError, TypeError):
        return False
    h = _height(s)
    w = getattr(s, "width", 0) or 0
    return h == target or w == target


def _matches_codec(s, token: str) -> bool:
    """Prefix match; unknown codec is treated as matching (not filtered out)."""
    if not token:
        return True
    raw = _codecs(s)
    if not raw:
        return True
    return raw.startswith(token.lower()) or token.lower() in raw


def _matches_lang(s, langs: str) -> bool:
    tokens = [t.strip().lower() for t in langs.split("|") if t.strip()]
    sl = _language(s)
    return any(t == sl or t in sl for t in tokens)


class StreamSelector:
    """
    Applies user filter strings to Stream objects, marks ``stream.selected``,
    and returns downloader-formatted argument strings via ``apply()``.
    """
    def __init__(self, video_filter: str, audio_filter: str, subtitle_filter: str, formatter=None):
        self._vf = (video_filter or "best").strip()
        self._af = (audio_filter or "best").strip()
        self._sf = (subtitle_filter or "all").strip()
        self._formatter = formatter or N3u8dlFormatter()

    def apply(self, streams: list) -> Tuple[str, str, str]:
        """
        Mark ``stream.selected`` and return ``(sv, sa, ss)`` arg strings.
        """
        pv = FilterSpec.parse(self._vf, "video")
        pa = FilterSpec.parse(self._af, "audio")
        ps = FilterSpec.parse(self._sf, "subtitle")

        rv = self._select_video(streams, pv)
        ra = self._select_audio(streams, pa)
        rs = self._select_subtitle(streams, ps)

        sv = self._formatter.format(rv)
        sa = self._formatter.format(ra)
        ss = self._formatter.format(rs)

        logger.info(f"StreamSelector n3u8dl args → video={sv!r}  audio={sa!r}  subtitle={ss!r}")
        return sv, sa, ss
    
    def _select_video(self, streams: list, spec: FilterSpec) -> SelectionResult:
        result = SelectionResult(select_best=spec.select_best)
        videos = [s for s in streams if getattr(s, "type", "") == "video"]
        logger.info(f"Video available: {[f'{_height(s)}p/{_codecs(s)}' for s in videos]} with filter spec: res={spec.res} codec={spec.codec} select_all={spec.select_all} drop={spec.drop}")

        if spec.drop:
            result.drop = True
            return result

        if not videos:
            result.drop = True
            return result

        if spec.select_all:
            for s in videos:
                s.selected = True
            result.streams = videos
            result.select_all = True
            return result
        
        had_constraints = bool(spec.res or spec.codec)
        pick_exact = _best if spec.select_best else _worst  # full match
        pick_fallback = _worst  # any relaxed step

        # Step 1: res + codec — full match → honour user best/worst
        if spec.res and spec.codec:
            pool = [s for s in videos if _matches_res(s, spec.res) and _matches_codec(s, spec.codec)]
            if pool:
                return self._mark_one(pool, pick_exact, result, res=spec.res, codec=spec.codec)
            logger.info(f"StreamSelector video: res={spec.res} + codecs={spec.codec} — no match, relaxing")

        # Step 2: codec only (res relaxed)
        if spec.codec:
            pool = [s for s in videos if _matches_codec(s, spec.codec)]
            if pool:
                extra = f" for codec={spec.codec}" if spec.res else ""
                logger.info(f"StreamSelector video: res={spec.res}{extra} not available, selecting worst")
                result.select_best = False
                return self._mark_one(pool, pick_fallback, result)
            logger.info(f"StreamSelector video: codec={spec.codec} — no match, relaxing")

        # Step 3: res only (codec relaxed)
        if spec.res:
            pool = [s for s in videos if _matches_res(s, spec.res)]
            if pool:
                if spec.codec:
                    logger.info(f"StreamSelector video: codec={spec.codec} not available at res={spec.res}, selecting worst")
                    result.select_best = False
                    return self._mark_one(pool, pick_fallback, result)
                else:
                    return self._mark_one(pool, pick_exact, result, res=spec.res)
            logger.info(f"StreamSelector video: res={spec.res} — no match, falling back to worst")

        # Step 4: absolute fallback
        if had_constraints:
            result.select_best = False
            s = _worst(videos)
        else:
            s = pick_exact(videos)
        if s:
            s.selected = True
            result.streams = [s]
        return result

    def _select_audio(self, streams: list, spec: FilterSpec) -> SelectionResult:
        result = SelectionResult(select_best=spec.select_best)
        audios = [s for s in streams if getattr(s, "type", "") == "audio"]
        logger.info(f"Audio available: {[f'{_language(s)}/{_codecs(s)}' for s in audios]} with filter spec: lang={spec.langs} codec={spec.codec} select_all={spec.select_all} drop={spec.drop}")

        if spec.drop:
            result.drop = True
            return result

        if not audios:
            result.drop = True
            return result

        if spec.select_all and not spec.langs and not spec.codec:
            for s in audios:
                s.selected = True
            result.streams = audios
            result.select_all = True
            return result

        # Step 1: lang + codec
        if spec.langs and spec.codec:
            pool = [s for s in audios if _matches_lang(s, spec.langs) and _matches_codec(s, spec.codec)]
            if pool:
                self._mark_best_per_lang(pool, spec.select_best)
                result.streams = [s for s in pool if s.selected]
                result.matched_langs = spec.langs
                result.matched_codec = spec.codec
                if spec.select_all:
                    result.select_all = True
                return result
            logger.info(f"StreamSelector audio: lang={spec.langs!r} + codec={spec.codec} — no match, relaxing")

        # Step 2: codec only
        if spec.codec:
            pool = [s for s in audios if _matches_codec(s, spec.codec)]
            if pool:
                if spec.langs:
                    logger.info(f"StreamSelector audio: lang={spec.langs!r} not available with codec={spec.codec}, selecting all {spec.codec} streams")
                self._mark_best_per_lang(pool, spec.select_best)
                result.streams = [s for s in pool if s.selected]
                result.matched_codec = spec.codec
                if spec.select_all:
                    result.select_all = True
                return result
            
            # Codec specified but zero streams have it → drop
            logger.info(f"StreamSelector audio: codec={spec.codec} not available — dropping audio (false)")
            result.drop = True
            return result

        # Step 3: lang only (no codec constraint)
        if spec.langs:
            pool = [s for s in audios if _matches_lang(s, spec.langs)]
            if pool:
                self._mark_best_per_lang(pool, spec.select_best)
                result.streams = [s for s in pool if s.selected]
                result.matched_langs = spec.langs
                if spec.select_all:
                    result.select_all = True
                return result
            logger.info(f"StreamSelector audio: lang={spec.langs!r} — no match, falling back to best per lang")

        # Step 4: no constraints or lang-only fallback → best per language
        self._mark_best_per_lang(audios, spec.select_best)
        result.streams = [s for s in audios if s.selected]
        return result

    def _select_subtitle(self, streams: list, spec: FilterSpec) -> SelectionResult:
        result = SelectionResult(select_best=spec.select_best)
        subs = [s for s in streams if getattr(s, "type", "") == "subtitle"]
        logger.info(f"Subtitle available: {[f'{_language(s)}' for s in subs]} with filter spec: lang={spec.langs} select_all={spec.select_all} drop={spec.drop}")

        if spec.drop:
            result.drop = True
            return result

        # No subtitle streams exist at all → drop
        if not subs:
            result.drop = True
            return result

        if spec.select_all and not spec.langs:
            for s in subs:
                s.selected = True
            result.streams = subs
            result.select_all = True
            return result

        if spec.langs:
            pool = [s for s in subs if _matches_lang(s, spec.langs)]
            if pool:
                for s in pool:
                    s.selected = True
                result.streams = pool
                result.matched_langs = spec.langs
                result.select_all = True
                return result
            logger.info(f"StreamSelector subtitle: lang={spec.langs!r} — no match, selecting all available")

        # Fallback: select all subs
        for s in subs:
            s.selected = True
        result.streams = subs
        result.select_all = True
        return result

    @staticmethod
    def _mark_one(pool: list, pick_fn, result: SelectionResult, res: Optional[str] = None, codec: Optional[str] = None, langs: Optional[str] = None) -> SelectionResult:
        s = pick_fn(pool)
        if s:
            s.selected = True
            result.streams = [s]
            result.matched_res = res
            result.matched_codec = codec
            result.matched_langs = langs
        return result

    @staticmethod
    def _mark_best_per_lang(streams: list, best: bool = True) -> None:
        seen: dict = {}
        for s in sorted(streams, key=_bitrate, reverse=best):
            lang = _language(s) or "und"
            if lang not in seen:
                seen[lang] = True
                s.selected = True

    @staticmethod
    def parse_filter(filter_str: str) -> dict:
        spec = FilterSpec.parse(filter_str or "", "video")
        if spec.drop:
            return {"for": "false"}
        result: dict = {}
        if spec.select_all:
            result["for"] = "all"
        if spec.res:
            result["res"] = spec.res
        if spec.langs:
            result["lang"] = spec.langs
        if spec.codec:
            result["codecs"] = spec.codec
        result.update(spec.extra)
        result.setdefault("for", "all" if spec.select_all else "best")
        return result

    @staticmethod
    def extract_order_from_filter(filter_string: str) -> List[str]:
        spec = FilterSpec.parse(filter_string or "", "audio")
        if spec.langs:
            return [v.strip() for v in spec.langs.split("|") if v.strip()]
        return []


def _best(streams: list):
    return max(streams, key=_bitrate) if streams else None


def _worst(streams: list):
    return min(streams, key=_bitrate) if streams else None