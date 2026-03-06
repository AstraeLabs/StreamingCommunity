# 16.12.25

import os
import re
from typing import Tuple

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import config_manager, start_message
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import map_movie_title, map_episode_title, map_season_name, map_series_name
from VibraVid.services._base.tv_download_manager import process_season_selection, process_episode_download

from VibraVid.core.downloader import HLS_Downloader

from .client import get_bearer_token, get_playback_url
from .scrapper import GetSerieInfo


console = Console()
msg = Prompt()
extension_output = config_manager.config.get("PROCESS", "extension")


def extract_content_id(url: str) -> str:
    """Extract content ID from Tubi TV URL"""
    # URL format: https://tubitv.com/movies/{content_id}/{slug}
    match = re.search(r'/movies/(\d+)/', url)
    if match:
        return match.group(1)
    return None


def download_film(select_title: Entries) -> Tuple[str, bool]:
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{select_title.name} \n")

    # Extract content ID from URL
    content_id = extract_content_id(select_title.url)
    if not content_id:
        console.print("[red]Error: Could not extract content ID from URL")
        return None, True

    # Get bearer token
    try:
        bearer_token = get_bearer_token()
    except Exception as e:
        console.print(f"[red]Error getting bearer token: {e}")
        return None, True

    # Get master playlist URL
    try:
        master_playlist, license_url = get_playback_url(content_id, bearer_token)
    except Exception as e:
        console.print(f"[red]Error getting playback URL: {e}")
        return None, True

    # Define the filename and path for the downloaded film
    title_name = f"{map_movie_title(select_title.name, select_title.year)}.{extension_output}"
    title_path = os.path.join(site_constants.MOVIE_FOLDER, title_name.replace(f".{extension_output}", ""))

    # HLS Download
    return HLS_Downloader(
        m3u8_url=master_playlist,
        output_path=os.path.join(title_path, title_name),
        license_url=license_url
    ).start()


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie, bearer_token):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    series_folder_name = map_series_name(scrape_serie.series_name, getattr(scrape_serie, 'year', None))
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    # Define filename and path for the downloaded video
    episode_name = f"{map_episode_title(scrape_serie.series_name, index_season_selected, index_episode_selected, obj_episode.name)}.{extension_output}"
    episode_path = os.path.join(site_constants.SERIES_FOLDER, series_folder_name, map_season_name(index_season_selected))

    # Get master playlist URL
    try:
        master_playlist, license_url = get_playback_url(obj_episode.id, bearer_token)
    except Exception as e:
        console.print(f"[red]Error getting playback URL: {e}")
        return None, True

    # Download the episode
    return HLS_Downloader(
        m3u8_url=master_playlist,
        output_path=os.path.join(episode_path, episode_name),
        license_url=license_url
    ).start()


def download_series(select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie = None) -> None:
    """
    Handle downloading a complete series.

    Parameters:
        - select_season (Entries): Series metadata from search
        - season_selection (str, optional): Pre-defined season selection that bypasses manual input
        - episode_selection (str, optional): Pre-defined episode selection that bypasses manual input
        - scrape_serie (Any, optional): Pre-existing scraper instance to avoid recreation
    """
    start_message()
    if scrape_serie is None:
        bearer_token = get_bearer_token()
        scrape_serie = GetSerieInfo(select_season.url, bearer_token, select_season.name)
        scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)

    # Create callback function for downloading episodes
    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""
        
        # Create callback for downloading individual videos
        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie, bearer_token)
        
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