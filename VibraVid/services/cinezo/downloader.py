# Cinezo downloader

import os
import logging

from rich.console import Console

from VibraVid.utils import config_manager, start_message, os_manager
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import map_movie_path, map_episode_path
from VibraVid.core.downloader import HLS_Downloader
from VibraVid.core.ui.tracker import context_tracker

from .client import get_stream
from .scrapper import GetSerieInfo


console = Console()
logger  = logging.getLogger(__name__)
extension_output = config_manager.config.get("PROCESS", "extension")


def download_film(select_title: Entries):
    """Download a movie from Cinezo."""
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{select_title.name}\n")

    tmdb_id = getattr(select_title, 'id', None) or getattr(select_title, 'tmdb_id', None)
    if not tmdb_id:
        raise ValueError(f"[Cinezo] No TMDB ID for '{select_title.name}'")

    m3u8_url, stream_headers = get_stream(int(tmdb_id), 'movie')
    console.print(f"[cyan]Stream: {m3u8_url[:70]}...\n")

    path_components, filename = map_movie_path(select_title.name, select_title.year)
    out_dir = os_manager.get_sanitize_path(
        os.path.join(site_constants.MOVIE_FOLDER, *path_components)
        if path_components else site_constants.MOVIE_FOLDER
    )
    out_path = os.path.join(out_dir, f"{filename}.{extension_output}")

    return HLS_Downloader(
        m3u8_url   = m3u8_url,
        headers    = stream_headers or None,
        output_path= out_path,
    ).start()


def download_episode(obj_episode, index: int, scrape_serie: GetSerieInfo, season_number: int):
    """Download a single episode from Cinezo."""
    start_message()
    console.print(
        f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> "
        f"[cyan]{scrape_serie.series_name} (S{season_number}E{obj_episode.number})\n"
    )

    m3u8_url, stream_headers = get_stream(
        scrape_serie.tmdb_id, 'tv',
        season=season_number, episode=int(obj_episode.number)
    )
    console.print(f"[cyan]Stream: {m3u8_url[:70]}...\n")

    path_components, filename = map_episode_path(
        series_name    = scrape_serie.series_name,
        series_year    = None,
        season_number  = season_number,
        episode_number = int(obj_episode.number),
        episode_name   = obj_episode.name,
    )
    out_dir  = os_manager.get_sanitize_path(
        os.path.join(site_constants.SERIES_FOLDER, *path_components))
    out_path = os.path.join(out_dir, f"{filename}.{extension_output}")

    return HLS_Downloader(
        m3u8_url   = m3u8_url,
        headers    = stream_headers or None,
        output_path= out_path,
    ).start()


def download_series(select_title: Entries, season_selection: str = None,
                    episode_selection: str = None, scrape_serie: GetSerieInfo = None):
    """Download selected episodes from Cinezo."""
    from rich.prompt import Prompt
    from VibraVid.services._base.tv_display_manager import manage_selection

    start_message()

    tmdb_id = getattr(select_title, 'id', None) or getattr(select_title, 'tmdb_id', None)
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(int(tmdb_id), select_title.name)

    scrape_serie.getNumberSeason()
    console.print(f"\n[green]Serie: [cyan]{select_title.name}")

    if season_selection is None:
        season_selection = Prompt.ask("\n[cyan]Inserisci numero stagione")
    season_num = int(season_selection)

    episodes = scrape_serie.getEpisodeSeasons(season_num)
    if not episodes:
        console.print(f"[red]Nessun episodio trovato per stagione {season_num}.")
        return

    console.print(f"\n[green]Episodi disponibili: [red]{len(episodes)}")

    if episode_selection is None:
        episode_selection = Prompt.ask(
            "\n[cyan]Inserisci indice episodio, [red]* [cyan]per tutti, [red]1-3 [cyan]per range")

    list_ep = manage_selection(episode_selection, len(episodes))
    kill = False
    for i in list_ep:
        if kill:
            break
        ep = episodes[i - 1]
        _, kill = download_episode(ep, i - 1, scrape_serie, season_num)
