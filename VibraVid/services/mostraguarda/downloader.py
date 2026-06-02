# 17.09.24

import os
import logging

from rich.console import Console

from VibraVid.utils import config_manager, start_message
from VibraVid.provider.tmdb import tmdb
from VibraVid.services._base.tv_display_manager import map_movie_path
from VibraVid.services._base import site_constants, Entries, movie_folder, series_folder
from VibraVid.services._base.tv_display_manager import map_episode_path
from VibraVid.services._base.tv_download_manager import process_season_selection, process_episode_download

from VibraVid.core.downloader import HLS_Downloader

from VibraVid.player.vidxgo import VideoSource as VidXgoVideoSource
from VibraVid.services.mostraguarda.scrapper import GetSerieInfo


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

    tmdb_id = getattr(select_title, 'tmdb_id', None) or getattr(select_title, 'id', None)
    imdb_id = tmdb.get_imdb_id(tmdb_id, 'movie') or tmdb_id
    video_source = VidXgoVideoSource(imdb_id, content_type="movie")
    master_playlist = video_source.get_playlist()

    if not master_playlist:
        logger.error(f"No VidXgo playlist found for {select_title.name}")
        return None

    # Define output path
    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = movie_folder(*path_components)
    movie_name = f"{filename}.{extension_output}"

    return HLS_Downloader(
        m3u8_url=master_playlist,
        headers=video_source.get_playback_headers(),
        output_path=os.path.join(movie_path, movie_name)
    ).start()


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie, video_source):
    """Downloads a specific episode from a VidXgo-backed series."""
    start_message()
    series_display = getattr(scrape_serie, 'series_display_name', None) or scrape_serie.series_name
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{series_display} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    # Define output path
    path_components, filename = map_episode_path(series_display, getattr(scrape_serie, 'year', None), index_season_selected, index_episode_selected, obj_episode.name)
    episode_path = series_folder(*path_components)
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
    tmdb_id = getattr(select_title, 'tmdb_id', None) or getattr(select_title, 'id', None)
    imdb_id = tmdb.get_imdb_id(tmdb_id, 'tv') or tmdb_id
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(select_title.name, tmdb_id, select_title.year)
        scrape_serie.getNumberSeason()

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""
        def download_video_callback(obj_episode, season_idx, episode_idx):
            video_source = VidXgoVideoSource(imdb_id, season_idx, episode_idx)
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
