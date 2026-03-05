# 16.12.25

from bs4 import BeautifulSoup
from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client_curl, get_userAgent
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_film


indice = 11
_useFor = "Film_Serie"
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

    search_url = f"https://www.ipersphera.com/?s={query}"
    console.print(f"[cyan]Search url: [yellow]{search_url}")

    try:
        response = create_client_curl(headers={'user-agent': get_userAgent()}).get(search_url)
        response.raise_for_status()
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Create soup instance
    soup = BeautifulSoup(response.text, "html.parser")
    table = soup.find("div", id="content")

    # Track seen URLs to avoid duplicates
    seen_urls = set()
    articles = table.find_all("article")
    
    for i, article in enumerate(articles):
        title_element = article.find("h2", class_="entry-title")
        link = title_element.find("a") if title_element else None
        title = link.text.strip() if link else "N/A"
        url = link.get('href', '') if link else "N/A"

        # Skip duplicates
        if url in seen_urls:
            continue
        seen_urls.add(url)
        
        # Determine type based on categories
        categs_div = article.find("div", class_="categs")
        tipo = "film"
        if categs_div:
            categs_text = categs_div.get_text().lower()
            if "serie" in categs_text or "tv" in categs_text:
                tipo = "tv"

        entries_manager.add(Entries(
            url=url,
            name=title,
            type=tipo
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
        download_series_func=None,
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