# 17.09.24

import os
import logging

from bs4 import BeautifulSoup
from rich.console import Console

from VibraVid.utils import config_manager, start_message
from VibraVid.services._base.tv_display_manager import map_movie_path
from VibraVid.utils.http_client import create_client, get_headers
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import map_episode_path
from VibraVid.services._base.tv_download_manager import process_season_selection, process_episode_download

from VibraVid.core.downloader import HLS_Downloader

from VibraVid.player.vidxgo import VideoSource as VidXgoVideoSource
from VibraVid.services.mostraguarda.scrapper import GetSerieInfo

from VibraVid.player.supervideo import VideoSource


console = Console()
logger = logging.getLogger(__name__)
extension_output = config_manager.config.get("PROCESS", "extension")


def download_film(select_title: Entries) -> str:
    """
    Downloads a film using the provided Entries information.

    Parameters:
        - select_title (Entries): Class with info about film title.

    Return:
        - str: output path
    """
    start_message()
    console.print(f"[bold yellow]Download: [red]{site_constants.SITE_NAME}[/red] → [cyan]{select_title.name} \n")

    imdb_id = select_title.imdb_id
    if not imdb_id:
        logger.error(f"No IMDB ID found for {select_title.name}")
        return None

    try:
        url = f"https://mostraguarda.stream/set-movie-a/{imdb_id}"
        client = create_client(headers=get_headers())
        response = client.get(url)
        client.close()
        response.raise_for_status()

    except Exception as e:
        logger.error(f"Not found in the server. Title: {select_title.name}, error: {e}")
        raise

    if "not found" in str(response.text):
        logger.error(f"Can't find title: {select_title.name}.")
        return None

    # Extract supervideo url
    soup = BeautifulSoup(response.text, "html.parser")
    player_links = soup.find("ul", class_="_player-mirrors").find_all("li")
    if not player_links:
        logger.error(f"No player links found for {select_title.name}")
        return None
    
    supervideo_url = None
    for li in player_links:
        data_link = li.get("data-link")
        if data_link and "supervideo" in data_link:
            supervideo_url = "https:" + data_link if data_link.startswith("//") else data_link
            break
    
    if not supervideo_url:
        logger.error(f"No supervideo link found for {select_title.name}")
        return None

    # Set domain and media ID for the video source
    video_source = VideoSource(supervideo_url)

    # Define output path
    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = os.path.join(site_constants.MOVIE_FOLDER, *path_components) if path_components else site_constants.MOVIE_FOLDER
    movie_name = f"{filename}.{extension_output}"
    master_playlist = video_source.get_playlist()

    return HLS_Downloader(m3u8_url=master_playlist, output_path=os.path.join(movie_path, movie_name)).start()


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie, video_source):
    """Downloads a specific episode from a VidXgo-backed series."""
    start_message()
    series_display = getattr(scrape_serie, 'series_display_name', None) or scrape_serie.series_name
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{series_display} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    # Define output path
    path_components, filename = map_episode_path(series_display, getattr(scrape_serie, 'year', None), index_season_selected, index_episode_selected, obj_episode.name)
    episode_path = os.path.join(site_constants.SERIES_FOLDER, *path_components)
    episode_name = f"{filename}.{extension_output}"
    master_playlist = video_source.get_playlist()

    return HLS_Downloader(
        m3u8_url=master_playlist,
        headers=video_source.get_playback_headers(),
        output_path=os.path.join(episode_path, episode_name)
    ).start()


def download_series(select_title: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None) -> None:
    """Handle downloading a complete series through VidXgo."""
    start_message()
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(select_title.id, select_title.name, select_title.imdb_id, select_title.year)
        scrape_serie.getNumberSeason()

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""
        def download_video_callback(obj_episode, season_idx, episode_idx):
            video_source = VidXgoVideoSource(select_title.imdb_id, season_idx, episode_idx)
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie, video_source)

        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=download_video_callback,
            download_all=download_all,
            episode_selection=episode_selection
        )

    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=len(scrape_serie.seasons_manager),
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback
    )