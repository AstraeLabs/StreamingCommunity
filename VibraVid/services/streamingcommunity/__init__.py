# 21.05.24

import json

from bs4 import BeautifulSoup
from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, get_userAgent
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_series, download_film


indice = 0
_useFor = "Film_Serie"
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """
    Search for titles based on a search query in both IT and EN languages.
      
    Parameters:
        - query (str): The query to search for.

    Returns:
        int: The number of unique titles found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    # Dictionary to track unique IDs
    seen_ids = set()
    languages = ['it', 'en']
    
    for lang in languages:
        console.print(f"[cyan]Searching in language: [yellow]{lang}")
        
        try:
            response = create_client(headers={'user-agent': get_userAgent()}).get(f"{site_constants.FULL_URL}/{lang}")
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            version = json.loads(soup.find('div', {'id': "app"}).get("data-page"))['version']
        except Exception as e:
            console.print(f"[red]Site: {site_constants.SITE_NAME} version ({lang}), request error: {e}")
            continue

        search_url = f"{site_constants.FULL_URL}/{lang}/search?q={query}"
        console.print(f"[cyan]Search url: [yellow]{search_url}")

        try:
            response = create_client(headers={'user-agent': get_userAgent(), 'x-inertia': 'true', 'x-inertia-version': version}).get(search_url)
            response.raise_for_status()
        except Exception as e:
            console.print(f"[red]Site: {site_constants.SITE_NAME} ({lang}), request search error: {e}")
            continue

        # Collect json data
        try:
            data = response.json().get('props').get('titles')
        except Exception as e:
            console.log(f"[red]Error parsing JSON response ({lang}): {e}")
            continue

        for i, dict_title in enumerate(data):
            try:
                title_id = dict_title.get('id')
                
                # Skip if we've already seen this ID
                if title_id in seen_ids:
                    continue
                
                # Add ID to seen set
                seen_ids.add(title_id)
                
                images = dict_title.get('images') or []
                filename = None
                preferred_types = ['poster', 'cover', 'cover_mobile', 'background']
                for ptype in preferred_types:
                    for img in images:
                        if img.get('type') == ptype and img.get('filename'):
                            filename = img.get('filename')
                            break

                    if filename:
                        break

                if not filename and images:
                    filename = images[0].get('filename')

                image_url = None
                if filename:
                    image_url = f"{site_constants.FULL_URL.replace('stream', 'cdn.stream')}/images/{filename}"

                # Extract year: prefer first_air_date at root level, otherwise search in translations
                year = None
                if not year:
                    for trans in dict_title.get('translations') or []:
                        if trans.get('key') == 'first_air_date' and trans.get('value'):
                            year = trans.get('value')
                            break
                
                # If still no year, try release_date in translations
                if not year:
                    for trans in dict_title.get('translations') or []:
                        if trans.get('key') == 'release_date' and trans.get('value'):
                            year = trans.get('value')
                            break

                # If still no year, use root level fields
                if not year:
                    year = dict_title.get('last_air_date') or dict_title.get('release_date')

                entries_manager.add(Entries(
                    id=title_id,
                    slug=dict_title.get('slug'),
                    name=dict_title.get('name'),
                    type=dict_title.get('type'),
                    image=image_url,
                    year=year.split("-")[0] if year and "-" in year else "9999",
                    provider_language=lang
                ))

            except Exception as e:
                print(f"[red]Error parsing a film entry ({lang}): {e}")

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