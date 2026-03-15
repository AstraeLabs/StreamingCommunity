# 16.12.25

import re

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.utils.http_client import create_client, get_userAgent, check_region_availability
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_series, download_film
from .client import get_bearer_token


indice = 10
_useFor = "Serie"
_region = ["US"]
msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()


def title_to_slug(title):
    """Convert a title to a URL-friendly slug"""
    slug = title.lower()
    slug = re.sub(r'[^a-z0-9\s-]', '', slug)
    slug = re.sub(r'\s+', '-', slug)
    slug = slug.strip('-')
    return slug

def affinity_score(element, keyword):
    """Calculate relevance score for search results"""
    score = 0
    title = element.get("title", "").lower()
    description = element.get("description", "").lower()
    tags = [t.lower() for t in element.get("tags", [])]
    
    if keyword.lower() in title:
        score += 10
    if keyword.lower() in description:
        score += 5
    if keyword.lower() in tags:
        score += 3

    return score

def title_search(query: str) -> int:
    """
    Search for titles on Tubi TV based on a search query.
      
    Parameters:
        - query (str): The query to search for.

    Returns:
        int: The number of titles found.
    """
    entries_manager.clear()
    table_show_manager.clear()

    if not check_region_availability(_region, site_constants.SITE_NAME):
        return 0

    try:
        headers = {
            'authorization': f"Bearer {get_bearer_token()}",
            'user-agent': get_userAgent(),
        }

        search_url = 'https://search.production-public.tubi.io/api/v2/search'
        console.print(f"[cyan]Search url: [yellow]{search_url}")

        params = {'search': query}
        response = create_client(headers=headers).get(search_url, params=params)
        response.raise_for_status()

    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request search error: {e}")
        return 0

    # Collect json data
    try:
        contents_dict = response.json().get('contents', {})
        elements = list(contents_dict.values())
        
        # Sort by affinity score
        elements_sorted = sorted(
            elements, 
            key=lambda x: affinity_score(x, query), 
            reverse=True
        )

    except Exception as e:
        console.log(f"Error parsing JSON response: {e}")
        return 0

    # Process results
    for element in elements_sorted[:20]:
        try:
            type_content = "tv" if element.get("type", "") == "s" else "movie"
            year = element.get("year", "")
            content_id = element.get("id", "")
            title = element.get("title", "")
            
            # Build URL
            if type_content == "tv":
                url = f"https://tubitv.com/series/{content_id}/{title_to_slug(title)}"
            else:
                url = f"https://tubitv.com/movies/{content_id}/{title_to_slug(title)}"
            
            # Get thumbnail
            thumbnail = ""
            if "thumbnails" in element and element["thumbnails"]:
                thumbnail = element["thumbnails"][0]
            
            entries_manager.add(Entries(
                name=title,
                type=type_content,
                year=str(year) if year else "9999",
                image=thumbnail,
                url=url,
            ))
            
        except Exception as e:
            console.print(f"[yellow]Error parsing a title entry: {e}")
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