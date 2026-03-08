# 21.05.24

import os
import urllib.parse
from urllib.parse import urlparse, urlunparse
from typing import Tuple

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import os_manager, config_manager, start_message
from VibraVid.utils.http_client import create_client
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import map_movie_title, map_episode_path
from VibraVid.services._base.tv_download_manager import process_season_selection, process_episode_download

from VibraVid.core.downloader import DASH_Downloader

from .scrapper import GetSerieInfo
from .client import get_playback_url, get_tracking_info, generate_license_url


console = Console()
msg = Prompt()
extension_output = config_manager.config.get("PROCESS", "extension")


def try_mpd(url, qualities):
    """
    Given a url containing one of the qualities (hd/hr/sd), try to replace it with the others and check which manifest exists.
    """
    parsed = urlparse(url)
    path_parts = parsed.path.rsplit('/', 1)
    if len(path_parts) != 2:
        return None
    
    dir_path, filename = path_parts

    # Find the current quality in the filename
    def replace_quality(filename, old_q, new_q):
        if f"{old_q}_" in filename:
            return filename.replace(f"{old_q}_", f"{new_q}_", 1)
        elif filename.startswith(f"{old_q}_"):
            return f"{new_q}_" + filename[len(f"{old_q}_") :]
        return filename

    for q in qualities:
        for old_q in qualities:
            if f"{old_q}_" in filename or filename.startswith(f"{old_q}_"):
                new_filename = replace_quality(filename, old_q, q)
                break
        else:
            new_filename = filename  # No quality found, use original filename

        new_path = f"{dir_path}/{new_filename}"
        mpd_url = urlunparse(parsed._replace(path=new_path)).strip()

        try:
            r = create_client().head(mpd_url)
            if r.status_code == 200:
                return mpd_url
        except Exception:
            pass

    return None

def get_manifest(base):
    """
    Try to get the manifest URL by checking different qualities.
    """
    manifest_qualities = ["hd", "hr", "sd"]

    mpd_url = try_mpd(base, manifest_qualities)
    if not mpd_url:
        exit(1)

    return mpd_url


def download_film(select_title: Entries) -> Tuple[str, bool]:
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{select_title.name} \n")

    # Define the filename and path for the downloaded film
    title_name = f"{map_movie_title(select_title.name, select_title.year)}.{extension_output}"
    title_path = os.path.join(site_constants.MOVIE_FOLDER, title_name.replace(f".{extension_output}", ""))

    # Get playback URL and tracking info
    playback_json = get_playback_url(select_title.id)
    tracking_info = get_tracking_info(playback_json)['videos'][0]

    license_url, license_params = generate_license_url(tracking_info)
    if license_params:
        license_url = f"{license_url}?{urllib.parse.urlencode(license_params)}"

    # Download the episode
    return DASH_Downloader(
        mpd_url=get_manifest(tracking_info['url']),
        license_url=license_url,
        output_path=os.path.join(title_path, title_name),
    ).start()


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    # Define filename and path for the downloaded video
    path_components, filename = map_episode_path(scrape_serie.series_name, getattr(scrape_serie, 'year', None), index_season_selected, index_episode_selected, obj_episode.name)
    episode_path = os_manager.get_sanitize_path(os.path.join(site_constants.SERIES_FOLDER, *path_components))
    episode_name = f"{filename}.{extension_output}"

    # Generate mpd and license URLs
    playback_json = get_playback_url(obj_episode.id)
    tracking_info = get_tracking_info(playback_json)
    license_url, license_params = generate_license_url(tracking_info['videos'][0])
    if license_params:
        license_url = f"{license_url}?{urllib.parse.urlencode(license_params)}"

    # Download the episode
    return DASH_Downloader(
        mpd_url=get_manifest(tracking_info['videos'][0]['url']),
        license_url=license_url,
        mpd_sub_list=tracking_info['subtitles'],
        output_path=os.path.join(episode_path, episode_name),
    ).start()
    

def download_series(dict_serie: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie = None) -> None:
    """
    Handle downloading a complete series.

    Parameters:
        - dict_serie (Entries): Series metadata from search
        - season_selection (str, optional): Pre-defined season selection that bypasses manual input
        - episode_selection (str, optional): Pre-defined episode selection that bypasses manual input
        - scrape_serie (Any, optional): Pre-existing scraper instance to avoid recreation
    """
    start_message()
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(dict_serie.url)
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

    # Use the process_season_selection function with try-catch for season lookup
    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback
    )