# 07.03.26

import re
import json

from bs4 import BeautifulSoup
from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import check_region_availability
from VibraVid.utils.http_client import create_client, get_userAgent
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_film, download_series
from .client import BASE_URL


indice = 15
_useFor = "Film_Serie"
_region = ["IT"]
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """
    Search for titles on Prime Video using the HTML search page.

    Args:
        query (str): The search query.

    Returns:
        int: Number of titles found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    if not check_region_availability(_region, site_constants.SITE_NAME):
        return 0

    search_url = f"{BASE_URL}/search/ref=atv_nb_sug"
    params = {"ie": "UTF8", "phrase": query}
    console.print(f"[cyan]Search url: [yellow]{search_url}?phrase={query}")

    try:
        response = create_client(headers={'user-agent': get_userAgent(), "viewport-width": "1143"}).get(search_url, params=params, timeout=20)
        response.raise_for_status()
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request error: {e}")
        return 0

    # Extract the props JSON embedded in a <script> tag
    soup = BeautifulSoup(response.text, "html.parser")
    data = None
    for script in soup.find_all("script"):
        if script.string and '{"props":{"body":[{"args"' in script.string:
            try:
                data = json.loads(script.string)
                break
            except json.JSONDecodeError:
                continue

    if not data:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, could not parse search JSON.")
        return 0

    try:
        containers = data["props"]["body"][0]["props"]["search"]["containers"]
        entities   = []
        for container in containers:
            entities.extend(container.get("entities", []))
    except (KeyError, IndexError, TypeError) as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, unexpected JSON structure: {e}")
        return 0

    for card in entities:
        try:
            href = card.get("link", {}).get("url", "")
            m    = re.search(r'/detail/([^/]+)/', href)
            if not m:
                continue

            compact_id = m.group(1)
            entity_type = card.get("entityType", "")
            media_type = "tv" if entity_type == "TV Show" else "film"
            cues = card.get("entitlementCues", {})
            avail_msg = (cues.get("glanceMessage", {}).get("message") or cues.get("focusMessage",  {}).get("message") or "")

            entries_manager.add(Entries(
                name = card.get("title", card.get("displayTitle", "")),
                type = media_type,
                year = str(card.get("releaseYear", "")),
                url = f"{BASE_URL}/detail/{compact_id}/",
                slug = compact_id,
                episode = avail_msg,
                image = card.get("images", {}).get("cover", {}).get("url", "")
            ))

        except Exception as e:
            console.print(f"[red]Error parsing search entry: {e}")

    return len(entries_manager)

def process_search_result(select_title, selections=None, scrape_serie=None):
    """Wrapper for the generalized process_search_result function."""
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