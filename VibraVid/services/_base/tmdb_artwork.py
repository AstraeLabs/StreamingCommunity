# 22.07.26

import logging
from typing import Optional

from VibraVid.utils import config_manager
from VibraVid.provider.tmdb import tmdb_client
from VibraVid.core.ui.tracker import context_tracker

logger = logging.getLogger(__name__)
_TV_MATCH_SITES = {"streamingcommunity", "altadefinizione", "cinezo", "tubitv", "animeunity", "animeworld"}


def _tv_matching_allowed(site_name: Optional[str]) -> bool:
    """Whether TMDB series/episode matching is allowed for this site."""
    site_name = site_name or context_tracker.site_name
    return bool(site_name) and site_name.lower() in _TV_MATCH_SITES


def embed_enabled() -> bool:
    """Whether resolved artwork should be written into the downloaded file."""
    return config_manager.config.get_bool("DOWNLOAD", "embed_tmdb_poster", default=True)


def _resolve_tmdb_id(media_type: str, tmdb_id=None, name: Optional[str] = None, slug: Optional[str] = None, year=None) -> Optional[int]:
    """Resolve a raw TMDB id: use tmdb_id if already known, else fall back to a slug+year lookup"""
    if not tmdb_client.api_key:
        return None

    if tmdb_id:
        try:
            return int(tmdb_id)
        except (TypeError, ValueError):
            return None

    slug = slug or (tmdb_client._slugify(name) if name else None)
    if not slug:
        return None

    result = tmdb_client.get_type_and_id_by_slug_year(slug, str(year) if year else None, media_type)
    if result and result.get('type') == media_type and result.get('id'):
        return result['id']

    return None


def resolve_movie_poster_url(tmdb_id=None, name: Optional[str] = None, slug: Optional[str] = None, year=None) -> Optional[str]:
    """Resolve a movie's poster URL, service-agnostic (works from just name+year if no tmdb_id is known)."""
    resolved = _resolve_tmdb_id('movie', tmdb_id, name, slug, year)
    return tmdb_client.get_poster_url('movie', resolved) if resolved else None


def resolve_series_tmdb_id(tmdb_id=None, name: Optional[str] = None, slug: Optional[str] = None, year=None, site_name: Optional[str] = None) -> Optional[int]:
    """Resolve a series' raw TMDB id, to be reused for every episode's artwork lookup."""
    if not _tv_matching_allowed(site_name):
        return None
    return _resolve_tmdb_id('tv', tmdb_id, name, slug, year)


def resolve_episode_artwork_url(series_tmdb_id, season_number, episode_number) -> Optional[str]:
    """Episode still -> season poster -> series poster fallback chain."""
    if not series_tmdb_id:
        return None
    season_number, episode_number = tmdb_client.resolve_actual_season_episode(series_tmdb_id, season_number, episode_number)
    return tmdb_client.get_episode_artwork_url(series_tmdb_id, season_number, episode_number)