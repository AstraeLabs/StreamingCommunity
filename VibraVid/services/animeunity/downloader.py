# 11.03.24

import os

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import os_manager, config_manager, start_message
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import manage_selection, map_episode_path, map_movie_path

from VibraVid.core.downloader import MP4_Downloader, HLS_Downloader

from VibraVid.player.vixcloud import VideoSourceAnime

from .scrapper import ScrapeSerieAnime


console = Console()
msg = Prompt()
extension_output = config_manager.config.get("PROCESS", "extension")
KILL_HANDLER = False
DOWNOAD_HLS = True


def download_film(select_title: Entries):
    """
    Downloads a film using the provided Entries information.
    """
    scrape_serie = ScrapeSerieAnime(site_constants.FULL_URL)
    video_source = VideoSourceAnime(site_constants.FULL_URL)

    # Set up video source (only configure scrape_serie now)
    scrape_serie.setup(None, select_title.id, select_title.slug)
    scrape_serie.is_series = False
    obj_episode = scrape_serie.get_info_episode(0)
    download_episode(obj_episode, 0, scrape_serie, video_source)


def download_episode(obj_episode, index_select, scrape_serie, video_source):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{scrape_serie.series_name} ([cyan]E{obj_episode.number}) \n")

    # Collect mp4 url
    video_source.get_embed(obj_episode.id, not DOWNOAD_HLS)

    if scrape_serie.is_series:
        if isinstance(obj_episode.number, str) and '-' in obj_episode.number:
            console.print(f"[red]Warning: [yellow]Episode number '{obj_episode.number}' contains a hyphen. Using the first part as the episode number.")
            episode_number = int(float(obj_episode.number.split('-')[0]))
        elif isinstance(obj_episode.number, (int, float, str)):
            episode_number = int(float(obj_episode.number))
        else:
            episode_number = 1
        episode_name = f"Episode {obj_episode.number}"

        path_components, filename = map_episode_path(series_name=scrape_serie.series_name, series_year=None, season_number=1, episode_number=episode_number, episode_name=episode_name)
        mp4_path = os_manager.get_sanitize_path(os.path.join(site_constants.ANIME_FOLDER, *path_components))
        mp4_name = filename
    else:
        path_components, filename = map_movie_path(scrape_serie.series_name, None)
        mp4_path = os_manager.get_sanitize_path(os.path.join(site_constants.MOVIE_FOLDER, *path_components) if path_components else site_constants.MOVIE_FOLDER)
        mp4_name = filename

    # Create output folder
    os_manager.create_path(mp4_path)

    # Start downloading
    if not DOWNOAD_HLS:
        return MP4_Downloader(url=str(video_source.src_mp4).strip(), path=os.path.join(mp4_path, f"{mp4_name}.mp4"))
    
    else:
        return HLS_Downloader(m3u8_url=video_source.master_playlist, output_path=os.path.join(mp4_path, f"{mp4_name}.{extension_output}")).start()

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
    if scrape_serie is None:
        scrape_serie = ScrapeSerieAnime(site_constants.FULL_URL)
        scrape_serie.setup(None, select_title.id, select_title.slug)

    video_source = VideoSourceAnime(site_constants.FULL_URL)

    # Get episode information
    episoded_count = scrape_serie.get_count_episodes()
    console.print(f"\n[green]Episodes count: [red]{episoded_count}")
    
    # Display episodes list and get user selection
    if episode_selection is None:
        last_command = msg.ask("\n[cyan]Insert media [red]index [yellow]or [red]* [cyan]to download all media [yellow]or [red]1-2 [cyan]or [red]3-* [cyan]for a range of media")
    else:
        last_command = episode_selection

    # Manage user selection
    list_episode_select = manage_selection(last_command, episoded_count)

    # Download selected episodes
    if len(list_episode_select) == 1 and last_command != "*":
        obj_episode = scrape_serie.get_info_episode(list_episode_select[0]-1)
        path, _ = download_episode(obj_episode, list_episode_select[0]-1, scrape_serie, video_source)
        return path

    # Download all other episodes selected
    else:
        kill_handler = False
        for i_episode in list_episode_select:
            if kill_handler:
                break
            obj_episode = scrape_serie.get_info_episode(i_episode-1)
            _, kill_handler = download_episode(obj_episode, i_episode-1, scrape_serie, video_source)