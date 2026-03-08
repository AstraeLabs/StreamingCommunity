# 22.12.25

import os

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import os_manager, config_manager, start_message
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import map_episode_path
from VibraVid.services._base.tv_download_manager import process_season_selection, process_episode_download

from VibraVid.core.downloader import DASH_Downloader

from .client import get_client
from .scrapper import GetSerieInfo


msg = Prompt()
console = Console()
extension_output = config_manager.config.get("PROCESS", "extension")


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    client = get_client()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")
    
    # Define output path
    path_components, filename = map_episode_path(scrape_serie.series_name, getattr(scrape_serie, 'year', None), index_season_selected, index_episode_selected, obj_episode.name)
    episode_path = os_manager.get_sanitize_path(os.path.join(site_constants.SERIES_FOLDER, *path_components))
    episode_name = f"{filename}.{extension_output}"
    
    # Get playback information using the new client
    playback_info = client.get_playback_info(obj_episode.id)
    license_headers = client.generate_license_headers(playback_info['license_token']) if playback_info.get('license_token') else {}
    
    return DASH_Downloader(
        mpd_url=playback_info['manifest'],
        license_url=playback_info['license'],
        license_headers=license_headers,
        output_path=os.path.join(episode_path, episode_name),
        drm_preference="widevine" if "widevine" in playback_info['license'].lower() else "playready"
    ).start()

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
        scrape_serie = GetSerieInfo(select_season.id)
        scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)
    
    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""
        
        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie)
        
        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=download_video_callback,
            download_all=download_all,
            episode_selection=episode_selection
        )

    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback
    )