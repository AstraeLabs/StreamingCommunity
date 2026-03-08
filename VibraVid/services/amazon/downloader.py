# 07.03.26

import os
from typing import Tuple

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import config_manager, start_message
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_title
from VibraVid.services._base.tv_download_manager import process_season_selection, process_episode_download

from .scrapper import GetSerieInfo


msg = Prompt()
console = Console()
extension_output = config_manager.config.get("PROCESS", "extension")



def download_film(select_title: Entries) -> None:
    """
    Download a single film.
    Not yet implemented (MPD/stream URL extraction pending).

    Args:
        select_film: Film metadata from search results.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{select_title.name}\n")

    # Define the filename and path for the downloaded film
    title_name = f"{map_movie_title(select_title.name, select_title.year)}.{extension_output}"
    title_path = os.path.join(site_constants.MOVIE_FOLDER, title_name.replace(f".{extension_output}", ""))

    console.print(f"[yellow]TODO: download not implemented for {site_constants.SITE_NAME}, path: {title_path}")
    print(select_title.url)


def download_episode(obj_episode, index_season_selected: int, index_episode_selected: int, scrape_serie: GetSerieInfo) -> Tuple[str, bool]:
    """
    Prepare episode output path.
    Download not yet implemented (MPD/stream URL extraction pending).

    Args:
        obj_episode:            Episode object.
        index_season_selected:  Season number (used for naming).
        index_episode_selected: Episode number (used for naming).
        scrape_serie:           Active GetSerieInfo instance.

    Returns:
        Tuple[str, bool]: (output_path, stopped).
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected})\n")
    print(obj_episode.url)

    # Generate output path
    path_components, filename = map_episode_path(scrape_serie.series_name, getattr(scrape_serie, 'year', None), index_season_selected, index_episode_selected, obj_episode.name)
    episode_path = os.path.join(site_constants.SERIES_FOLDER, *path_components)
    episode_full_path = os.path.join(episode_path, f"{filename}.{extension_output}")

    console.print(f"[yellow]TODO: download not implemented for {site_constants.SITE_NAME}, path: {episode_full_path}")
    return (episode_full_path, True)

def download_series(select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie: GetSerieInfo = None) -> None:
    """
    Handle series navigation and episode selection.

    Args:
        select_season:      Series metadata from search results.
        season_selection:   Pre-defined season selection; bypasses prompt.
        episode_selection:  Pre-defined episode selection; bypasses prompt.
        scrape_serie:       Existing GetSerieInfo instance to reuse.
    """
    start_message()
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(select_season.url)
        scrape_serie.getNumberSeason()

    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        def download_video_callback(obj_episode, season_idx: int, episode_idx: int):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie)

        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=download_video_callback,
            download_all=download_all,
            episode_selection=episode_selection,
        )

    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback,
    )