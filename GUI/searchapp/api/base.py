# 06.06.25

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class Entries:
    """Standardized media item representation."""
    name: str
    type: str  # 'film', 'series', 'ova', etc.
    slug: str = None
    id: Any = None
    path_id: Optional[str] = None
    url: Optional[str] = None
    poster: Optional[str] = None
    year: Optional[int] = None
    provider_language: Optional[str] = None
    tmdb_id: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None
    
    @property
    def is_movie(self) -> bool:
        return self.type.lower() in ['film', 'movie', 'ova']


@dataclass
class Episode:
    """Episode information."""
    number: int
    name: str
    id: Optional[Any] = None


@dataclass
class Season:
    """Season information."""
    number: int
    episodes: List[Episode]
    name: Optional[str] = None
    
    @property
    def episode_count(self) -> int:
        return len(self.episodes)


class BaseStreamingAPI(ABC):
    _scraper_cache: Dict[str, Any] = {}  # Global cache to persist scrapers across instances

    def __init__(self):
        self.site_name: str = ""
        self.base_url: str = ""

    def _get_cache_key(self, media_item: Entries) -> str:
        """Generate a unique key for the scraper cache."""
        return f"{self.site_name}_{media_item.url or media_item.path_id or media_item.id or media_item.slug}"

    def get_cached_scraper(self, media_item: Entries) -> Optional[Any]:
        """Retrieve a cached scraper instance from the global cache."""
        key = self._get_cache_key(media_item)
        return self._scraper_cache.get(key)

    def set_cached_scraper(self, media_item: Entries, scraper: Any):
        """Store a scraper instance in the global cache."""
        key = self._get_cache_key(media_item)
        self._scraper_cache[key] = scraper

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