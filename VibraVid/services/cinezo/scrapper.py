# 17.04.26

import logging

from VibraVid.services._base.object import SeasonManager, Season, Episode, EpisodeManager
from VibraVid.utils.tmdb_client import tmdb_client


logger = logging.getLogger(__name__)


class GetSerieInfo:
    """
    Fetches season/episode metadata for a Cinezo series via TMDB.
    """

    def __init__(self, tmdb_id: int, series_name: str):
        self.tmdb_id     = tmdb_id
        self.series_name = series_name
        self.series_year = None
        self.seasons_manager = SeasonManager()
        self._loaded = False

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            details = tmdb_client._make_request(f"tv/{self.tmdb_id}", {"language": "it"}) or {}
            first_air = details.get('first_air_date', '') or ''
            if first_air:
                self.series_year = int(first_air[:4])
                
            for raw_s in details.get('seasons', []):
                sn = raw_s.get('season_number', 0)
                if sn == 0:
                    continue  # skip specials

                ep_count = raw_s.get('episode_count', 0)
                em = EpisodeManager()
                for ep_num in range(1, ep_count + 1):
                    em.add(Episode(
                        id     = ep_num,
                        number = ep_num,
                        name   = f"Episodio {ep_num}",
                    ))

                s = Season(id=sn, number=sn, name=raw_s.get('name', f"Stagione {sn}"), slug='')
                s.episodes = em
                self.seasons_manager.add(s)
        except Exception as e:
            logger.error(f"[Cinezo] TMDB series load failed: {e}")

    def getNumberSeason(self) -> int:
        self._load()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        self._load()
        season = self.seasons_manager.get_season_by_number(season_number)
        if not season:
            return []
        return season.episodes.episodes
