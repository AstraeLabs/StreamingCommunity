# 08.04.26

from .generic import GenericStreamingAPI
from .base import Entries

from VibraVid.services.realtime.scrapper import GetSerieInfo


class DiscoveryAPI(GenericStreamingAPI):
    """Discovery Channel IT — uses the shared realtime scrapper."""
    site_name = "discovery"
    log_label = "Discovery"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.url)
