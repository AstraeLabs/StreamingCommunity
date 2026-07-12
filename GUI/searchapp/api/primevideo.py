# 08.03.26

from typing import Any, Dict

from .generic import GenericStreamingAPI
from .base import Entries, Episode

from VibraVid.services.primevideo.scrapper import GetSerieInfo


class PrimeVideoAPI(GenericStreamingAPI):
    site_name = "primevideo"
    base_url = "https://www.primevideo.com"
    log_label = "PrimeVideo"

    def _build_entry(self, item_dict: Dict[str, Any]) -> Entries:
        entry = super()._build_entry(item_dict)
        entry.id = item_dict.get('slug')   # compact_id is carried in slug
        return entry

    def _map_episode(self, ep: Any, idx: int) -> Episode:
        # PrimeVideo episode dicts use 'episodeNumber' / 'title' keys.
        if isinstance(ep, dict):
            return Episode(
                number=ep.get('episodeNumber') or idx,
                name=ep.get('title') or f"Episodio {idx}",
                id=ep.get('id', idx),
            )
        return super()._map_episode(ep, idx)

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(url=media_item.url)
