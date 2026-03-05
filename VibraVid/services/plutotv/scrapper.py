# 26.11.2025

import logging

from VibraVid.utils.http_client import create_client
from VibraVid.services._base.object import SeasonManager, Season, Episode

from .client import get_api


class GetSerieInfo:
    def __init__(self, url):
        """
        Initialize series scraper for Pluto TV
        
        Args:
            url (str): The full URL to the seasons endpoint
        """
        self.api = get_api()
        self.url = url
        self.series_name = ""
        self.seasons_manager = SeasonManager()
        self.seasons_data = {}
        self._get_series_info()
        
    def _get_series_info(self):
        """Get series information including seasons"""
        try:
            params = {'offset': '1000', 'page': '1'}
            response = create_client(headers=self.api.get_request_headers()).get(self.url, params=params)
            response.raise_for_status()
            json_response = response.json()
            
            self.series_name = json_response.get('name', 'Unknown Series')
            seasons_array = json_response.get('seasons', [])
            
            if not seasons_array:
                logging.warning("No seasons found in JSON response")
                return
            
            # Process each season
            for season_obj in seasons_array:
                season_number = season_obj.get('number')
                if season_number is None:
                    logging.warning("Season without number found, skipping")
                    continue
                
                # Store season data
                self.seasons_data[str(season_number)] = season_obj
                
                # Add season to manager
                season = self.seasons_manager.add(Season(
                    number=season_number,
                    name=f"Season {season_number}",
                    id=f"season-{season_number}"
                ))
                
                # Process episodes for this season
                episodes = season_obj.get('episodes', [])
                for episode in episodes:
                    season.episodes.add(Episode(
                        id=episode.get('_id'),
                        video_id=episode.get('_id'),
                        name=episode.get('name', f"Episode {episode.get('number')}"),
                        number=episode.get('number'),
                        duration=round(episode.get('duration', 0) / 1000 / 60) if episode.get('duration') else 0
                    ))
                
        except Exception as e:
            logging.error(f"Error collecting series info: {e}")
            raise
    
    
    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """Get total number of seasons"""
        return len(self.seasons_manager.seasons)
    
    def getEpisodeSeasons(self, season_number: int) -> list:
        """Get all episodes for a specific season"""
        season = self.seasons_manager.get_season_by_number(season_number)
        if season:
            return season.episodes.episodes

        return []
    
    def selectEpisode(self, season_number: int, episode_index: int) -> Episode:
        """Get information for a specific episode"""
        episodes = self.getEpisodeSeasons(season_number)
        if not episodes or episode_index < 0 or episode_index >= len(episodes):
            logging.error(f"Episode index {episode_index} out of range for season {season_number}")
            return None
        
        return episodes[episode_index]