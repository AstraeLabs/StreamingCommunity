# 22.12.25

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, check_region_availability
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_series
from .client import get_client


indice = 11
_useFor = "Film_Serie"
_region = ["IT"]
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


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

    if not check_region_availability(_region, site_constants.SITE_NAME):
        return 0

    client = get_client()
    url = f"{client.base_url}/cms/routes/search/result"
    console.print(f"[cyan]Searching on Discovery+ for: [yellow]{query}")

    params = {
        'include': 'default',
        'decorators': 'viewingHistory,isFavorite,playbackAllowed,contentAction,badges',
        'contentFilter[query]': query,
        'page[items.number]': '1',
        'page[items.size]': '20',
    }

    try:
        response = create_client(headers=client.headers, cookies=client.cookies).get(url, params=params)
        response.raise_for_status()
    except Exception as e:
        console.print(f"[red]Error during Discovery+ search request: {e}")
        return 0

    # Parse response
    data = response.json()

    # Build image mapping
    image_map = {}
    for element in data.get('included', []):
        if element.get('type') == 'image':
            attributes = element.get('attributes', {})
            if attributes.get('kind') in ['poster', 'poster_with_logo', 'default']:
                image_map[element.get('id')] = attributes.get('src')

    for element in data.get('included', []):
        if element.get('type') == 'show':
            attrs = element.get('attributes', {})
            image_url = None
            relationships = element.get('relationships', {})
            images_data = relationships.get('images', {}).get('data', [])
            for img in images_data:
                img_id = img.get('id')
                if img_id in image_map:
                    image_url = image_map[img_id]
                    break

            year = None
            premiere_date = attrs.get('premiereDate', '')
            if premiere_date:
                year = premiere_date.split('-')[0] if '-' in premiere_date else None

            entries_manager.add(Entries(
                id=attrs.get('alternateId'),
                name=attrs.get('name'),
                type='tv',
                image=image_url,
                year=year
            ))

        elif element.get('type') == 'video':
            attrs = element.get('attributes', {})
            image_url = None
            relationships = element.get('relationships', {})
            images_data = relationships.get('images', {}).get('data', [])
            for img in images_data:
                img_id = img.get('id')
                if img_id in image_map:
                    image_url = image_map[img_id]
                    break

            year = None
            air_date = attrs.get('airDate', '')
            if air_date:
                year = air_date[:4] if len(air_date) >= 4 else None

            entries_manager.add(Entries(
                id=element.get('id'),
                name=attrs.get('name'),
                type='movie',
                image=image_url,
                year=year
            ))

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