# 13.06.24

import os

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import config_manager, start_message
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import map_episode_path
from VibraVid.services._base.tv_download_manager import process_season_selection, process_episode_download

from VibraVid.core.downloader import HLS_Downloader

from VibraVid.player.supervideo import VideoSource

from .scrapper import GetSerieInfo


msg = Prompt()
console = Console()
extension_output = config_manager.config.get("PROCESS", "extension")


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{scrape_serie.tv_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{str(index_season_selected)}E{index_episode_selected}) \n")

    # Define filename and path for the downloaded video
    path_components, filename = map_episode_path(scrape_serie.tv_name, getattr(scrape_serie, 'year', None), index_season_selected, index_episode_selected, obj_episode.name)
    episode_path = os.path.join(site_constants.SERIES_FOLDER, *path_components)
    episode_name = f"{filename}.{extension_output}"

    # Get the master playlist
    video_source = VideoSource(obj_episode.url)
    master_playlist = video_source.get_playlist()
    
    output_path, _ = HLS_Downloader(
        m3u8_url=master_playlist, 
        output_path=os.path.join(episode_path, episode_name)
    ).start()

    if output_path is None:

        # Get the master playlist
        video_source = VideoSource(obj_episode.url_2)
        master_playlist = video_source.get_playlist()

        return HLS_Downloader(
            m3u8_url=master_playlist,
            output_path=os.path.join(episode_path, episode_name),
            headers={
                'Origin': 'https://dropload.pro',
                'Referer': 'https://dropload.pro/',
            }
        ).start()

    return output_path, _

def download_series(select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie = None) -> None:
    """
    Handle downloading a complete series
    
    Parameters:
        select_season (Entries): Series metadata from search
        season_selection (str, optional): Pre-defined season selection
        episode_selection (str, optional): Pre-defined episode selection
        scrape_serie (Any, optional): Pre-existing scraper instance to avoid recreation
    """
    start_message()
    if not scrape_serie:
        scrape_serie = GetSerieInfo(select_season)
        scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)
    
    # Create callback function for downloading episodes
    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""
        
        # Create callback for downloading individual videos
        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie)
        
        # Use the process_episode_download function
        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=download_video_callback,
            download_all=download_all,
            episode_selection=episode_selection
        )

    # Use the process_season_selection function
    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback
    )
