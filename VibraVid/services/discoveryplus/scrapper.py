# 22.12.25

import logging

from VibraVid.utils.http_client import create_client
from VibraVid.services._base.object import SeasonManager, Episode, Season

from .client import get_client


logger = logging.getLogger(__name__)


class GetSerieInfo:
    def __init__(self, show_id: str):
        """
        Initialize series scraper for Discovery+
        
        Args:
            show_id (str): The alternate ID of the show
        """
        self.client = get_client()
        self.show_id = show_id
        self.universal_id = None
        self.series_name = ""
        self.seasons_manager = SeasonManager()
        self.n_seasons = 0
        self.seasons_list = []
        self._all_episodes = None
        self._get_show_info()

    def _fetch_all_episodes(self):
        """Fetch all episodes for the show"""
        try:
            # Fetch show data (includes season filter information)
            url = f"{self.client.base_url}/cms/routes/show/{self.show_id}"
            params = {
                'include': 'default',
                'decorators': 'viewingHistory,badges,isFavorite,contentAction'
            }
            response = create_client(headers=self.client.headers, cookies=self.client.cookies).get(url, params=params)
            response.raise_for_status()
            data = response.json()

            # Extract show info
            show_info = next((x for x in data['included'] if x.get('attributes', {}).get('alternateId', '') == self.show_id), None)
            if not show_info:
                logger.error(f"Show info not found for: {self.show_id}")
                return []
            self.universal_id = show_info.get('attributes', {}).get('universalId')
            self.series_name = show_info.get('attributes', {}).get('name', 'Unknown')

            # Locate the episodes content block
            content = next((x for x in data['included'] if 'show-page-rail-episodes-tabbed-content' in x.get('attributes', {}).get('alias', '')), None)
            if not content:
                logger.error(f"Episodes content block not found for show {self.show_id}")
                return []

            content_id = content.get('id')
            show_params = content['attributes']['component'].get('mandatoryParams', '')

            # Season filter options (parameters for each season)
            season_filter = next((f for f in content['attributes']['component'].get('filters', []) if f.get('id') == 'seasonNumber'), None)
            if not season_filter:
                logger.error(f"Season filter not found for show {self.show_id}")
                return []
            season_params = [opt.get('parameter') for opt in season_filter.get('options', [])]

            all_episodes = []
            for season_param in season_params:
                coll_url = f"{self.client.base_url}/cms/collections/{content_id}?{season_param}&{show_params}"
                coll_params = {
                    'include': 'default',
                    'decorators': 'viewingHistory,badges,isFavorite,contentAction',
                }
                response = create_client(headers=self.client.headers, cookies=self.client.cookies).get(coll_url, params=coll_params)
                response.raise_for_status()
                season_data = response.json()

                for item in season_data.get('included', []):
                    if item.get('type') == 'video' and item.get('attributes', {}).get('videoType') == 'EPISODE':
                        attrs = item['attributes']
                        relationships = item.get('relationships', {})
                        edit_id = relationships.get('edit', {}).get('data', {}).get('id') or item.get('id')
                        all_episodes.append({
                            'id': edit_id,
                            'show': self.series_name,
                            'season': attrs.get('seasonNumber'),
                            'episode': attrs.get('episodeNumber'),
                            'title': attrs.get('name'),
                        })

            # Sort by season and episode
            all_episodes.sort(key=lambda x: (x['season'], x['episode']))
            return all_episodes

        except Exception as e:
            logger.error(f"Error in _fetch_all_episodes: {e}")
            return []

    def _get_show_info(self):
        """Cache all episodes and set season counts."""
        try:
            if self._all_episodes is None:
                self._all_episodes = self._fetch_all_episodes()

            if not self._all_episodes:
                return False

            # Distinct seasons
            seasons_set = set(ep['season'] for ep in self._all_episodes if ep['season'] is not None)
            self.n_seasons = len(seasons_set)
            self.seasons_list = sorted(list(seasons_set))

            return True

        except Exception as e:
            logger.error(f"Failed to get show info: {e}")
            return False

    def _get_season_episodes(self, season_number: int):
        """Return episodes for a given season from the cached list."""
        if self._all_episodes is None:
            self._all_episodes = self._fetch_all_episodes()

        season_episodes = []
        for episode in self._all_episodes:
            if episode['season'] == season_number:
                season_episodes.append({
                    'id': episode['id'],
                    'video_id': episode['id'],
                    'name': episode['title'],
                    'episode_number': episode['episode'],
                    'duration': 0
                })
        season_episodes.sort(key=lambda x: x['episode_number'])
        return season_episodes

    def collect_season(self):
        """Populate the seasons_manager with all seasons and episodes."""
        for season_num in self.seasons_list:
            episodes = self._get_season_episodes(season_num)
            if episodes:
                season_obj = self.seasons_manager.add(Season(
                    number=season_num,
                    name=f"Season {season_num}",
                    id=f"season_{season_num}"
                ))
                if season_obj:
                    for ep in episodes:
                        season_obj.episodes.add(Episode(
                            id=ep.get('id'),
                            video_id=ep.get('video_id'),
                            name=ep.get('name'),
                            number=ep.get('episode_number'),
                            duration=ep.get('duration')
                        ))

    
    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """Get total number of seasons"""
        if not self.seasons_manager.seasons:
            self.collect_season()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        """Get all episodes for a specific season"""
        if not self.seasons_manager.seasons:
            self.collect_season()
        season = self.seasons_manager.get_season_by_number(season_number)
        return season.episodes.episodes if season else []