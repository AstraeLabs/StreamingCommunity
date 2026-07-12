# 27-01-26

from .generic import GenericStreamingAPI
from .base import Entries

from VibraVid.services.realtime.scrapper import GetSerieInfo


class RealtimeAPI(GenericStreamingAPI):
    """Realtime — uses the shared realtime scrapper."""
    site_name = "realtime"
    base_url = "https://public.aurora.enhanced.live"
    log_label = "Realtime"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.url)
