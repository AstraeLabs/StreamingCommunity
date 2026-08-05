# 05.08.26

from VibraVid.services.paramountplus.scrapper import GetSerieInfo

from .base import Entries
from .generic import GenericStreamingAPI


class ParamountPlus(GenericStreamingAPI):
    site_name = "paramountplus"
    log_label = "ParamountPlus"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.id)
