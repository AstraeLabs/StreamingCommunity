# 26.11.2025

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_series
from .client import get_api


indice = 17
_useFor = "Serie"
_region = ["IT"]
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """
    Search for titles on Pluto TV
    
    Parameters:
        query (str): Search query
        
    Returns:
        int: Number of results found
    """
    entries_manager.clear()
    table_show_manager.clear()

    search_url = f"https://service-media-search.clusters.pluto.tv/v1/search?q={query}&limit=10"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    try:
        api = get_api()
        response = create_client(headers=api.get_request_headers()).get(search_url)
        response.raise_for_status()
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0
    
    # Parse response
    data = response.json().get('data', [])
    for dict_title in data:
        try:
            if dict_title.get('type') == 'channel':
                continue

            define_type = 'tv' if dict_title.get('type') == 'series' else dict_title.get('type')
            
            entries_manager.add(Entries(
                id=dict_title.get('id'),
                name=dict_title.get('name'),
                type=define_type,
                image=None,
                year=None
            ))
            
        except Exception as e:
            print(f"Error parsing entry: {e}")
    
    return len(entries_manager)


# WRAPPING FUNCTIONS
def process_search_result(select_title, selections=None, scrape_serie=None):
    """
    Wrapper for the generalized process_search_result function.
    """
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