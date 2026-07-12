# 27.01.26

from .generic import GenericStreamingAPI
from .base import Entries

from VibraVid.services.realtime.scrapper import GetSerieInfo


class HomeGardenTVAPI(GenericStreamingAPI):
    """Home & Garden TV — uses the shared realtime scrapper."""
    site_name = "homegardentv"
    base_url = "https://public.aurora.enhanced.live"
    entry_default_type = "tv"
    log_label = "HomeGardenTV"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.url)
