# 22.12.25

import os

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import os_manager, config_manager, start_message
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import map_episode_title, map_season_name, map_series_name
from VibraVid.services._base.tv_download_manager import process_season_selection, process_episode_download

from VibraVid.core.downloader import DASH_Downloader

from .scrapper import GetSerieInfo
from .client import get_playback_info, generate_license_headers


msg = Prompt()
console = Console()
extension_output = config_manager.config.get("PROCESS", "extension")


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    series_folder_name = map_series_name(scrape_serie.series_name, getattr(scrape_serie, 'year', None))
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")
    
    # Define output path
    episode_name = f"{map_episode_title(scrape_serie.series_name, index_season_selected, index_episode_selected, obj_episode.name)}.{extension_output}"
    episode_path = os_manager.get_sanitize_path(
        os.path.join(site_constants.SERIES_FOLDER, series_folder_name, map_season_name(index_season_selected))
    )
    
    # Get playback information using video_id
    playback_info = get_playback_info(obj_episode.video_id)
    if str(playback_info['type']).strip().lower() != 'dash' or playback_info['license_url'] is None:
        console.print(f"[red]Unsupported streaming type. Playback info: {playback_info}")
        return None, False
    
    # Generate license headers
    license_headers = generate_license_headers(playback_info['license_token'])
    
    # Download the episode
    return DASH_Downloader(
        mpd_url=playback_info['mpd_url'],
        license_url=playback_info['license_url'],
        license_headers=license_headers,
        output_path=os.path.join(episode_path, episode_name),
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
        id_parts = select_season.id.split('|')
        scrape_serie = GetSerieInfo(id_parts[1], id_parts[0])
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