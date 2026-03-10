# 22.12.25

import logging

from VibraVid.utils.http_client import create_client_curl
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
        self.series_name = ""
        self.seasons_manager = SeasonManager()
        self.n_seasons = 0
        self.seasons_list = []
        self._all_episodes = None
        self._get_show_info()
        
    def _fetch_all_episodes(self):
        """Fetch all episodes for the show"""
        try:
            url = f"{self.client.base_url}/cms/routes/show/{self.show_id}"
            params = {
                'include': 'default',
                'decorators': 'viewingHistory,badges,isFavorite,contentAction',
            }
            
            response = create_client_curl(headers=self.client.headers, cookies=self.client.cookies).get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            
            # Find show info
            show_info = next((x for x in data['included'] if x.get('attributes', {}).get('alternateId', '') == self.show_id), None)
            if not show_info:
                logger.error(f"Show info not found for: {self.show_id}")
                return []
                
            show_name = show_info.get('attributes', {}).get('name', 'Unknown')
            self.series_name = show_name

            # first try collecting any episodes already included in the response
            direct_episodes = []
            for item in data.get('included', []):
                if item.get('type') == 'video':
                    attrs = item.get('attributes', {})
                    if attrs.get('seasonNumber') is not None and attrs.get('episodeNumber') is not None:
                        relationships = item.get('relationships', {})
                        edit_id = relationships.get('edit', {}).get('data', {}).get('id') or item.get('id')
                        direct_episodes.append({
                            'id': edit_id,
                            'show': show_name,
                            'season': attrs.get('seasonNumber'),
                            'episode': attrs.get('episodeNumber'),
                            'title': attrs.get('name'),
                        })

            # start accumulation with direct episodes
            all_episodes = list(direct_episodes)

            # Find episodes content (used for paginated/filtered collections)
            episodes_aliases = ['show-page-rail-episodes-tabbed-content', 'generic-show-episodes']
            content = next((
                x for x in data['included'] 
                if any(alias in x.get('attributes', {}).get('alias', '') for alias in episodes_aliases)
            ), None)

            # fallback: any included object with a seasonNumber filter
            if not content:
                for x in data.get('included', []):
                    attrs = x.get('attributes', {})
                    comp = attrs.get('component') if isinstance(attrs, dict) else None
                    if comp and isinstance(comp.get('filters'), list):
                        if any(f.get('id') == 'seasonNumber' for f in comp.get('filters', [])):
                            content = x
                            break

            if not content and not direct_episodes:
                logger.error(f"No episodes found for show {self.show_id}, falling back to direct scan failed too")
                return []

            if content:
                content_id = content.get('id')
                show_params = content.get('attributes', {}).get('component', {}).get('mandatoryParams', '')

                # Find the season filter
                season_filter = next((f for f in content.get('attributes', {}).get('component', {}).get('filters', []) if f.get('id') == 'seasonNumber'), None)
                if not season_filter:
                    logger.error(f"Season filter not found for show {self.show_id}")
                    season_params = []
                else:
                    season_params = [x.get('parameter') for x in season_filter.get('options', [])]
            else:
                season_params = []

            # Get episodes for each season (collection fetch)
            for season_param in season_params:
                coll_url = f"{self.client.base_url}/cms/collections/{content_id}?{season_param}&{show_params}"
                coll_params = {
                    'include': 'default',
                    'decorators': 'viewingHistory,badges,isFavorite,contentAction',
                }
                
                response = create_client_curl(headers=self.client.headers, cookies=self.client.cookies).get(coll_url, params=coll_params)
                response.raise_for_status()
                
                season_data = response.json()
                
                for item in season_data.get('included', []):
                    if item.get('type') == 'video' and item.get('attributes', {}).get('videoType') == 'EPISODE':
                        attrs = item['attributes']
                        relationships = item.get('relationships', {})
                        edit_id = relationships.get('edit', {}).get('data', {}).get('id') or item.get('id')
                        
                        episode = {
                            'id': edit_id,
                            'show': show_name,
                            'season': attrs.get('seasonNumber'),
                            'episode': attrs.get('episodeNumber'),
                            'title': attrs.get('name'),
                        }
                        all_episodes.append(episode)
            
            # deduplicate by episode id (collection fetch may re‑include direct entries)
            unique = {}
            for ep in all_episodes:
                unique[ep['id']] = ep
            all_episodes = list(unique.values())

            all_episodes.sort(key=lambda x: (x['season'], x['episode']))
            return all_episodes
            
        except Exception as e:
            logger.error(f"Error in _fetch_all_episodes: {e}")
            return []

    def _get_show_info(self):
        """Get show information and cache episodes list"""
        try:
            if self._all_episodes is None:
                self._all_episodes = self._fetch_all_episodes()
            
            if not self._all_episodes:
                return False
            
            # Get number of seasons from actual distinct season numbers
            seasons_set = set(ep['season'] for ep in self._all_episodes)
            self.n_seasons = len(seasons_set)
            self.seasons_list = sorted(list(seasons_set))
            
            return True

        except Exception as e:
            logger.error(f"Failed to get show info: {e}")
            return False
    
    def _get_season_episodes(self, season_number: int):
        """
        Get episodes for a specific season from cache
        
        Args:
            season_number (int): Season number
            
        Returns:
            list: List of episodes for the season
        """
        try:
            if self._all_episodes is None:
                self._all_episodes = self._fetch_all_episodes()
            
            if not self._all_episodes:
                return []
            
            # Filter episodes for the specific season
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
            
            # Sort by episode number
            season_episodes.sort(key=lambda x: x['episode_number'])
            logger.info(f"Using cached n_episodes: {len(season_episodes)} for season: {season_number}")
            return season_episodes
        
        except Exception as e:
            logger.error(f"Failed to get episodes for season {season_number}: {e}")
            return []
    
    def collect_season(self):
        """Collect all seasons and episodes"""
        try:
            # Iterate over actual season numbers instead of range(1, n+1)
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
                            
        except Exception as e:
            logger.error(f"Error in collect_season: {e}")

    
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
        if season:
            return season.episodes.episodes
        
        return []