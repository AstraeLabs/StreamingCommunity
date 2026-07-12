# 11.07.26

import importlib
from typing import Any, Dict, List, Optional

from .base import BaseStreamingAPI, Entries, Season, Episode

from VibraVid.services._base.site_loader import get_folder_name


class GenericStreamingAPI(BaseStreamingAPI):
    """
    Shared implementation for streaming services whose GUI adapter follows the common pattern:

      * ``search()``              -> map the scraper's ``media_list`` to Entries
      * ``get_series_metadata()`` -> build a scraper, walk ``seasons_manager``
      * ``start_download()``      -> forward direct_item + selections to service
    """
    site_name: str = ""
    base_url: Optional[str] = None
    entry_default_type: Optional[str] = None   # fallback value for Entries.type
    log_label: str = ""                        # prefix used in log lines

    def __init__(self):
        super().__init__()
        self.site_name = type(self).site_name
        self.base_url = type(self).base_url
        self._search_fn = None

    def _get_search_fn(self):
        """Lazy-load the service's ``search`` entry point."""
        if self._search_fn is None:
            module = importlib.import_module(f"VibraVid.{get_folder_name()}.{self.site_name}")
            self._search_fn = getattr(module, "search")
        return self._search_fn

    def _build_entry(self, item_dict: Dict[str, Any]) -> Entries:
        """Map one raw scraper item dict to an Entries. Override to customise."""
        return Entries(
            id=item_dict.get('id'),
            name=item_dict.get('name'),
            slug=item_dict.get('slug', ''),
            path_id=item_dict.get('path_id'),
            type=item_dict.get('type', self.entry_default_type),
            url=item_dict.get('url'),
            poster=item_dict.get('image'),
            year=item_dict.get('year'),
            tmdb_id=item_dict.get('tmdb_id'),
            provider_language=item_dict.get('provider_language'),
            raw_data=item_dict,
        )

    def search(self, query: str) -> List[Entries]:
        search_fn = self._get_search_fn()
        database = search_fn(query, get_onlyDatabase=True)

        results: List[Entries] = []
        if database and hasattr(database, 'media_list'):
            for element in list(database.media_list):
                item_dict = element.__dict__.copy() if hasattr(element, '__dict__') else {}
                results.append(self._build_entry(item_dict))
        return results

    def _build_scraper(self, media_item: Entries):
        """Construct the site's series scraper for ``media_item``. Must override.
        Return ``None`` to signal 'no scraper could be built' (treated as no series metadata).
        """
        raise NotImplementedError

    def _map_episode(self, ep: Any, idx: int) -> Episode:
        """Map one raw episode (dict or object) to an Episode.

        A falsy/0 episode number falls back to the 1-based position ``idx`` (some
        providers tag extras/clips with number 0); this matches the original
        per-service adapters, which all used ``<number> or idx``.
        """
        if isinstance(ep, dict):
            ep_number = ep.get('number') or idx
            ep_name = ep.get('name') or f"Episodio {idx}"
            ep_id = ep.get('id', idx)
        else:
            ep_number = getattr(ep, 'number', None) or idx
            ep_name = getattr(ep, 'name', None) or f"Episodio {idx}"
            ep_id = getattr(ep, 'id', idx)
        return Episode(number=ep_number, name=ep_name, id=ep_id)

    def get_series_metadata(self, media_item: Entries) -> Optional[List[Season]]:
        """Fetch and return a list of Seasons for the given series media_item, or None if not applicable."""
        if media_item.is_movie:
            return None

        scrape_serie = self.get_cached_scraper(media_item)
        if not scrape_serie:
            scrape_serie = self._build_scraper(media_item)
            if scrape_serie is None:
                return None
            self.set_cached_scraper(media_item, scrape_serie)

        scrape_serie.getNumberSeason()
        if not len(scrape_serie.seasons_manager):
            print(f"[{self.log_label or self.site_name}] No seasons found for: {media_item.name}")
            return None

        seasons: List[Season] = []
        for s in scrape_serie.seasons_manager.seasons:
            episodes_raw = scrape_serie.getEpisodeSeasons(s.number)
            episodes = [self._map_episode(ep, i) for i, ep in enumerate(episodes_raw or [], 1)]
            season = Season(number=s.number, episodes=episodes, name=getattr(s, 'name', None))
            seasons.append(season)
            print(f"[{self.log_label or self.site_name}] Season {season.number} "
                  f"({season.name or f'Season {season.number}'}): {len(episodes)} episodes")

        return seasons if seasons else None

    def _make_selections(self, season: Optional[str], episodes: Optional[str]) -> Optional[Dict[str, Any]]:
        """Build the selections dict for the service's ``search`` entry point."""
        if season or episodes:
            return {'season': season, 'episode': episodes}
        return None

    def start_download(self, media_item: Entries, season: Optional[str] = None, episodes: Optional[str] = None) -> bool:
        """Start a download for a selected track, album, or series by delegating to the service's ``search`` entry point."""
        search_fn = self._get_search_fn()
        selections = self._make_selections(season, episodes)
        scrape_serie = self.get_cached_scraper(media_item)

        # Prefer the scraper-provided raw_data; fall back to the Entries dict so a
        # service that never populates raw_data still gets a valid direct_item
        # (base_search skips the interactive prompt only when direct_item is truthy).
        direct_item = media_item.raw_data if media_item.raw_data else media_item.__dict__.copy()
        search_fn(direct_item=direct_item, selections=selections, scrape_serie=scrape_serie)
        return True
