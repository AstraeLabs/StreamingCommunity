# 26.05.24

import importlib
from typing import List, Optional

from .base import BaseStreamingAPI, Entries, Season

from VibraVid.services._base.site_loader import get_folder_name


class MostraguardaAPI(BaseStreamingAPI):
    def __init__(self):
        super().__init__()
        self.site_name = "mostraguarda"
        self._search_fn = None
    
    def _get_search_fn(self):
        """Lazy load the search function."""
        if self._search_fn is None:
            module = importlib.import_module(f"VibraVid.{get_folder_name()}.{self.site_name}")
            self._search_fn = getattr(module, "search")
        return self._search_fn
    
    def search(self, query: str) -> List[Entries]:
        """
        Search for content on Mostraguarda.
        
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
                    year=item_dict.get('year'),
                    tmdb_id=item_dict.get('tmdb_id'),
                    provider_language=item_dict.get('provider_language'),
                    raw_data=item_dict
                )
                results.append(media_item)
        
        return results

    def get_series_metadata(self, media_item: Entries) -> Optional[List[Season]]:
        """
        Mostraguarda supports only movies, hence no series metadata.
        """
        return None

    def start_download(self, media_item: Entries, season: Optional[str] = None, episodes: Optional[str] = None) -> bool:
        """
        Start downloading from Mostraguarda.
        
        Args:
            media_item: Entries to download
            season: Should be None (movies only)
            episodes: Should be None (movies only)
            
        Returns:
            True if download started successfully
        """
        search_fn = self._get_search_fn()
        search_fn(direct_item=media_item.raw_data, selections=None, scrape_serie=None)
        return True