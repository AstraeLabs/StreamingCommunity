# 08.03.26

import importlib
from typing import List, Optional

from .base import BaseStreamingAPI, Entries, Season, Episode

from VibraVid.services._base.site_loader import get_folder_name
from VibraVid.services.primevideo.scrapper import GetSerieInfo


class PrimeVideoAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "primevideo"
        self._load_config()
        self._search_fn = None
        self.scrape_serie = None
    
    def _load_config(self):
        """Load site configuration."""
        self.base_url = "https://www.primevideo.com"
        print(f"[{self.site_name}] Configuration loaded: base_url={self.base_url}")
    
    def _get_search_fn(self):
        """Lazy load the search function."""
        if self._search_fn is None:
            module = importlib.import_module(f"VibraVid.{get_folder_name()}.{self.site_name}")
            self._search_fn = getattr(module, "search")
        return self._search_fn
    
    def search(self, query: str) -> List[Entries]:
        """
        Search for content on PrimeVideo.
        
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
                    id=item_dict.get('slug'), # compact_id is in slug
                    name=item_dict.get('name'),
                    slug=item_dict.get('slug', ''),
                    path_id=item_dict.get('path_id'),
                    type=item_dict.get('type'),
                    url=item_dict.get('url'),
                    poster=item_dict.get('image'),
                    year=item_dict.get('year'),
                    tmdb_id=item_dict.get('tmdb_id'),
                    provider_language=item_dict.get('provider_language'),
                    raw_data=item_dict
                )
                results.append(media_item)
        
        return results

    def get_series_metadata(self, media_item: Entries) -> Optional[List[Season]]:
        """
        Get seasons and episodes for a PrimeVideo series.
        
        Args:
            media_item: Entries to get metadata for
            
        Returns:
            List of Season objects, or None if not a series
        """
        if getattr(media_item, 'type', '') == 'film' or media_item.is_movie:
            return None
        
        scrape_serie = self.get_cached_scraper(media_item)
        if not scrape_serie:
            scrape_serie = GetSerieInfo(url=media_item.url)
            self.set_cached_scraper(media_item, scrape_serie)
        
        seasons_count = scrape_serie.getNumberSeason()
        if not seasons_count:
            return None
        
        seasons = []
        for s in scrape_serie.seasons_manager.seasons:
            season_num = s.number
            season_name = getattr(s, 'name', None)
            
            episodes_raw = scrape_serie.getEpisodeSeasons(s.number)
            episodes = []
            
            for idx, ep in enumerate(episodes_raw or [], 1):
                ep_num = (ep.get('episodeNumber') if isinstance(ep, dict) else getattr(ep, 'number', idx)) or idx
                ep_name = ep.get('title') if isinstance(ep, dict) else getattr(ep, 'name', f"Episodio {idx}")
                ep_id = ep.get('id') if isinstance(ep, dict) else getattr(ep, 'id', idx)

                episode = Episode(
                    number=ep_num,
                    name=ep_name if ep_name else f"Episodio {idx}",
                    id=ep_id
                )
                episodes.append(episode)
            
            season = Season(number=season_num, episodes=episodes, name=season_name)
            seasons.append(season)
            print(f"[PrimeVideo] Season {season_num} ({season_name or f'Season {season_num}'}): {len(episodes)} episodes")
        
        return seasons if seasons else None

    def start_download(self, media_item: Entries, season: Optional[str] = None, episodes: Optional[str] = None) -> bool:
        """
        Start downloading from PrimeVideo.
        
        Args:
            media_item: Entries to download
            season: Season number (for series)
            episodes: Episode selection
            
        Returns:
            True if download started successfully
        """
        search_fn = self._get_search_fn()
        
        # Prepare selections
        selections = None
        if season or episodes:
            selections = {
                'season': season,
                'episode': episodes
            }
        
        scrape_serie = self.get_cached_scraper(media_item)
        search_fn(direct_item=media_item.raw_data, selections=selections, scrape_serie=scrape_serie)
        return True