# 29.05.26
# By @UrloMythus

import os
import logging

from rich.console import Console

from VibraVid.utils import config_manager, start_message, os_manager
from VibraVid.services._base import site_constants, Entries, series_folder
from VibraVid.services._base.tv_display_manager import map_episode_path
from VibraVid.services._base.tv_download_manager import process_season_selection, process_episode_download
from VibraVid.core.downloader import HLS_Downloader
from VibraVid.player.loadm import LoadmSource
from VibraVid.player.maxstream import MaxStreamSource

from .scrapper import GetSerieInfo


console = Console()
logger = logging.getLogger(__name__)
extension_output = config_manager.config.get("PROCESS", "extension")


def download_film(select_title: Entries):
    console.print(f"[yellow]Eurostreaming does not provide movies — skipping '{select_title.name}'.")


def _download_episode(obj_episode, season_number: int, episode_number: int, scrape_serie: GetSerieInfo,):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{season_number}E{episode_number}) \n")

    link_url, host = scrape_serie.get_episode_link(season_number, episode_number)
    if not link_url:
        logger.error(f"[Eurostreaming] No supported link for S{season_number}E{episode_number:02d}")
        return None

    referer = site_constants.FULL_URL
    if host == 'loadm':
        source = LoadmSource(link_url, referer=referer)
    else:
        source = MaxStreamSource(link_url, referer=referer)

    stream_url, playback_headers = source.get_stream()
    if not stream_url:
        logger.error(f"[Eurostreaming] No stream URL via {host} for S{season_number}E{episode_number:02d}")
        return None

    path_components, filename = map_episode_path(scrape_serie.series_display_name, scrape_serie.year, season_number, episode_number, obj_episode.name,)
    out_dir = os_manager.get_sanitize_path(series_folder(*path_components))
    out_path = os.path.join(out_dir, f"{filename}.{extension_output}")

    return HLS_Downloader(
        m3u8_url = stream_url,
        headers = playback_headers,
        output_path = out_path,
    ).start()


def download_series(select_title: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie = None) -> None:
    """
    Handle downloading a complete series.

    Parameters:
        - select_title (Entries): Series metadata from search
        - season_selection (str, optional): Pre-defined season selection that bypasses manual input
        - episode_selection (str, optional): Pre-defined episode selection that bypasses manual input
        - scrape_serie (Any, optional): Pre-existing scraper instance to avoid recreation
    """
    start_message()
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(select_title.name, site_constants.FULL_URL)
        scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""
        def download_video_callback(obj_episode, season_idx, episode_idx):
            return _download_episode(obj_episode, season_idx, episode_idx, scrape_serie)
        
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
