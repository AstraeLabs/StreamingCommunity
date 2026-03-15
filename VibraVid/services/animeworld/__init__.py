# 21.03.25

from bs4 import BeautifulSoup
from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils.http_client import create_client, get_headers
from VibraVid.utils import TVShowManager
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_film, download_series


indice = 6
_useFor = "Anime"
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_search(query: str) -> int:
    """
    Function to perform an anime search using a provided title.

    Parameters:
        - query (str): The query to search for.

    Returns:
        - int: A number containing the length of media search manager.
    """
    entries_manager.clear()
    table_show_manager.clear()

    search_url = f"{site_constants.FULL_URL}/search?keyword={query}"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    # Make the GET request
    try:
        response = create_client(headers=get_headers()).get(search_url)
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Create soup istance
    soup = BeautifulSoup(response.text, 'html.parser')

    # Collect data from soup
    for element in soup.find_all('a', class_='poster'):
        try:
            title = element.find('img').get('alt')
            url = f"{site_constants.FULL_URL}{element.get('href')}"
            status_div = element.find('div', class_='status')
            is_dubbed = False
            anime_type = 'TV'

            if status_div:
                if status_div.find('div', class_='dub'):
                    is_dubbed = True
                
                if status_div.find('div', class_='movie'):
                    anime_type = 'Movie'
                elif status_div.find('div', class_='ona'):
                    anime_type = 'ONA'

                entries_manager.add(Entries(
                    name=title,
                    type=anime_type,
                    DUB=is_dubbed,
                    url=url,
                    image=element.find('img').get('src')
                ))

        except Exception as e:
            print(f"Error parsing a film entry: {e}")

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