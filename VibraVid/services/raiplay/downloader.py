# 21.05.24

import os
import re
from typing import Tuple

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import config_manager, start_message
from VibraVid.utils.http_client import create_client, get_headers, get_userAgent
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import map_movie_title, map_episode_path
from VibraVid.services._base.tv_download_manager import process_season_selection, process_episode_download

from VibraVid.core.downloader import DASH_Downloader, HLS_Downloader

from VibraVid.player.mediapolisvod import VideoSource

from .client import generate_license_url
from .scrapper import GetSerieInfo


console = Console()
msg = Prompt()
extension_output = config_manager.config.get("PROCESS", "extension")


def fix_manifest_url(manifest_url: str) -> str:
    """
    Fixes RaiPlay manifest URLs to include all available quality levels.
    
    Args:
        manifest_url (str): Original manifest URL from RaiPlay
    """
    STANDARD_QUALITIES = "1200,1800,2400,3600,5000"
    pattern = r'(_,[\d,]+)(/playlist\.m3u8)'
    
    # Check if URL contains quality specification
    match = re.search(pattern, manifest_url)
    
    if match:
        fixed_url = re.sub(pattern, f'_,{STANDARD_QUALITIES}\\2', manifest_url)
        return fixed_url
    
    return manifest_url

def download_film(select_title: Entries) -> Tuple[str, bool]:
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{select_title.name} \n")

    # Extract m3u8 URL from the film's URL
    response = create_client(headers=get_headers()).get(select_title.url + ".json")
    first_item_path = "https://www.raiplay.it" + response.json().get("first_item_path")
    master_playlist = VideoSource.extract_m3u8_url(first_item_path)

    # Define the filename and path for the downloaded film
    title_name = f"{map_movie_title(select_title.name, select_title.year)}.{extension_output}"
    title_path = os.path.join(site_constants.MOVIE_FOLDER, title_name.replace(f".{extension_output}", ""))

    # HLS
    if ".mpd" not in master_playlist:
        return HLS_Downloader(
            m3u8_url=fix_manifest_url(master_playlist),
            output_path=os.path.join(title_path, title_name)
        ).start()

    # MPD
    else:
        license_url = generate_license_url(select_title.mpd_id)

        return DASH_Downloader(
            mpd_url=master_playlist,
            license_url=license_url,
            output_path=os.path.join(title_path, title_name),
        ).start()
    

def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    # Define filename and path
    path_components, filename = map_episode_path(scrape_serie.series_name, getattr(scrape_serie, 'year', None), index_season_selected, index_episode_selected, obj_episode.name)
    episode_path = os.path.join(site_constants.SERIES_FOLDER, *path_components)
    episode_name = f"{filename}.{extension_output}"

    # Get streaming URL
    master_playlist = VideoSource.extract_m3u8_url(obj_episode.url)

    if not master_playlist:
        console.print(f"[red]Error: Could not extract streaming URL for {obj_episode.name}")
        return False

    # HLS
    if ".mpd" not in master_playlist:
        return HLS_Downloader(
            m3u8_url=fix_manifest_url(master_playlist),
            output_path=os.path.join(episode_path, episode_name)
        ).start()

    # MPD
    else:
        full_license_url = generate_license_url(obj_episode.mpd_id)
        license_headers = {
            'nv-authorizations': full_license_url.split("?")[1].split("=")[1],
            'user-agent': get_userAgent(),
        }

        return DASH_Downloader(
            mpd_url=master_playlist,
            license_url=full_license_url.split("?")[0],
            license_headers=license_headers,
            output_path=os.path.join(episode_path, episode_name),
        ).start()

def download_series(select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie = None) -> None:
    """
    Handle downloading a complete series.

    Parameters:
        - select_season (Entries): Series metadata from search
        - season_selection (str, optional): Pre-defined season selection that bypasses manual input
        - episode_selection (str, optional): Pre-defined episode selection that bypasses manual input
        - scrape_serie (Any, optional): Pre-instantiated scraper instance
    """
    start_message()
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(select_season.path_id)
        scrape_serie.collect_info_title()
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