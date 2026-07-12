# 02.02.26

from .generic import GenericStreamingAPI
from .base import Entries

from VibraVid.services.realtime.scrapper import GetSerieInfo


class DmaxAPI(GenericStreamingAPI):
    """Dmax — uses the shared realtime scrapper."""
    site_name = "dmax"
    base_url = "https://public.aurora.enhanced.live"
    log_label = "Dmax"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.url)
