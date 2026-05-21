# 14.05.26

import logging

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .client import JumoClient, format_duration
from .downloader import download_song


indice = 16
_useFor = "song"
console = Console()
msg = Prompt()
logger = logging.getLogger(__name__)
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """
    Search for tracks
    """
    entries_manager.clear()
    table_show_manager.clear()

    client = JumoClient()
    tracks = client.search(query, limit=25)

    for t in tracks:
        if t["id"] is None:
            continue

        name = (
            f"{t['artist']} - {t['title']}"
            if t["artist"] not in ("—", "", None)
            else t["title"]
        )

        entry = Entries(
            name=name,
            type="song",
            year=t.get("year", ""),
            image=t.get("cover", ""),
            url=f"jumo:{t['id']}",
        )
        entry.album = t.get("album", "")
        entry.duration = format_duration(t["duration"]) if t.get("duration") else "—"
        entry.explicit = "🅴" if t.get("explicit") else ""
        entry.genre = t.get("genre", "")

        entries_manager.add(entry)

    return len(entries_manager)


def process_search_result(select_title, selections=None, scrape_serie=None):
    """Process search result for music."""
    if select_title is None:
        console.print("[yellow]No title selected.")
        return False

    # base_process_search_result for type "song" does NOT pass selections to
    # download_film_func — so we stash audio_format on the Entries instance
    # itself. The metaclass on Entries lets us set arbitrary attributes that
    # download_song will read later via getattr.
    if selections and selections.get("audio_format"):
        select_title.audio_format = selections.get("audio_format")

    return base_process_search_result(
        select_title=select_title,
        download_film_func=download_song,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        selections=selections,
        scrape_serie=scrape_serie,
    )


def search(string_to_search: str = None, get_onlyDatabase: bool = False, direct_item: dict = None, selections: dict = None, scrape_serie=None):
    """Wrapper for the generalized search function."""
    return base_search(
        title_search_func=title_search,
        process_result_func=process_search_result,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        site_name=site_constants.SITE_NAME,
        string_to_search=string_to_search,
        get_onlyDatabase=get_onlyDatabase,
        direct_item=direct_item,
        selections=selections,
        scrape_serie=scrape_serie,
    )