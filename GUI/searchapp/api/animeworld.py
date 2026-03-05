# 27.01.26

import importlib
from typing import List, Optional

from .base import BaseStreamingAPI, Entries, Season, Episode

from VibraVid.utils import config_manager
from VibraVid.services._base.site_loader import get_folder_name
from VibraVid.services.animeworld.scrapper import ScrapSerie


class AnimeWorldAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "animeworld"
        self._load_config()
        self._search_fn = None
        self.scrape_serie = None
    
    def _load_config(self):
        """Load site configuration."""
        self.base_url = config_manager.domain.get(self.site_name, "full_url")
        print(f"[{self.site_name}] Configuration loaded: base_url={self.base_url}")
    
    def _get_search_fn(self):
        """Lazy load the search function."""
        if self._search_fn is None:
            module = importlib.import_module(f"VibraVid.{get_folder_name()}.{self.site_name}")
            self._search_fn = getattr(module, "search")
        return self._search_fn
    
    def search(self, query: str) -> List[Entries]:
        """
        Search for anime content on AnimeWorld.
        
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
                    type=item_dict.get('type', 'TV'),
                    url=item_dict.get('url'),
                    poster=item_dict.get('image'),
                    year=item_dict.get('year'),
                    tmdb_id=item_dict.get('tmdb_id'),
                    raw_data=item_dict
                )
                results.append(media_item)
        
        return results
    
    def get_series_metadata(self, media_item: Entries) -> Optional[List[Season]]:
        """
        Get episodes for an AnimeWorld series.
        
        Args:
            media_item: Entries to get metadata for
            
        Returns:
            List with a single Season containing all episodes, or None if it's a movie
        """
        if media_item.type == 'Movie':
            return None
        
        scrape_serie = self.get_cached_scraper(media_item)
        if not scrape_serie:
            scrape_serie = ScrapSerie(media_item.url, self.base_url)
            self.set_cached_scraper(media_item, scrape_serie)

        episodes_data = scrape_serie.get_episodes()
        
        if not episodes_data:
            print(f"[AnimeWorld] No episodes found for: {media_item.name}")
            return None
        
        # Create episodes list
        episodes = []
        for idx, ep_data in enumerate(episodes_data, 1):
            episode = Episode(
                number=idx,
                name=getattr(ep_data, 'name', f"Episode {idx}"),
                id=getattr(ep_data, 'id', idx)
            )
            episodes.append(episode)
        
        season = Season(number=1, episodes=episodes, name="Episodes")
        print(f"[AnimeWorld] Found {len(episodes)} episodes for: {media_item.name}")
        
        return [season]
    
    def start_download(self, media_item: Entries, season: Optional[str] = None, episodes: Optional[str] = None) -> bool:
        """
        Start downloading from AnimeWorld.
        
        Args:
            media_item: Entries to download
            season: Season number (typically not used for anime, defaults to None)
            episodes: Episode selection
            
        Returns:
            True if download started successfully
        """
        search_fn = self._get_search_fn()
        
        # Prepare selections
        selections = None
        if episodes:
            selections = {
                'episode': episodes
            }
        
        scrape_serie = self.get_cached_scraper(media_item)
        search_fn(direct_item=media_item.raw_data, selections=selections, scrape_serie=scrape_serie)
        return True