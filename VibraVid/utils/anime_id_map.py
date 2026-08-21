# 19.08.26

import logging
import threading
import time

from VibraVid.utils import config_manager, disk_cache
from VibraVid.utils.http_client import create_client

logger = logging.getLogger(__name__)

_SOURCE_URL = "https://raw.githubusercontent.com/Fribb/anime-lists/master/anime-list-full.json"
_CACHE_SERVICE = "anime_id_map"
_CACHE_NAME = "crosswalk"
_CACHE_TTL_SECONDS = 24 * 3600

_lock = threading.Lock()
_by_mal: dict[int, dict] = {}
_by_anilist: dict[int, dict] = {}
_loaded_at = 0.0


def _crosswalk_enabled() -> bool:
    try:
        return config_manager.config.get_bool("ARR", "anime_id_crosswalk_enabled", default=True)
    except Exception:
        return True


def _fetch_entries() -> list | None:
    client = create_client(timeout=30)
    try:
        resp = client.get(_SOURCE_URL)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else None
    except Exception as e:
        logger.warning(f"[anime_id_map] fetch of Fribb/anime-lists failed: {e}")
        return None
    finally:
        client.close()


def _build_indexes(entries: list) -> tuple[dict[int, dict], dict[int, dict]]:
    by_mal: dict[int, dict] = {}
    by_anilist: dict[int, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        tmdb_ids = entry.get("themoviedb_id")
        if not isinstance(tmdb_ids, dict) or not tmdb_ids:
            continue

        record = {
            "themoviedb_id": tmdb_ids,
            "season": entry.get("season"),
            "episode_offset": entry.get("episode_offset"),
        }

        mal_id = entry.get("mal_id")
        if isinstance(mal_id, int):
            by_mal[mal_id] = record

        anilist_id = entry.get("anilist_id")
        if isinstance(anilist_id, int):
            by_anilist[anilist_id] = record

    return by_mal, by_anilist


def _indexes_to_cache_payload(by_mal: dict, by_anilist: dict) -> dict:
    return {
        "expiry": time.time() + _CACHE_TTL_SECONDS,
        "by_mal": {str(k): v for k, v in by_mal.items()},
        "by_anilist": {str(k): v for k, v in by_anilist.items()},
    }


def _cache_payload_to_indexes(payload: dict) -> tuple[dict[int, dict], dict[int, dict]]:
    by_mal = {int(k): v for k, v in (payload.get("by_mal") or {}).items()}
    by_anilist = {int(k): v for k, v in (payload.get("by_anilist") or {}).items()}
    return by_mal, by_anilist


def _ensure_loaded() -> None:
    global _by_mal, _by_anilist, _loaded_at

    with _lock:
        if _by_mal or _by_anilist:
            if (time.time() - _loaded_at) < _CACHE_TTL_SECONDS:
                return

        cached = disk_cache.load(_CACHE_SERVICE, _CACHE_NAME)
        if cached and disk_cache.is_fresh(cached):
            _by_mal, _by_anilist = _cache_payload_to_indexes(cached)
            _loaded_at = time.time()
            return

        entries = _fetch_entries()
        if entries is None:
            # Network failure: fall back to a stale on-disk copy rather than nothing.
            if cached:
                _by_mal, _by_anilist = _cache_payload_to_indexes(cached)
            _loaded_at = time.time()
            return

        _by_mal, _by_anilist = _build_indexes(entries)
        _loaded_at = time.time()
        disk_cache.save(_CACHE_SERVICE, _CACHE_NAME, _indexes_to_cache_payload(_by_mal, _by_anilist))
        logger.info(f"[anime_id_map] crosswalk loaded: {len(_by_mal)} MAL / {len(_by_anilist)} AniList entries")


def lookup(mal_id=None, anilist_id=None) -> dict | None:
    """Return the crosswalk record for a MAL or AniList id, or None if unknown/disabled."""
    if not _crosswalk_enabled():
        return None

    _ensure_loaded()

    for raw_id, index in ((mal_id, _by_mal), (anilist_id, _by_anilist)):
        if raw_id in (None, ""):
            continue
        try:
            record = index.get(int(raw_id))
        except (TypeError, ValueError):
            continue
        if record:
            return record
    return None


def resolve_tmdb_id(media_type: str, mal_id=None, anilist_id=None) -> str | None:
    """Return a TMDB id (as a string) for `media_type` ('tv'/'movie') via the MAL/AniList crosswalk."""
    record = lookup(mal_id=mal_id, anilist_id=anilist_id)
    if not record:
        return None
    tmdb_ids = record.get("themoviedb_id") or {}
    value = tmdb_ids.get("movie" if media_type == "movie" else "tv")
    if value in (None, ""):
        return None
    return str(value)


def resolve_split_cour_episode(
    candidates: list[dict], season_num: int, absolute_episode: int
) -> dict[str, int] | None:
    """Map a Sonarr/TMDB absolute episode number to one provider candidate + its local episode.

    Some anime seasons are split across several provider entries ("Part 1",
    "Part 2", ...) that all share the same TMDB show id and season number but
    each only carry a slice of it (e.g. TMDB season 3 = provider "Part 1"
    episodes 1-12 + "Part 2" episodes 1-10, offset 12). Sonarr only knows the
    combined TMDB numbering, so a raw episode number like 13 has to be
    translated into "Part 2, local episode 1" before it means anything to the
    provider.

    `candidates` is a list of dicts, each with optional 'mal_id'/'anilist_id'
    (to look up in the crosswalk) and an optional 'episodes_count' (to bound
    the match). Returns the first candidate whose crosswalk season matches
    `season_num` and whose offset-adjusted range contains `absolute_episode`,
    as {'candidate_index': i, 'local_episode': n} — or None if none qualify.
    """
    for index, candidate in enumerate(candidates):
        record = lookup(mal_id=candidate.get("mal_id"), anilist_id=candidate.get("anilist_id"))
        if not record:
            continue

        record_season = (record.get("season") or {}).get("tmdb")
        if record_season is not None and record_season != season_num:
            continue

        offset = (record.get("episode_offset") or {}).get("tmdb") or 0
        local_episode = absolute_episode - offset
        if local_episode < 1:
            continue

        episodes_count = candidate.get("episodes_count")
        if episodes_count is not None and local_episode > episodes_count:
            continue

        return {"candidate_index": index, "local_episode": local_episode}

    return None
