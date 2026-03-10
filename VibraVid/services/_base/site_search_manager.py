# 01.10.25 

import logging
from typing import Callable, Optional, Dict, Any

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import TVShowManager
from VibraVid.services._base import Entries, EntriesManager


console = Console()
msg = Prompt()
logger = logging.getLogger(__name__)
available_colors = ['red', 'magenta', 'yellow', 'cyan', 'green', 'blue', 'white']
column_to_hide = ['Slug', 'Sub_ita', 'First_air_date', 'Seasons_count', 'Url', 'Image', 'Path_id', 'Score']


def get_select_title(table_show_manager, media_search_manager): 
    """
    Display a selection of titles and prompt the user to choose one.

    Parameters:
        table_show_manager: Manager for console table display.
        media_search_manager: Manager holding the list of media items.

    Returns:
        Entries: The selected media item, or None if no selection is made or an error occurs.
    """
    if not media_search_manager.media_list:
        return None

    if not media_search_manager.media_list:
        console.print("\n[red]No media items available.")
        logger.info("No media items available for selection.")
        return None
    
    first_media_item = media_search_manager.media_list[0]
    column_info = {"Index": {'color': available_colors[0]}}

    color_index = 1
    for key in first_media_item.__dict__.keys():

        if key.capitalize() in column_to_hide:
            continue

        if key in ('id', 'type', 'name', 'score'):
            if key == 'type': 
                column_info["Type"] = {'color': 'yellow'}

            elif key == 'name': 
                column_info["Name"] = {'color': 'magenta'}
            elif key == 'score': 
                column_info["Score"] = {'color': 'cyan'}
                
        else:
            column_info[key.capitalize()] = {'color': available_colors[color_index % len(available_colors)]}
            color_index += 1

    table_show_manager.clear() 
    table_show_manager.add_column(column_info)

    for i, media in enumerate(media_search_manager.media_list):
        media_dict = {'Index': str(i)}
        for key in first_media_item.__dict__.keys():
            if key.capitalize() in column_to_hide:
                continue
            media_dict[key.capitalize()] = str(getattr(media, key))
        table_show_manager.add_tv_show(media_dict)

    while True:
        last_command_str = table_show_manager.run(force_int_input=True, max_int_input=len(media_search_manager.media_list))
        
        if last_command_str is None or last_command_str.lower() in ["q", "quit"]: 
            table_show_manager.clear()
            console.print("\n[red]Selection cancelled by user.")
            return None 

        try:
            selected_index = int(last_command_str)
            
            if 0 <= selected_index < len(media_search_manager.media_list):
                table_show_manager.clear()
                return media_search_manager.get(selected_index)
            else:
                console.print("\n[red]Invalid or out-of-range index. Please try again.")
                logger.info("Invalid or out-of-range index selected.")

        except ValueError:
            console.print("\n[red]Non-numeric input received. Please try again.")
            logger.info("Non-numeric input received.")

