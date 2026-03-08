# 07.03.26

import re
import urllib.parse

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import check_region_availability
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_film, download_series
from .client import BASE_URL, get_json


indice = 18
_useFor = "Film_Serie"
_region = ["IT"]
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """
    Search for titles on Prime Video.

    Args:
        query (str): The search query.

    Returns:
        int: Number of titles found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    if not check_region_availability(_region, site_constants.SITE_NAME):
        return 0

    query_params = urllib.parse.urlencode({"jic": "8|EgRzdm9k", "phrase": query, "type": "CONTENT_CARDS"})
    search_url = f"{BASE_URL}/api/searchSuggestions?{query_params}"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    extra_headers = {
        "accept": "*/*",
        "x-requested-with": "XMLHttpRequest",
        "x-amzn-client-ttl-seconds": "15",
        "referer": f"{BASE_URL}/",
    }

    try:
        data = get_json(search_url, extra_headers).get("contentCards", [])
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    for card in data:
        try:
            href = card.get("href", "")
            m = re.search(r'/detail/([^/]+)/', href)
            if not m:
                continue

            compact_id = m.group(1)
            entity_type = card.get("entityType", "")
            media_type = "tv" if entity_type == "TV Show" else "film"

            entries_manager.add(Entries(
                name=card.get("text", ""),
                type=media_type,
                year=str(card.get("releaseYear", "")),
                url=f"{BASE_URL}/detail/{compact_id}/",
                slug=compact_id,
            ))

        except Exception as e:
            console.print(f"[red]Error parsing search entry: {e}")

    return len(entries_manager)


def process_search_result(select_title, selections=None, scrape_serie=None):
    return base_process_search_result(
        select_title=select_title,
        download_film_func=download_film,
        download_series_func=download_series,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        selections=selections,
        scrape_serie=scrape_serie,
    )


def search(string_to_search: str = None, get_onlyDatabase: bool = False, direct_item: dict = None, selections: dict = None, scrape_serie=None):
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