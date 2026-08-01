# 27-01-26

from VibraVid.services.realtime.scrapper import GetSerieInfo

from .base import Entries
from .generic import GenericStreamingAPI


class RealtimeAPI(GenericStreamingAPI):
    """Realtime — uses the shared realtime scrapper."""

    site_name = "realtime"
    base_url = "https://public.aurora.enhanced.live"
    log_label = "Realtime"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.url)
