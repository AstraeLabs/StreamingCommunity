# 27-01-26

from .generic import GenericStreamingAPI
from .base import Entries

from VibraVid.services.discoveryplus.scrapper import GetSerieInfo


class DiscoveryPlus(GenericStreamingAPI):
    site_name = "discoveryplus"
    log_label = "DiscoveryPlus"

    def _build_scraper(self, media_item: Entries):
        # Discovery+ is keyed by the show id, not the url.
        return GetSerieInfo(media_item.id)
