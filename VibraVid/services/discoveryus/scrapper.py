# 22.12.25

import logging

from VibraVid.utils.http_client import create_client
from VibraVid.services._base.object import SeasonManager, Episode, Season

from .client import get_api


class GetSerieInfo:
    def __init__(self, show_alternate_id, show_id):
        """
        Initialize series scraper for Discovery+
        
        Args:
            show_alternate_id (str): The alternate ID of the show (e.g., 'homestead-rescue-discovery')
            show_id (str): The numeric ID of the show
        """
        self.api = get_api()
        self.show_alternate_id = show_alternate_id
        self.show_id = show_id
        self.series_name = ""
        self.seasons_manager = SeasonManager()
        self.n_seasons = 0
        self.collection_id = None
        self._get_show_info()
        
    def _get_show_info(self):
        """Get show information including number of seasons and collection ID"""
        try:
            response = create_client(headers=self.api.get_request_headers()).get(
                f'https://us1-prod-direct.go.discovery.com/cms/routes/show/{self.show_alternate_id}',
                params={
                    'include': 'default',
                    'decorators': 'viewingHistory,isFavorite,playbackAllowed'
                },
                cookies=self.api.get_cookies()
            )
            response.raise_for_status()
            data = response.json()
            
            # Get series name from first show element
            for element in data.get('included', []):
                if element.get('type') == 'show':
                    self.series_name = element.get('attributes', {}).get('name', '')
                    break
            
            # Get number of seasons
            filters = data.get('included', [])[4].get('attributes', {}).get('component', {}).get('filters', [])
            if filters:
                self.n_seasons = int(filters[0].get('initiallySelectedOptionIds', [0])[0])
            
            # Get collection ID
            for element in data.get('included', []):
                if element.get('type') == 'collection':
                    self.collection_id = element.get('id')
                    #print(f"Collection ID: {self.collection_id}")
                    #break
                    
            return True
            
        except Exception as e:
            logging.error(f"Failed to get show info: {e}")
            return False
    
    def _get_season_episodes(self, season_number):
        """
        Get episodes for a specific season
        
        Args:
            season_number (int): Season number
        """
        try:
            response = create_client(headers=self.api.get_request_headers()).get(
                f'https://us1-prod-direct.go.discovery.com/cms/collections/{self.collection_id}',
                params={
                    'include': 'default',
                    'decorators': 'viewingHistory,isFavorite,playbackAllowed',
                    'pf[seasonNumber]': season_number,
                    'pf[show.id]': self.show_id
                },
                cookies=self.api.get_cookies()
            )
            response.raise_for_status()
            
            data = response.json()
            episodes = []
            
            for element in data.get('included', []):
                if element.get('type') == 'video':
                    attributes = element.get('attributes', {})
                    if 'episodeNumber' in attributes:
                        episodes.append({
                            'id': attributes.get('alternateId'),
                            'video_id': element.get('id'),
                            'name': attributes.get('name'),
                            'episode_number': attributes.get('episodeNumber'),
                            'duration': attributes.get('videoDuration', 0) // 60000
                        })
            
            # Sort by episode number
            episodes.sort(key=lambda x: x['episode_number'])
            return episodes
            
        except Exception as e:
            logging.error(f"Failed to get episodes for season {season_number}: {e}")
            return []
    
    def collect_season(self):
        """Collect all seasons and episodes"""
        try:
            for season_num in range(1, self.n_seasons + 1):
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
            logging.error(f"Error in collect_season: {e}")


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