# 11.03.24

import os

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import os_manager, start_message
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import manage_selection, dynamic_format_number

from VibraVid.core.downloader import MP4_Downloader

from VibraVid.player.sweetpixel import VideoSource

from .scrapper import ScrapSerie


console = Console()
msg = Prompt()


def download_film(select_title: Entries):
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    scrape_serie = ScrapSerie(select_title.url, site_constants.FULL_URL)
    episodes = scrape_serie.get_episodes() 

    # Get episode information
    episode_data = episodes[0]
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} ([cyan]{scrape_serie.get_name()}) \n")

    # Define filename and path for the downloaded video
    serie_name_with_year = os_manager.get_sanitize_file(scrape_serie.get_name(), select_title.year)
    mp4_name = f"{serie_name_with_year}.mp4"
    mp4_path = os.path.join(site_constants.ANIME_FOLDER, serie_name_with_year.replace('.mp4', ''))

    # Create output folder
    os_manager.create_path(mp4_path)

    # Get video source for the episode
    video_source = VideoSource(site_constants.FULL_URL, episode_data, scrape_serie.session_id, scrape_serie.csrf_token)
    mp4_link = video_source.get_playlist()

    # Start downloading
    path, kill_handler = MP4_Downloader(
        url=str(mp4_link).strip(),
        path=os.path.join(mp4_path, mp4_name)
    )

    return path, kill_handler


def download_episode(episode_data, index_select, scrape_serie):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{scrape_serie.get_name()} ([cyan]E{str(index_select+1)}) \n")

    # Define filename and path for the downloaded video
    mp4_name = f"{scrape_serie.get_name()}_EP_{dynamic_format_number(str(index_select+1))}.mp4"
    mp4_path = os.path.join(site_constants.ANIME_FOLDER, scrape_serie.get_name())

    # Create output folder
    os_manager.create_path(mp4_path)

    # Get video source for the episode
    video_source = VideoSource(site_constants.FULL_URL, episode_data, scrape_serie.session_id, scrape_serie.csrf_token)
    mp4_link = video_source.get_playlist()

    # Start downloading
    path, kill_handler = MP4_Downloader(
        url=str(mp4_link).strip(),
        path=os.path.join(mp4_path, mp4_name)
    )

    return path, kill_handler

def download_series(select_title: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie = None):
    """
    Handle downloading a complete series.

    Parameters:
        - select_season (Entries): Series metadata from search
        - season_selection (str, optional): Pre-defined season selection that bypasses manual input
        - episode_selection (str, optional): Pre-defined episode selection that bypasses manual input
        - scrape_serie (Any, optional): Pre-existing scraper instance to avoid recreation
    """
    start_message()

    # Create scrap instance
    if not scrape_serie:
        scrape_serie = ScrapSerie(select_title.url, site_constants.FULL_URL)
    episodes = scrape_serie.get_episodes() 

    # Get episode count
    console.print(f"\n[green]Episodes count: [red]{len(episodes)}")

    # Display episodes list and get user selection
    if episode_selection is None:
        last_command = msg.ask("\n[cyan]Insert media [red]index [yellow]or [red]* [cyan]to download all media [yellow]or [red]1-2 [cyan]or [red]3-* [cyan]for a range of media")
    else:
        last_command = episode_selection

    list_episode_select = manage_selection(last_command, len(episodes))

    # Download selected episodes
    if len(list_episode_select) == 1 and last_command != "*":
        obj_episode = episodes[list_episode_select[0]-1]
        path, _ = download_episode(obj_episode, list_episode_select[0]-1, scrape_serie)
        return path

    # Download all other episodes selected
    else:
        kill_handler = False
        for i_episode in list_episode_select:
            if kill_handler:
                break
            obj_episode = episodes[i_episode-1]
            _, kill_handler = download_episode(obj_episode, i_episode-1, scrape_serie)