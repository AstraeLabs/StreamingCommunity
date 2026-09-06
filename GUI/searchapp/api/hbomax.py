# 27-01-26

from VibraVid.services.hbomax.scrapper import GetSerieInfo

from .base import Entries
from .generic import GenericStreamingAPI


class Max(GenericStreamingAPI):
    site_name = "hbomax"
    log_label = "HBO Max"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.id)
