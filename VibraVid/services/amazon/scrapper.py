# 07.03.26

import re

from VibraVid.services._base.object import SeasonManager, Episode, Season
from .client import get_json, get_spa_headers, BASE_URL, SPA_VERSION


class GetSerieInfo:
    def __init__(self, url: str):
        """
        Initialize the GetSerieInfo scraper for a Prime Video TV series.

        Args:
            url (str): Detail URL of the series
        """
        self.url             = url
        self.series_name     = None
        self.seasons_manager = SeasonManager()
        self.all_seasons_info: list = []
        self.title_info      = None

        m = re.search(r'/detail/([^/]+)/', url)
        self.compact_id = m.group(1) if m else ''

    def _atf(self, data: dict) -> dict:
        try:
            return data["page"][0]["assembly"]["body"][0]["props"]["atf"]["state"]
        except Exception:
            return {}

    def _btf(self, data: dict) -> dict:
        try:
            return data["page"][0]["assembly"]["body"][0]["props"]["btf"]["state"]
        except Exception:
            return {}

    def _fetch_season_data(self, cid: str) -> dict:
        """Fetch raw SPA JSON for a given compact season/series ID."""
        url = (f"{BASE_URL}/detail/{cid}/ref=atv_dp_season_select_s2?dvWebSPAClientVersion={SPA_VERSION}")
        return get_json(url, get_spa_headers(cid))

    def collect_info_title(self) -> None:
        """
        Retrieve series-level metadata and build the seasons list.
        """
        try:
            data = self._fetch_season_data(self.compact_id)
            atf = self._atf(data)
            tid = atf.get("pageTitleId", "")
            hd = atf.get("detail", {}).get("headerDetail", {}).get(tid, {})

            if self.series_name is None:
                self.series_name = hd.get("parentTitle") or hd.get("title", "Unknown Series")

            self.title_info = {"id": tid, "title": self.series_name}

            raw_seasons = atf.get("seasons", {}).get(tid, [])
            self.all_seasons_info = [{
                    "number": s["sequenceNumber"],
                    "name": s["displayName"],
                    "id": s["seasonId"],
                    "compact": s["seasonLink"].split("/detail/")[1].split("/")[0],
                } for s in raw_seasons
            ]

            # Fallback: single-season series
            if not self.all_seasons_info:
                self.all_seasons_info = [{
                    "number": hd.get("seasonNumber", 1),
                    "name": f"Season {hd.get('seasonNumber', 1)}",
                    "id": tid,
                    "compact": self.compact_id,
                }]

            for s in sorted(self.all_seasons_info, key=lambda x: x["number"]):
                self.seasons_manager.add(Season(
                    id=s["id"],
                    number=s["number"],
                    name=s["name"],
                    slug=s["compact"],
                ))

        except Exception as e:
            print(f"[primevideo] Error collecting series info: {e}")
            raise

    def collect_info_season(self, number_season: int) -> None:
        """
        Retrieve and attach episode data for a specific season number.

        Args:
            number_season (int): Season number to populate.
        """
        try:
            if not self.seasons_manager.seasons:
                self.collect_info_title()

            season = self.seasons_manager.get_season_by_number(number_season)
            if not season:
                print(f"[primevideo] Season {number_season} not found in manager.")
                return

            season_compact = season.slug or self.compact_id
            data = self._fetch_season_data(season_compact)
            btf  = self._btf(data)

            raw_episodes = btf.get("detail", {}).get("detail", {})
            episodes = sorted([
                    (ep_id, ep)
                    for ep_id, ep in raw_episodes.items()
                    if ep.get("titleType") == "episode"
                ], key=lambda x: x[1].get("episodeNumber", 0),
            )

            if not episodes:
                print(f"[primevideo] No episodes found for season {number_season}.")
                return

            for ep_id, ep in episodes:
                season.episodes.add(Episode(
                    id=ep_id,
                    number=ep.get("episodeNumber"),
                    name=ep.get("title", f"Episode {ep.get('episodeNumber')}"),
                    description=ep.get("synopsis", ""),
                    duration=ep.get("runtime", ""),
                    poster=ep.get("images", {}).get("packshot", ""),
                    url=f"{BASE_URL}/detail/{ep_id}/",
                ))

        except Exception as e:
            print(f"[primevideo] Error collecting episodes for season {number_season}: {e}")
            raise

    def getNumberSeason(self) -> int:
        if not self.seasons_manager.seasons:
            self.collect_info_title()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        season = self.seasons_manager.get_season_by_number(season_number)
        if not season:
            print(f"[primevideo] Season {season_number} not found.")
            return []
        if not season.episodes.episodes:
            self.collect_info_season(season_number)
        return season.episodes.episodes