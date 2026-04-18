# 06.06.25

import importlib
from typing import List, Optional

from .base import BaseStreamingAPI, Entries, Season, Episode

from VibraVid.utils import config_manager
from VibraVid.services._base.site_loader import get_folder_name
from VibraVid.services.animeunity.scrapper import ScrapeSerieAnime


class AnimeUnityAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "animeunity"
        self._load_config()
        self._search_fn = None
        self.scrape_serie = None
    
    def _load_config(self):
        """Load site configuration."""
        self.base_url = config_manager.domain.get(self.site_name, "full_url").rstrip("/")
        print(f"[{self.site_name}] Configuration loaded: base_url={self.base_url}")
    
    def _get_search_fn(self):
        """Lazy load the search function."""
        if self._search_fn is None:
            module = importlib.import_module(f"VibraVid.{get_folder_name()}.{self.site_name}")
            self._search_fn = getattr(module, "search")
        return self._search_fn
    
    def search(self, query: str) -> List[Entries]:
        """
        Search for content on AnimeUnity.
        
        Args:
            query: Search term
            
        Returns:
            List of Entries objects
        """
        search_fn = self._get_search_fn()
        database = search_fn(query, get_onlyDatabase=True)
        
        results = []
        if database and hasattr(database, 'media_list'):
            items = list(database.media_list)
            for element in items:
                item_dict = element.__dict__.copy() if hasattr(element, '__dict__') else {}
                
                media_item = Entries(
                    id=item_dict.get('id'),
                    name=item_dict.get('name'),
                    slug=item_dict.get('slug', ''),
                    path_id=item_dict.get('path_id'),
                    type=item_dict.get('type'),
                    url=item_dict.get('url'),
                    poster=item_dict.get('image'),
                    tmdb_id=item_dict.get('tmdb_id'),
                    raw_data=item_dict
                )
                results.append(media_item)
        
        return results
    
    def get_series_metadata(self, media_item: Entries) -> Optional[List[Season]]:
        """
        Get seasons and episodes for an AnimeUnity series.
        Note: AnimeUnity typically has single season anime.
        
        Args:
            media_item: Entries to get metadata for
            
        Returns:
            List of Season objects (usually one season), or None if not a series
        """
        # Check if it's a movie or OVA
        if media_item.is_movie:
            return None
        
        scrape_serie = self.get_cached_scraper(media_item)
        if not scrape_serie:
            scrape_serie = ScrapeSerieAnime(self.base_url)
            scrape_serie.setup(series_name=media_item.slug, media_id=media_item.id)
            self.set_cached_scraper(media_item, scrape_serie)
        
        episodes_count = scrape_serie.get_count_episodes()
        if not episodes_count:
            return None
        
        # AnimeUnity typically has single season
        episodes = []
        for ep_num in range(1, episodes_count + 1):
            episode = Episode(
                number=ep_num,
                name=f"Episodio {ep_num}",
                id=ep_num
            )
            episodes.append(episode)
        
        season = Season(number=1, episodes=episodes, name="Stagione 1")
        return [season]
            
    def start_download(self, media_item: Entries, season: Optional[str] = None, episodes: Optional[str] = None) -> bool:
        """
        Start downloading from AnimeUnity.
        
        Args:
            media_item: Entries to download
            season: Season number (typically 1 for anime)
            episodes: Episode selection
            
        Returns:
            True if download started successfully
        """
        search_fn = self._get_search_fn()
        media_type = str(media_item.type or "").lower()
        is_series_like = media_type in {"tv", "serie", "series", "ova", "ona", "show", "tv short"}

        # For AnimeUnity, always pass an episode selection for series-like titles.
        selections = None
        if is_series_like:
            selections = {'episode': episodes or '*'}
        
        scrape_serie = self.get_cached_scraper(media_item)
        search_fn(direct_item=media_item.raw_data, selections=selections, scrape_serie=scrape_serie)
        return True