# 21.05.24

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, get_headers, check_region_availability
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_film, download_series


indice = 5
_useFor = "Film_Serie"
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

    search_url = "https://www.raiplay.it/atomatic/raiplay-search-service/api/v1/msearch"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    json_data = {
        'templateIn': '6470a982e4e0301afe1f81f1',
        'templateOut': '6516ac5d40da6c377b151642',
        'params': {
            'param': query,
            'from': None,
            'sort': 'relevance',
            'onlyVideoQuery': False,
        },
    }

    try:
        response = create_client(headers=get_headers()).post(search_url, json=json_data)
        response.raise_for_status()

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    try:
        response_data = response.json()
        cards = response_data.get('agg', {}).get('titoli', {}).get('cards', [])
        
        # Limit to only 15 results for performance
        data = cards[:15]
        console.print(f"[cyan]Found {len(cards)} results, processing first {len(data)}...")
        
    except Exception as e:
        console.print(f"[red]Error parsing search results: {e}")
        return 0
    
    # Process each item and add to media manager
    for idx, item in enumerate(data, 1):
        try:
            # Get path_id
            path_id = item.get('path_id', '')
            if not path_id:
                console.print("[yellow]Skipping item due to missing path_id")
                continue

            # Get image URL - handle both relative and absolute URLs
            image = item.get('immagine', '')
            if image and not image.startswith('http'):
                image = f"https://www.raiplay.it{image}"
            
            # Get URL - handle both relative and absolute URLs
            url = item.get('url', '')
            if url and not url.startswith('http'):
                url = f"https://www.raiplay.it{url}"

            entries_manager.add(Entries(
                id=item.get('id', ''),
                path_id=path_id,
                name=item.get('titolo', 'Unknown'),
                type='tv',
                url=url,
                image=image,
                year=image.split("/")[-4]
            ))
    
        except Exception as e:
            console.print(f"[red]Error processing item '{item.get('titolo', 'Unknown')}': {e}")
            continue
    
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