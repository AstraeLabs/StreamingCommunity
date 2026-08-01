# 27.07.26

import logging
import os
import re
import threading
import time
from pathlib import Path

from ._library_paths import VIDEO_EXTS, _category_dir, _norm_cmp, _parse, _season_dir_num

logger = logging.getLogger(__name__)

_EPISODE_CATEGORIES = ("serie", "anime")
_TTL_SECONDS = 30
_TRAILING_YEAR = re.compile(r"\s*\(\d{4}\)\s*$")
_TRAILING_LANG = re.compile(r"[\s\-_]+(?:ITA|SUB|DUB|ENG|JAP|SUBITA)\.?$", re.IGNORECASE)
_TRAILING_NUM = re.compile(r"[\s\-_]+\d{1,2}$")

_cache: dict[str, tuple[float, frozenset]] = {}
_cache_lock = threading.Lock()


def _norm(name: str) -> str:
    """Norm for comparison: lowercase, no accents, no trailing year or language tag."""
    text = _TRAILING_YEAR.sub("", name or "")
    text = _TRAILING_LANG.sub("", text)
    return _norm_cmp(text)


def _series_dir(series_name: str) -> Path | None:
    """Return the directory for a series, or None if not found."""
    wanted = _norm(series_name)
    if not wanted:
        return None

    loose_wanted = _TRAILING_NUM.sub("", wanted).strip()
    fallback = None

    for category in _EPISODE_CATEGORIES:
        try:
            base = _category_dir(category)
            if not base.is_dir():
                continue
            entries = [p for p in base.iterdir() if p.is_dir()]
        except OSError:
            logger.debug("Categoria %s non leggibile", category, exc_info=True)
            continue

        for path in entries:
            current = _norm(path.name)
            if current == wanted:
                return path
            if fallback is None and _TRAILING_NUM.sub("", current).strip() == loose_wanted:
                fallback = path

    return fallback


def _scan(series_dir: Path) -> frozenset:
    """Return a set of (season, episode) tuples for all episodes found in the series directory."""
    found: set[tuple[int, int]] = set()

    for root, _dirs, files in os.walk(series_dir):
        season_hint = _season_dir_num(Path(root).name)
        for filename in files:
            if Path(filename).suffix.lower() not in VIDEO_EXTS:
                continue
            parsed = _parse(Path(filename).stem)
            if not parsed:
                continue
            season, episode, _title = parsed
            if season_hint is not None and season != season_hint:
                season = season_hint
            found.add((season, episode))

    return frozenset(found)


def owned_episodes(series_name: str) -> frozenset:
    """Return a set of (season, episode) tuples for all episodes found in the library for a series."""
    if not series_name:
        return frozenset()

    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(series_name)
        if hit and now - hit[0] < _TTL_SECONDS:
            return hit[1]

    try:
        directory = _series_dir(series_name)
        result = _scan(directory) if directory else frozenset()
    except Exception:
        logger.exception("Scansione libreria fallita per %s", series_name)
        result = frozenset()

    with _cache_lock:
        _cache[series_name] = (now, result)
    return result


def forget(series_name: str | None = None) -> None:
    """Forget cached episode info for a series, or all series if series_name is None."""
    with _cache_lock:
        if series_name is None:
            _cache.clear()
        else:
            _cache.pop(series_name, None)


__all__ = ["owned_episodes", "forget"]
