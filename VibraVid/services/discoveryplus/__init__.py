# 22.12.25

import re

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base import Entries, EntriesManager, site_constants
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search
from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client

from .client import get_client
from .downloader import download_film, download_live, download_series

indice = 10
_useFor = "Film_Serie"
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()

_UUID_RE = re.compile(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")


def register_cli_args(parser) -> list:
    """
    Register CLI options.

    Returns:
        list[str]: the argparse 'dest' names this function registered.
    """
    group = parser.add_argument_group("Discovery+ options (--site 10)")
    group.add_argument("--url", dest="url", default=None, metavar="URL", help="Discovery+ title URL (show or movie).")
    return ["url"]


def _resolve_url_to_item(url: str):
    """Resolve a Discovery+ URL to an item dictionary containing metadata."""
    uuid_match = _UUID_RE.search(url)
    if not uuid_match:
        console.print("[red]Could not extract content ID from URL")
        return None
    content_id = uuid_match.group(1)

    client = get_client()
    is_movie = "/show/" not in url

    try:
        if is_movie:
            api_url = f"{client.base_url}/cms/routes/movie/{content_id}"
            params = {"include": "default", "decorators": "badges"}
            with create_client(headers=client.headers, cookies=client.cookies) as http_client:
                response = http_client.get(api_url, params=params)
            response.raise_for_status()
            data = response.json()

            content_info = next(
                (
                    x
                    for x in data.get("included", [])
                    if x.get("attributes", {}).get("videoType", "").lower() == "standalone"
                ),
                None,
            )
            if not content_info:
                console.print(f"[red]Could not resolve movie metadata for id '{content_id}'")
                return None

            attrs = content_info.get("attributes", {})
            name = attrs.get("name", content_id)
            premiere_date = attrs.get("airDate", "") or attrs.get("premiereDate", "")
            year = premiere_date.split("-")[0] if premiere_date else "9999"
            console.print(f"[cyan]Detected movie from URL: [green]{name}")
            return {"id": content_id, "name": name, "type": "movie", "url": url, "year": year}

        api_url = f"{client.base_url}/cms/routes/show/{content_id}"
        params = {"include": "default", "decorators": "badges"}
        with create_client(headers=client.headers, cookies=client.cookies) as http_client:
            response = http_client.get(api_url, params=params)
        response.raise_for_status()
        data = response.json()

        show_info = next(
            (x for x in data.get("included", []) if x.get("attributes", {}).get("alternateId", "") == content_id), None
        )
        if not show_info:
            console.print(f"[red]Could not resolve show metadata for id '{content_id}'")
            return None

        attrs = show_info.get("attributes", {})
        name = attrs.get("name", content_id)
        premiere_date = attrs.get("premiereDate", "")
        year = premiere_date.split("-")[0] if premiere_date else "9999"
        console.print(f"[cyan]Detected series from URL: [green]{name}")
        return {"id": content_id, "name": name, "type": "tv", "url": url, "year": year}

    except Exception as e:
        console.print(f"[red]Error resolving Discovery+ URL: {e}")
        return None


def title_search(query: str) -> int:
    """
    Search for titles on Discovery+

    Parameters:
        query (str): Search query

    Returns:
        int: Number of results found
    """
    entries_manager.clear()
    table_show_manager.clear()

    client = get_client()
    url = f"{client.base_url}/cms/routes/search/result"
    console.print(f"[cyan]Searching on Discovery+ for: [yellow]{query}")

    params = {
        "include": "default",
        "decorators": "viewingHistory,isFavorite,playbackAllowed,contentAction,badges",
        "contentFilter[query]": query,
        "page[items.number]": "1",
        "page[items.size]": "20",
    }

    try:
        with create_client(headers=client.headers, cookies=client.cookies) as http_client:
            response = http_client.get(url, params=params)
        response.raise_for_status()
    except Exception as e:
        console.print(f"[red]Error during Discovery+ search request: {e}")
        return 0

    # Parse response
    data = response.json()

    # Build image mapping
    image_map = {}
    for element in data.get("included", []):
        if element.get("type") == "image":
            attributes = element.get("attributes", {})
            if attributes.get("kind") in ["poster", "poster_with_logo", "default"]:
                image_map[element.get("id")] = attributes.get("src")

    for element in data.get("included", []):
        if element.get("type") == "show":
            attrs = element.get("attributes", {})
            image_url = None
            relationships = element.get("relationships", {})
            images_data = relationships.get("images", {}).get("data", [])
            for img in images_data:
                img_id = img.get("id")
                if img_id in image_map:
                    image_url = image_map[img_id]
                    break

            year = None
            premiere_date = attrs.get("premiereDate", "")
            if premiere_date:
                year = premiere_date.split("-")[0] if "-" in premiere_date else None

            # Handle STANDALONE content as movies
            content_type = "movie" if attrs.get("showType") == "STANDALONE" else "tv"
            entries_manager.add(
                Entries(
                    id=attrs.get("alternateId"), name=attrs.get("name"), type=content_type, image=image_url, year=year
                )
            )

        elif element.get("type") == "video":
            attrs = element.get("attributes", {})
            image_url = None
            relationships = element.get("relationships", {})
            images_data = relationships.get("images", {}).get("data", [])
            for img in images_data:
                img_id = img.get("id")
                if img_id in image_map:
                    image_url = image_map[img_id]
                    break

            year = None
            air_date = attrs.get("airDate", "") or attrs.get("scheduleStart", "")
            if air_date:
                year = air_date[:4] if len(air_date) >= 4 else None

            # Distinguish live events from standalone VOD
            video_type = attrs.get("videoType", "")
            if video_type == "LIVE":
                content_type = "live"
            else:
                content_type = "movie"

            name = attrs.get("name")
            secondary_title = attrs.get("secondaryTitle")
            if secondary_title:
                name = f"{name} - {secondary_title}"

            edit_id = relationships.get("edit", {}).get("data", {}).get("id")

            entries_manager.add(
                Entries(id=element.get("id"), name=name, type=content_type, image=image_url, year=year, edit_id=edit_id)
            )

    return len(entries_manager)


def process_search_result(select_title, selections=None, scrape_serie=None):
    """Wrapper for the generalized process_search_result function."""
    return base_process_search_result(
        select_title=select_title,
        download_film_func=download_film,
        download_series_func=download_series,
        download_live_func=download_live,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        selections=selections,
        scrape_serie=scrape_serie,
    )


def search(
    string_to_search: str = None,
    get_onlyDatabase: bool = False,
    direct_item: dict = None,
    selections: dict = None,
    scrape_serie=None,
):
    """Wrapper for the generalized search function."""
    if direct_item is None and not get_onlyDatabase:
        url = (context_tracker.site_options or {}).get("url")
        if url:
            direct_item = _resolve_url_to_item(url)
            if not direct_item:
                return False

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
