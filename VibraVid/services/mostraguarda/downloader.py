# 17.09.24

import os
import logging

from bs4 import BeautifulSoup
from rich.console import Console

from VibraVid.utils import config_manager, start_message
from VibraVid.services._base.tv_display_manager import map_movie_path
from VibraVid.utils.http_client import create_client, get_headers
from VibraVid.services._base import site_constants, Entries

from VibraVid.core.downloader import HLS_Downloader

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
        response = create_client(headers=get_headers()).get(url)
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

    # Get m3u8 master playlist
    master_playlist = video_source.get_playlist()

    return HLS_Downloader(m3u8_url=master_playlist, output_path=os.path.join(movie_path, movie_name)).start()