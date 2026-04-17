# 09.06.24

from bs4 import BeautifulSoup
from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, get_userAgent
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_series


indice = 4
_useFor = "Serie"
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
        - int: The number of titles found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    search_url = f"{site_constants.FULL_URL}/?story={query}&do=search&subaction=search"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    try:
        response = create_client(headers={'user-agent': get_userAgent()}).get(search_url)
        response.raise_for_status()
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Create soup and find table
    soup = BeautifulSoup(response.text, "html.parser")

    for serie_div in soup.find_all('div', class_='movie'):
        try:
            a   = serie_div.find('a')
            img = serie_div.find('img')

            title_tag = serie_div.find(class_=lambda c: c and 'title' in str(c).lower())
            name = (title_tag.get_text(strip=True) if title_tag else
                    a.get('title', '').replace('streaming guardaserie', '').strip() or
                    a.get_text(strip=True))

            image = (img.get('data-src') or img.get('src', '')) if img else ''
            if image and image.startswith('/'):
                image = site_constants.FULL_URL + image

            entries_manager.add(Entries(
                name  = name,
                type  = 'tv',
                url   = a.get('href', ''),
                image = image,
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