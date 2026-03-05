# 16.03.25

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager, config_manager
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_film, download_series
from .client import CrunchyrollClient


indice = 7
_useFor = "Anime"
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """
    Search for titles based on a search query.
      
    Parameters:
        - query (str): The query to search for.

    Returns:
        int: The number of titles found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    if not config_manager.login.get('crunchyroll','device_id') or not config_manager.login.get('crunchyroll','etp_rt'):
        raise Exception("device_id or etp_rt is missing or empty in config.json.")

    client = CrunchyrollClient()
    if not client.start():
        console.print("[red] Failed to authenticate with Crunchyroll.")
        raise Exception("Failed to authenticate with Crunchyroll.")

    api_url = "https://www.crunchyroll.com/content/v2/discover/search"

    params = {
        "q": query,
        "n": 20,
        "type": "series,movie_listing",
        "ratings": "true",
        "preferred_audio_language": "it-IT",
        "locale": "it-IT"
    }

    console.print(f"[cyan]Search url: [yellow]{api_url}")

    try:
        response = client.request('GET', api_url, params=params)
        response.raise_for_status()

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    data = response.json()
    seen_ids = set()

    # Parse results
    for block in data.get("data", []):
        block_type = block.get("type")
        if block_type not in ("series", "movie_listing", "top_results"):
            continue

        for item in block.get("items", []):
            item_id = item.get('id')
            if not item_id or item_id in seen_ids:
                continue
            
            seen_ids.add(item_id)
            tipo = None

            if item.get("type") == "movie_listing":
                tipo = "film"
            elif item.get("type") == "series":
                meta = item.get("series_metadata", {})

                # Heuristic: single episode series might be films
                if meta.get("episode_count") == 1 and meta.get("season_count", 1) == 1 and meta.get("series_launch_year"):
                    description = item.get("description", "").lower()
                    if "film" in description or "movie" in description:
                        tipo = "film"
                    else:
                        tipo = "tv"
                else:
                    tipo = "tv"
            else:
                continue

            url = f"https://www.crunchyroll.com/series/{item_id}"
            title = item.get("title", "")

            # Get image
            poster_image = None
            list_image = item.get('images', {})
            if list_image:
                poster_wide = list_image.get('poster_wide')
                if poster_wide and len(poster_wide) > 0:
                    poster_image = poster_wide[0][-1].get("source")

            entries_manager.add(Entries(
                id=item_id,
                name=title,
                type=tipo,
                url=url,
                image=poster_image
            ))

    return len(entries_manager)



# WRAPPING FUNCTIONS
def process_search_result(select_title, selections=None, scrape_serie=None):
    """
    Wrapper for the generalized process_search_result function.
    """
    return base_process_search_result(
        select_title=select_title,
        download_film_func=download_film,
        download_series_func=download_series,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        selections=selections,
        scrape_serie=scrape_serie
    )

def search(string_to_search: str = None, get_onlyDatabase: bool = False, direct_item: dict = None, selections: dict = None, scrape_serie=None):
    """
    Wrapper for the generalized search function.
    """
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
        scrape_serie=scrape_serie
    )