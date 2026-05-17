# 06.06.25

import time
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass

from VibraVid.utils import config_manager


@dataclass
class Entries:
    """Standardized media item representation."""
    name: str
    type: str  # 'film', 'series', 'ova', 'song', 'album', etc.
    slug: str = None
    id: Any = None
    path_id: Optional[str] = None
    url: Optional[str] = None
    poster: Optional[str] = None
    year: Optional[int] = None
    provider_language: Optional[str] = None
    tmdb_id: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None
    audio_format: Optional[str] = None  # 'flac' | 'mp3' — set by GUI for music services

    @property
    def is_movie(self) -> bool:
        return self.type.lower() in ['film', 'movie', 'ova']

    @property
    def is_song(self) -> bool:
        return str(self.type or "").lower() in ['song', 'track', 'music']

    @property
    def is_album(self) -> bool:
        return str(self.type or "").lower() == 'album'


@dataclass
class Episode:
    """Episode information."""
    number: int
    name: str
    id: Optional[Any] = None
    language: Optional[str] = None


@dataclass
class Season:
    """Season information."""
    number: int
    episodes: List[Episode]
    name: Optional[str] = None
    
    @property
    def episode_count(self) -> int:
        return len(self.episodes)


_SCRAPER_CACHE_TTL = 900  # 15 minutes — allows the GUI to see new episodes without a container restart


class BaseStreamingAPI(ABC):
    _scraper_cache: Dict[str, Tuple[Any, float]] = {}  # key → (scraper, timestamp)

    def __init__(self):
        self.site_name: str = ""
        self.base_url: str = ""

    def _get_cache_key(self, media_item: Entries) -> str:
        """Generate a unique key for the scraper cache."""
        return f"{self.site_name}_{media_item.url or media_item.path_id or media_item.id or media_item.slug}"

    def _scraper_cache_enabled(self) -> bool:
        return not bool(config_manager.config.get_bool("DEFAULT", "disable_scraper_cache", default=False))

    def get_cached_scraper(self, media_item: Entries) -> Optional[Any]:
        """Retrieve a cached scraper instance from the global cache."""
        if not self._scraper_cache_enabled():
            return None
        key = self._get_cache_key(media_item)
        entry = self._scraper_cache.get(key)
        if entry is None:
            return None
        scraper, ts = entry
        if time.monotonic() - ts > _SCRAPER_CACHE_TTL:
            del self._scraper_cache[key]
            return None
        return scraper

    def set_cached_scraper(self, media_item: Entries, scraper: Any):
        """Store a scraper instance in the global cache."""
        if not self._scraper_cache_enabled():
            return
        key = self._get_cache_key(media_item)
        self._scraper_cache[key] = (scraper, time.monotonic())

    @abstractmethod
    def search(self, query: str) -> List[Entries]:
        """
        Search for content on the streaming site.
        
        Args:
            query: Search term
            
        Returns:
            List of Entries objects
        """
        pass
    
    @abstractmethod
    def get_series_metadata(self, media_item: Entries) -> Optional[List[Season]]:
        """
        Get seasons and episodes for a series.
        
        Args:
            media_item: Entries to get metadata for
            
        Returns:
            List of Season objects, or None if not a series
        """
        pass
    
    @abstractmethod
    def start_download(self, media_item: Entries, season: Optional[str] = None, episodes: Optional[str] = None) -> bool:
        """
        Start downloading content.
        
        Args:
            media_item: Entries to download
            season: Season number (for series)
            episodes: Episode selection (e.g., "1-5" or "1,3,5" or "*" for all)
            
        Returns:
            True if download started successfully
        """
        pass