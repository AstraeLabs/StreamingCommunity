# 17.04.26

from urllib.parse import quote_plus

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.utils.tmdb_client import tmdb_client
from VibraVid.services._base import site_constants, EntriesManager, Entries
from VibraVid.services._base.site_search_manager import base_process_search_result, base_search

from .downloader import download_film, download_series


indice  = 12
_useFor = "Film_Serie"

msg = Prompt()
console = Console()
entries_manager = EntriesManager()
table_show_manager = TVShowManager()
_TMDB_IMG = "https://image.tmdb.org/t/p/w500"


def title_search(query: str) -> int:
    entries_manager.clear()
    table_show_manager.clear()

    q = quote_plus(query)

    # Search film
    movies = tmdb_client._make_request("search/movie", {"query": q, "language": "it"}) or {}
    for m in movies.get('results', [])[:10]:
        poster = f"{_TMDB_IMG}{m['poster_path']}" if m.get('poster_path') else None
        year   = (m.get('release_date') or '')[:4] or None
        entries_manager.add(Entries(
            id    = m['id'],
            name  = m.get('title', ''),
            type  = 'film',
            slug  = 'movie',
            url   = f"https://www.cinezo.net/watch/movie/{m['id']}",
            image = poster,
            year  = year,
        ))

    # Search tv series
    shows = tmdb_client._make_request("search/tv", {"query": q, "language": "it"}) or {}
    for s in shows.get('results', [])[:10]:
        poster = f"{_TMDB_IMG}{s['poster_path']}" if s.get('poster_path') else None
        year   = (s.get('first_air_date') or '')[:4] or None
        entries_manager.add(Entries(
            id    = s['id'],
            name  = s.get('name', ''),
            type  = 'tv',
            slug  = 'tv',
            url   = f"https://www.cinezo.net/watch/tv/{s['id']}",
            image = poster,
            year  = year,
        ))

    return len(entries_manager)

def process_search_result(select_title, selections=None, scrape_serie=None):
    return base_process_search_result(
        select_title         = select_title,
        download_film_func   = download_film,
        download_series_func = download_series,
        media_search_manager = entries_manager,
        table_show_manager   = table_show_manager,
        selections           = selections,
        scrape_serie         = scrape_serie,
    )

def search(string_to_search=None, get_onlyDatabase=False, direct_item=None, selections=None, scrape_serie=None):
    return base_search(
        title_search_func    = title_search,
        process_result_func  = process_search_result,
        media_search_manager = entries_manager,
        table_show_manager   = table_show_manager,
        site_name            = site_constants.SITE_NAME,
        string_to_search     = string_to_search,
        get_onlyDatabase     = get_onlyDatabase,
        direct_item          = direct_item,
        selections           = selections,
        scrape_serie         = scrape_serie,
    )
