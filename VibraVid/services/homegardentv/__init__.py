# 26.11.25

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, get_userAgent, check_region_availability
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_series


indice = 15
_useFor = "Serie"
_region = ["IT"]
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

    if not check_region_availability(_region, site_constants.SITE_NAME):
        return 0

    search_url = f"https://public.aurora.enhanced.live/site/search/page/?include=default&filter[environment]=hgtvit&v=2&q={query}&page[number]=1&page[size]=20"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    try:
        response = create_client(headers={'user-agent': get_userAgent()}).get(search_url)
        response.raise_for_status()

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Collect json data
    try:
        data = response.json().get('data')
    except Exception as e:
        console.log(f"Error parsing JSON response: {e}")
        return 0

    for dict_title in data:
        try:
            # Skip non-showpage entries
            if dict_title.get('type') != 'showpage':
                continue
            
            entries_manager.add(Entries(
                name=dict_title.get('title'),
                type='tv',
                year=dict_title.get('dateLastModified').split('-')[0],
                image=dict_title.get('image').get('url'),
                url=f'https://public.aurora.enhanced.live/site/page/{str(dict_title.get("slug")).lower().replace(" ", "-")}/?include=default&filter[environment]=hgtvit&v=2&parent_slug={dict_title.get("parentSlug")}',
            ))
            
        except Exception as e:
            print(f"Error parsing a film entry: {e}")
	
    return len(entries_manager)

def process_search_result(select_title, selections=None, scrape_serie=None):
    """Wrapper for the generalized process_search_result function."""
    return base_process_search_result(
        select_title=select_title,
        download_film_func=None,
        download_series_func=download_series,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        selections=selections,
        scrape_serie=scrape_serie
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
        scrape_serie=scrape_serie
    )