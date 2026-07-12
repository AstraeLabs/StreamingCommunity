# 06.06.25

from .generic import GenericStreamingAPI
from .base import Entries

from VibraVid.services.raiplay.scrapper import GetSerieInfo


class RaiPlayAPI(GenericStreamingAPI):
    site_name = "raiplay"
    base_url = "https://www.raiplay.it"
    log_label = "RaiPlay"

    def _build_scraper(self, media_item: Entries):
        # RaiPlay is keyed by a path_id; derive it from the url when absent.
        path_id = media_item.path_id
        if not path_id:
            path_id = media_item.url.replace(self.base_url, "").lstrip("/") if media_item.url else None
            
        if not path_id:
            print(f"[RaiPlay] Error: Missing path_id for {media_item.name}")
            return None
        
        # getNumberSeason() triggers collect_info_title() internally when needed.
        return GetSerieInfo(path_id)
