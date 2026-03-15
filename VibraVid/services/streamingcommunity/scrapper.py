# 01.03.24

import json
import logging

from bs4 import BeautifulSoup

from VibraVid.utils.http_client import create_client, get_headers
from VibraVid.services._base.object import SeasonManager, Episode, Season


logger = logging.getLogger(__name__)


class GetSerieInfo:
    def __init__(self, url, media_id: int = None, series_name: str = None, year: int = None, provider_language: str = "it", series_display_name: str = None):
        """
        Initialize the GetSerieInfo class for scraping TV series information.
        
        Args:
            - url (str): The URL of the streaming site.
            - media_id (int): Unique identifier for the media
            - series_name (str): Slug of the TV series
            - series_display_name (str): Name of the TV series
        """
        self.is_series = False
        self.headers = get_headers()
        self.url = url
        self.media_id = media_id
        self.year = year
        self.seasons_manager = SeasonManager()
        self.provider_language = provider_language

        if series_name is not None:
            self.is_series = True
            self.series_name = series_name  # slug, used for URL building
            self.series_display_name = series_display_name if series_display_name is not None else series_name

    def collect_info_title(self) -> None:
        """
        Retrieve general information about the TV series from the streaming site.
        
        Raises:
            Exception: If there's an error fetching series information
        """
        try:
            response = create_client(headers=self.headers).get(f"{self.url}/titles/{self.media_id}-{self.series_name}")
            response.raise_for_status()

            # Extract series info from JSON response
            soup = BeautifulSoup(response.text, "html.parser")
            json_response = json.loads(soup.find("div", {"id": "app"}).get("data-page"))
            self.version = json_response['version']
            
            # Extract information about available seasons
            title_data = json_response.get("props", {}).get("title", {})
            
            # Save general series information
            self.title_info = title_data
            
            # Extract available seasons and add them to SeasonManager
            seasons_data = title_data.get("seasons", [])
            for season_data in seasons_data:
                self.seasons_manager.add(Season(
                    id=season_data.get('id'),
                    number=season_data.get('number'),
                    name=f"Season {season_data.get('number')}",
                    slug=season_data.get('slug')
                ))

        except Exception as e:
            logger.error(f"Error collecting series info: {e}")
            raise

    def collect_info_season(self, number_season: int) -> None:
        """
        Retrieve episode information for a specific season.
        
        Args:
            number_season (int): Season number to fetch episodes for
        
        Raises:
            Exception: If there's an error fetching episode information
        """
        try:
            # Get the season object from SeasonManager
            season = self.seasons_manager.get_season_by_number(number_season)
            if not season:
                logger.error(f"Season {number_season} not found")
                return

            custom_headers = self.headers.copy()
            custom_headers.update({
                'x-inertia': 'true',
                'x-inertia-version': self.version,
            })
            response = create_client(headers=custom_headers).get(f"{self.url}/titles/{self.media_id}-{self.series_name}/season-{number_season}")

            # Extract episodes from JSON response
            json_response = response.json().get('props', {}).get('loadedSeason', {}).get('episodes', [])
                
            # Add each episode to the corresponding season's episode manager
            for ep in json_response:
                season.episodes.add(Episode(
                    id=ep.get('id'),
                    video_id=ep.get('id'),
                    number=ep.get('number'),
                    name=ep.get('name'),
                    duration=ep.get('duration')
                ))

        except Exception as e:
            logger.error(f"Error collecting episodes for season {number_season}: {e}")
            raise

    
    # ------------- FOR GUI -------------
    def getNumberSeason(self) -> int:
        """
        Get the total number of seasons available for the series.
        """
        if not self.seasons_manager.seasons:
            self.collect_info_title()
            
        return len(self.seasons_manager.seasons)
    
    def getEpisodeSeasons(self, season_number: int) -> list:
        """
        Get all episodes for a specific season.
        """
        season = self.seasons_manager.get_season_by_number(season_number)

        if not season:
            logger.error(f"Season {season_number} not found")
            return []
            
        if not season.episodes.episodes:
            self.collect_info_season(season_number)
            
        return season.episodes.episodes