def base_process_search_result(select_title: Optional[Entries], download_film_func: Optional[Callable[[Entries], Any]] = None, download_series_func: Optional[Callable[[Entries, Optional[str], Optional[str], Optional[Any]], Any]] = None,
    media_search_manager: Optional[EntriesManager] = None, table_show_manager: Optional[TVShowManager] = None, selections: Optional[Dict[str, str]] = None, scrape_serie: Optional[Any] = None
) -> bool:
    """
    Handles the search result and initiates the download for either a film or series.
    
    Parameters:
        select_title (Entries): The selected media item. Can be None if selection fails.
        download_film_func (callable, optional): Function to download a film
        download_series_func (callable, optional): Function to download a series
        media_search_manager (EntriesManager, optional): Manager to clear after processing
        table_show_manager (TVShowManager, optional): Manager to clear after processing
        selections (dict, optional): Dictionary containing selection inputs that bypass manual input
                                    e.g., {'season': season_selection, 'episode': episode_selection}
        scrape_serie (Any, optional): Pre-existing scraper instance to avoid recreation
    
    Returns:
        bool: True if processing was successful, False otherwise
    """
    if not select_title:
        console.print("[yellow]No title selected or selection cancelled.")
        logger.info("No title selected or selection cancelled.")
        return False
    
    # Handle TV series
    if str(select_title.type).lower() in ['tv', 'serie', 'ova', 'ona', 'show']:
        if not download_series_func:
            console.print("[red]Error: download_series_func not provided for TV series")
            return False
            
        season_selection = None
        episode_selection = None
        
        if selections:
            season_selection = selections.get('season')
            episode_selection = selections.get('episode')
            if not scrape_serie:
                scrape_serie = selections.get('scrape_serie')
        
        download_series_func(select_title, season_selection, episode_selection, scrape_serie)
        
        # Clear managers if provided
        if media_search_manager:
            media_search_manager.clear()
        if table_show_manager:
            table_show_manager.clear()
        
        return True
    
    # Handle films
    elif str(select_title.type).lower() == 'film' or str(select_title.type).lower() == 'movie':
        if not download_film_func:
            console.print("[red]Error: download_film_func not provided for films")
            return False
            
        download_film_func(select_title)
        
        # Clear managers if provided
        if table_show_manager:
            table_show_manager.clear()
        
        return True
    
    else:
        console.print(f"[red]Unknown media type: {select_title.type}")
        logger.error(f"Unknown media type: {select_title.type}")
        return False


def base_search(title_search_func: Callable[[str], int], process_result_func: Callable[[Optional[Entries], Optional[Dict[str, str]], Optional[Any]], bool], media_search_manager: EntriesManager, table_show_manager: TVShowManager,
    site_name: str, string_to_search: Optional[str] = None, get_onlyDatabase: bool = False, direct_item: Optional[Dict[str, Any]] = None, selections: Optional[Dict[str, str]] = None, scrape_serie: Optional[Any] = None
) -> Any:
    """
    Generalized search function for streaming sites.
    
    Parameters:
        title_search_func (callable): Function that performs the actual search and returns number of results
        process_result_func (callable): Function that processes the selected result
        media_search_manager (EntriesManager): Manager for media search results
        table_show_manager (TVShowManager): Manager for displaying results
        site_name (str): Name of the site being searched
        string_to_search (str, optional): String to search for. Can be passed from run.py.
        get_onlyDatabase (bool, optional): If True, return only the database search manager object.
        direct_item (dict, optional): Direct item to process (bypasses search).
        selections (dict, optional): Dictionary containing selection inputs that bypass manual input
                                     for series (season/episode).
        scrape_serie (Any, optional): Pre-existing scraper instance to avoid recreation.
    
    Returns:
        EntriesManager if get_onlyDatabase=True, bool otherwise
    """
    # Handle direct item processing
    if direct_item:
        logger.info("Processing direct item without search.")
        select_title = Entries(**direct_item)
        result = process_result_func(select_title, selections, scrape_serie)
        return result
    
    # Get the user input for the search term
    actual_search_query = None
    if string_to_search is not None:
        logger.info(f"Using provided search string: {string_to_search}")
        actual_search_query = string_to_search.strip()
    else:
        logger.info("Prompting user for search input.")
        actual_search_query = msg.ask(f"\n[purple]Insert a word to search in [green]{site_name}").strip()

    # Search on database
    len_database = title_search_func(str(actual_search_query).strip())
    
    # Sort results by fuzzy score
    logger.info(f"Sorting {len_database} results by fuzzy score for query: '{actual_search_query}'")
    media_search_manager.sort_by_fuzzy_score(actual_search_query)
    
    # Handle empty input
    if not actual_search_query:
        logger.error("Empty search query provided.")
        return False
    
    # If only the database is needed, return the manager
    if get_onlyDatabase:
        return media_search_manager
    
    # Process results
    if len_database > 0:
        select_title = get_select_title(table_show_manager, media_search_manager)
        result = process_result_func(select_title, selections, scrape_serie)
        return result
    else:
        console.print(f"\n[red]Nothing matching was found for[white]: [purple]{actual_search_query}")
        return False