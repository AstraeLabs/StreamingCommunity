# 20.05.26

import logging
import threading

from VibraVid.provider.tmdb import tmdb
from VibraVid.services._base.object import Episode, Season, SeasonManager

logger = logging.getLogger(__name__)


class GetSerieInfo:
    def __init__(self, series_name: str, tmdb_id: str = None, year: int = None):
        self.tmdb_id = tmdb_id
        self.series_name = series_name
        self.series_display_name = series_name
        self.year = year
        self.seasons_manager = SeasonManager()
        self._loaded = False
        self._load_lock = threading.Lock()

    def _load(self):
        with self._load_lock:
            self._load_locked()

    def _load_locked(self):
        if self._loaded:
            return

        self._loaded = True
        try:
            details = tmdb._make_request(f"tv/{self.tmdb_id}", {"language": "it"}) or {}

            if details.get("name"):
                self.series_name = details["name"]
                self.series_display_name = details["name"]

            first_air_date = details.get("first_air_date") or ""
            if first_air_date:
                self.year = first_air_date

            for raw_season in details.get("seasons", []):
                season_number = raw_season.get("season_number", 0)
                if season_number in (0, None):
                    continue

                self.seasons_manager.add(
                    Season(
                        id=raw_season.get("id"),
                        number=season_number,
                        name=raw_season.get("name") or f"Season {season_number}",
                        slug=raw_season.get("name") or f"Season {season_number}",
                        type="season",
                        tmdb_id=raw_season.get("id"),
                    )
                )

        except Exception as error:
            logger.error(f"[Altadefinizione] TMDB series load failed: {error}")

    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """Get the total number of seasons available for the series."""
        self._load()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        """Get all episodes for a specific season."""
        self._load()

        season_details = tmdb._make_request(f"tv/{self.tmdb_id}/season/{season_number}", {"language": "it"}) or {}
        episodes = []

        for raw_episode in season_details.get("episodes", []):
            episodes.append(
                Episode(
                    id=raw_episode.get("id"),
                    number=raw_episode.get("episode_number"),
                    name=raw_episode.get("name"),
                    duration=raw_episode.get("runtime"),
                    image=raw_episode.get("still_path"),
                    poster=raw_episode.get("still_path"),
                    year=self.year,
                )
            )

        return episodes
