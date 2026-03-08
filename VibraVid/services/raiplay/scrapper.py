# 21.05.24

import re
import logging

from VibraVid.utils.http_client import create_client, get_headers
from VibraVid.services._base.object import SeasonManager, Episode, Season


class GetSerieInfo:
    def __init__(self, path_id: str):
        """Initialize the GetSerieInfo class."""
        self.base_url = "https://www.raiplay.it"
        self.path_id = path_id
        self.series_name = None
        self.prog_description = None
        self.prog_year = None
        self.seasons_manager = SeasonManager()
        self.season_block_mapping = {}      # Map season number to block_id
        self.all_seasons_data = []          # Store all seasons before filtering

    def collect_info_title(self) -> None:
        """Get series info including seasons from all multimedia blocks."""
        try:
            path = self.path_id.lstrip('/')
            program_url = f"{self.base_url}/{path}"
            response = create_client(headers=get_headers()).get(program_url)
            
            # If 404, content is not yet available
            if response.status_code == 404:
                logging.error(f"Content not yet available: {program_url}")
                return
                
            response.raise_for_status()
            json_data = response.json()

            # Get basic program info
            program_info = json_data.get('program_info', {})
            self.prog_description = program_info.get('vanity', '') or program_info.get('description', '')
            self.prog_year = program_info.get('year', '')
            self.series_name = program_info.get('title', '') or program_info.get('name', '')
            
            # Collect all seasons from all multimedia blocks
            self.all_seasons_data = []
            blocks_found = {}
            
            for block in json_data.get('blocks', []):
                block_type = block.get('type', '')
                block_name = block.get('name', 'N/A')
                block_id = block.get('id', '')

                # Skip Clip and Extra blocks
                if block_name.lower() == 'clip' or block_name.lower() == 'extra':
                    print(" - Skipping Clip or Extra block")
                    continue
                
                # Only process multimedia blocks with sets
                if block_type == 'RaiPlay Multimedia Block' and 'sets' in block:
                    sets = block.get('sets', [])
                    
                    for season_set in sets:
                        episode_size = season_set.get('episode_size', {})
                        episode_count = episode_size.get('number', 0)
                        
                        # Only add sets with episodes
                        if episode_count > 0:
                            self.all_seasons_data.append({
                                'season_set': season_set,
                                'block_id': block_id,
                                'block_name': block_name
                            })
                            
                            # Track which blocks we found
                            if block_name not in blocks_found:
                                blocks_found[block_name] = 0
                            blocks_found[block_name] += 1
            
            # Add all collected seasons without any filtering (oldest first)
            for season_data in self.all_seasons_data:
                self._add_season(
                    season_data['season_set'],
                    season_data['block_id'],
                    season_data['block_name']
                )

        except Exception as e:
            logging.error(f"Unexpected error collecting series info: {e}")

    def _add_season(self, season_set: dict, block_id: str, block_name: str):
        """Add a season combining set name and block name."""
        set_name = season_set.get('name', '')
        match = re.search(r'(\d+)', set_name)
        if match:
            season_number = int(match.group(1))
        else:
            season_number = len(self.seasons_manager.seasons) + 1
        
        # Store block_id mapping
        self.season_block_mapping[season_number] = {
            'block_id': block_id,
            'set_id': season_set.get('id')
        }
        
        self.seasons_manager.add(Season(
            id=season_set.get('id'),
            number=int(season_number),
            name=set_name,
            type=block_name
        ))

    def collect_info_season(self, number_season: int) -> None:
        """Get episodes for a specific season using episodes.json endpoint."""
        try:
            season = self.seasons_manager.get_season_by_number(number_season)
            block_info = self.season_block_mapping[number_season]
            block_id = block_info['block_id']
            set_id = block_info['set_id']
            
            # Build episodes endpoint URL
            base_path = self.path_id.lstrip('/').replace('.json', '')
            url = f"{self.base_url}/{base_path}/{block_id}/{set_id}/episodes.json"
            
            response = create_client(headers=get_headers()).get(url)
            response.raise_for_status()
            
            episodes_data = response.json()
            
            # Navigate nested structure to find cards
            cards = []
            seasons = episodes_data.get('seasons', [])
            if seasons:
                for season_data in seasons:
                    episodes = season_data.get('episodes', [])
                    for episode in episodes:
                        cards.extend(episode.get('cards', []))
            
            # Fallback to direct cards if nested structure not found
            if not cards:
                cards = episodes_data.get('cards', [])

            # Add episodes to season
            for ep in cards:
                video_url = ep.get('video_url', '')
                mpd_id = ''
                if video_url and '=' in video_url:
                    mpd_id = video_url.split("=")[1].strip()
                
                weblink = ep.get('weblink', '') or ep.get('url', '')
                episode_url = f"{self.base_url}{weblink}" if weblink else ''
                
                season.episodes.add(Episode(
                    id=ep.get('id'),
                    number=ep.get('episode'),
                    name=ep.get('episode_title') or ep.get('name') or ep.get('toptitle'),
                    duration=ep.get('duration') or ep.get('duration_in_minutes'),
                    url=episode_url,
                    mpd_id=mpd_id
                ))

        except Exception as e:
            logging.error(f"Error collecting episodes for season {number_season}: {e}")
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
            logging.error(f"Season {season_number} not found")
            return []
            
        if not season.episodes.episodes:
            self.collect_info_season(season_number)
            
        return season.episodes.episodes