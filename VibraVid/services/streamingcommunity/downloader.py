# 3.12.23

import os

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import config_manager, tmdb_client, start_message
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import map_movie_title, map_episode_path
from VibraVid.services._base.tv_download_manager import process_season_selection, process_episode_download

from VibraVid.core.downloader import HLS_Downloader

from VibraVid.player.vixcloud import VideoSource

from .scrapper import GetSerieInfo


console = Console()
msg = Prompt()
extension_output = config_manager.config.get("PROCESS", "extension")
use_other_api = config_manager.login.get("TMDB", "api_key") != ""


def download_film(select_title: Entries) -> str:
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{select_title.name} \n")

    # Prepare TMDB data 
    tmdb_data = None
    if use_other_api:
        result = tmdb_client.get_type_and_id_by_slug_year(select_title.slug, select_title.year, "movie", select_title.provider_language)
        
        if result and result.get('id') and result.get('type') == 'movie':
            tmdb_data = {'id': result.get('id')}

    # Init class
    video_source = VideoSource(f"{site_constants.FULL_URL}/{select_title.provider_language}", False, select_title.id, tmdb_data=tmdb_data)

    # Retrieve iframe only if not using TMDB API
    if tmdb_data is None:
        video_source.get_iframe(select_title.id)
    
    video_source.get_content()
    master_playlist = video_source.get_playlist()

    if master_playlist is None:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, error: No master playlist found")
        return None

    # Define the filename and path for the downloaded film
    title_name = f"{map_movie_title(select_title.name, select_title.year)}.{extension_output}"
    title_path = os.path.join(site_constants.MOVIE_FOLDER, title_name.replace(f".{extension_output}", ""))

    # Download the film using the m3u8 playlist, and output filename
    return HLS_Downloader(
        m3u8_url=master_playlist,
        output_path=os.path.join(title_path, title_name)
    ).start()


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie, video_source):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    series_display = getattr(scrape_serie, 'series_display_name', None) or scrape_serie.series_name
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{series_display} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    # Define filename and path for the downloaded video
    path_components, filename = map_episode_path(series_display, getattr(scrape_serie, 'year', None), index_season_selected, index_episode_selected, obj_episode.name)
    episode_path = os.path.join(site_constants.SERIES_FOLDER, *path_components)
    episode_name = f"{filename}.{extension_output}"

    if use_other_api:
        series_slug = scrape_serie.series_name.lower().replace(' ', '-').replace("'", '')
        result = tmdb_client.get_type_and_id_by_slug_year(str(series_slug), int(scrape_serie.year), 'tv', scrape_serie.provider_language)
        
        if result and result.get('id') and result.get('type') == 'tv':
            tmdb_id = result.get('id')
            video_source.tmdb_id = tmdb_id
            video_source.season_number = index_season_selected
            video_source.episode_number = index_episode_selected
            
        else:
            console.print("[yellow]TMDB ID not found or not a TV show, falling back to original method")
            video_source.get_iframe(obj_episode.id)

    else:
        # Retrieve iframe using original method
        video_source.get_iframe(obj_episode.id)

    video_source.get_content()
    master_playlist = video_source.get_playlist()

    # Download the episode
    return HLS_Downloader(
        m3u8_url=master_playlist,
        output_path=os.path.join(episode_path, episode_name)
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
    video_source = VideoSource(f"{site_constants.FULL_URL}/{select_season.provider_language}", True, select_season.id)
    
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(f"{site_constants.FULL_URL}/{select_season.provider_language}", select_season.id, select_season.slug, select_season.year, select_season.provider_language, series_display_name=select_season.name)
        scrape_serie.getNumberSeason()
        scrape_serie.series_display_name = select_season.name
    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""
        def download_video_callback(obj_episode, season_idx, episode_idx):
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
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback
    )