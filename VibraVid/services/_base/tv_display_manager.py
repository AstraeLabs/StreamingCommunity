# 19.06.24

import sys
import logging
from typing import List

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import config_manager, os_manager, tmdb_client
from VibraVid.utils.console import TVShowManager


msg = Prompt()
console = Console()
MOVIE_FORMAT = config_manager.config.get('OUTPUT', 'movie_format')
EPISODE_FORMAT = config_manager.config.get('OUTPUT', 'episode_format')
SEASON_FORMAT = config_manager.config.get('OUTPUT', 'season_format')
SERIES_FORMAT = config_manager.config.get('OUTPUT', 'series_format')
SEASON_PADDING = config_manager.config.get('OUTPUT', 'season_padding')


def dynamic_format_number(number_str: str) -> str:
    """
    Formats an episode number string, intelligently handling both integer and decimal episode numbers.
    
    Parameters:
        - number_str (str): The episode number as a string, which may contain integers or decimals.
    
    Returns:
        - str: The formatted episode number string, with appropriate handling based on the input type.
    """
    try:
        if '.' in number_str:
            return number_str
        
        n = int(number_str)

        if n < 10:
            width = len(str(n)) + 1
        else:
            width = len(str(n))

        return str(n).zfill(width)
    
    except Exception as e:
        logging.warning(f"Could not format episode number '{number_str}': {str(e)}. Using original format.")
        return number_str


def manage_selection(cmd_insert: str, max_count: int) -> List[int]:
    """
    Manage user selection for seasons or episodes to download.

    Parameters:
        - cmd_insert (str): User input for selection.
        - max_count (int): Maximum count available.

    Returns:
        list_selection (List[int]): List of selected items.
    """
    while True:
        list_selection = []

        if cmd_insert.lower() in ("q", "quit"):
            console.print("\n[red]Quit ...")
            sys.exit(0)

        # For all items ('*')
        if cmd_insert == "*":
            list_selection = list(range(1, max_count + 1))
            break

        try:
            # Handle comma separated values and ranges
            parts = cmd_insert.split(",")
            for part in parts:
                part = part.strip()
                if not part:
                    continue

                if "-" in part:
                    start, end = map(str.strip, part.split('-'))
                    start = int(start)

                    # Handle end part (could be numeric or '*' or empty for max_count)
                    if end.isnumeric():
                        end = int(end)
                    else:
                        end = max_count
                    
                    list_selection.extend(list(range(start, end + 1)))
                elif part.isnumeric():
                    list_selection.append(int(part))
                else:
                    raise ValueError
            
            if list_selection:
                list_selection = sorted(list(set(list_selection)))
                break
            
        except (ValueError, TypeError):
            pass

        cmd_insert = msg.ask("[red]Invalid input. Please enter a valid command")
    
    return list_selection


def map_movie_title(title_name: str, title_year: str = None) -> str:
    """
    Maps the movie title to a specific format using the movie_format config.

    Parameters:
        title_name (str): The name of the movie.
        title_year (str): The release year of the movie (optional).

    Returns:
        str: The formatted movie filename (without extension).
    """
    map_movie_temp = MOVIE_FORMAT

    if title_name is not None:
        map_movie_temp = map_movie_temp.replace("%(title_name)", os_manager.get_sanitize_file(title_name))

    if title_year is not None:
        y = str(title_year).split('-')[0].strip()
        if y.isdigit() and len(y) == 4:
            map_movie_temp = map_movie_temp.replace("%(title_year)", y)
        else:
            map_movie_temp = map_movie_temp.replace("(%(title_year))", "").strip()
            map_movie_temp = map_movie_temp.replace("%(title_year)", "").strip()
    else:
        map_movie_temp = map_movie_temp.replace("(%(title_year))", "").strip()
        map_movie_temp = map_movie_temp.replace("%(title_year)", "").strip()

    return map_movie_temp


def map_series_name(series_name: str, series_year: str = None) -> str:
    """
    Maps the series name to a specific format using the series_format config.

    Parameters:
        series_name (str): The name of the series.
        series_year (str): The release year of the series (optional).

    Returns:
        str: The formatted series name for folder naming.
    """
    map_series_temp = SERIES_FORMAT

    if series_name is not None:
        map_series_temp = map_series_temp.replace("%(series_name)", os_manager.get_sanitize_file(series_name))
        map_series_temp = map_series_temp.replace("%(series_slug)", tmdb_client._slugify(series_name))

    if series_year is not None:
        y = str(series_year).split('-')[0].strip()
        if y.isdigit() and len(y) == 4:
            map_series_temp = map_series_temp.replace("%(series_year)", y)
        else:
            map_series_temp = map_series_temp.replace("(%(series_year))", "").strip()
            map_series_temp = map_series_temp.replace("%(series_year)", "").strip()
    else:
        map_series_temp = map_series_temp.replace("(%(series_year))", "").strip()
        map_series_temp = map_series_temp.replace("%(series_year)", "").strip()

    return map_series_temp


def map_episode_title(tv_name: str, number_season: int, episode_number: int, episode_name: str) -> str:
    """
    Maps the episode title to a specific format.

    Parameters:
        tv_name (str): The name of the TV show.
        number_season (int): The season number.
        episode_number (int): The episode number.
        episode_name (str): The original name of the episode.

    Returns:
        str: The mapped episode title.
    """
    map_episode_temp = EPISODE_FORMAT
    
    if tv_name is not None:
        map_episode_temp = map_episode_temp.replace("%(tv_name)", os_manager.get_sanitize_file(tv_name))

    if number_season is not None:
        map_episode_temp = map_episode_temp.replace("%(season)", str(number_season))
    else:
        map_episode_temp = map_episode_temp.replace("%(season)", dynamic_format_number(str(0)))

    if episode_number is not None:
        map_episode_temp = map_episode_temp.replace("%(episode)", dynamic_format_number(str(episode_number)))
    else:
        map_episode_temp = map_episode_temp.replace("%(episode)", dynamic_format_number(str(0)))

    if episode_name is not None:
        map_episode_temp = map_episode_temp.replace("%(episode_name)", os_manager.get_sanitize_file(episode_name))

    return map_episode_temp


def format_season_number(season_number: int) -> str:
    """
    Formats the season number based on the season_padding config.
    
    Parameters:
        season_number (int): The season number.
    
    Returns:
        str: The formatted season number.
    """
    if SEASON_PADDING:
        return str(season_number).zfill(2)
    else:
        return str(season_number)


def map_season_name(season_number: int) -> str:
    """
    Maps the season number to a specific format for folder naming.

    Parameters:
        season_number (int): The season number.

    Returns:
        str: The formatted season name for folder naming.
    """
    map_season_temp = SEASON_FORMAT
    
    if season_number is not None:
        map_season_temp = map_season_temp.replace("%(season)", format_season_number(season_number))
    else:
        map_season_temp = map_season_temp.replace("%(season)", format_season_number(0))

    return map_season_temp


def validate_selection(list_season_select: List[int], available_seasons: List[int]) -> List[int]:
    """
    Validates and adjusts the selected seasons based on the available seasons.

    Parameters:
        - list_season_select (List[int]): List of seasons selected by the user.
        - available_seasons (List[int]): List of available season numbers.

    Returns:
        - List[int]: Adjusted list of valid season numbers.
    """
    while True:
        try:
            
            # Remove any seasons not in the available seasons
            valid_seasons = [season for season in list_season_select if season in available_seasons]

            # If the list is empty, the input was completely invalid
            if not valid_seasons:
                input_seasons = msg.ask(f"[red]Enter valid season numbers ({', '.join(map(str, available_seasons))})")
                list_season_select = list(map(int, input_seasons.split(',')))
                continue
            
            return valid_seasons
        
        except ValueError:
            logging.error("Error: Please enter valid integers separated by commas.")

            # Prompt the user for valid input again
            input_seasons = input(f"Enter valid season numbers ({', '.join(map(str, available_seasons))}): ")
            list_season_select = list(map(int, input_seasons.split(',')))


def display_seasons_list(seasons_manager) -> str:
    """
    Display seasons list and handle user input.

    Parameters:
        - seasons_manager: Manager object containing seasons information.

    Returns:
        last_command (str): Last command entered by the user.
    """
    if len(seasons_manager.seasons) == 1:
        return "1"
    
    # Set up table for displaying seasons
    table_show_manager = TVShowManager()

    # Check if 'type' and 'id' attributes exist in the first season
    try:
        has_type = hasattr(seasons_manager.seasons[0], 'type') and (seasons_manager.seasons[0].type) is not None and str(seasons_manager.seasons[0].type) != ''
        has_id = hasattr(seasons_manager.seasons[0], 'id') and (seasons_manager.seasons[0].id) is not None and str(seasons_manager.seasons[0].id) != ''
    except IndexError:
        has_type = False
        has_id = False

    # Add columns to the table
    column_info = {
        "Index": {'color': 'red'},
        "Name": {'color': 'yellow'}
    }

    if has_type:
        column_info["Type"] = {'color': 'magenta'}
    
    if has_id:
        column_info["ID"] = {'color': 'cyan'}

    table_show_manager.add_column(column_info)

    # Populate the table with seasons information
    for i, season in enumerate(seasons_manager.seasons):
        season_name = season.name if hasattr(season, 'name') else 'N/A'
        season_info = {
            'Index': str(i + 1),
            'Name': season_name
        }

        # Add 'Type' and 'ID' if they exist
        if has_type:
            season_type = season.type if hasattr(season, 'type') else 'N/A'
            season_info['Type'] = season_type
        
        if has_id:
            season_id = season.id if hasattr(season, 'id') else 'N/A'
            season_info['ID'] = season_id

        table_show_manager.add_tv_show(season_info)

    # Run the table and handle user input
    last_command = table_show_manager.run()

    if last_command in ("q", "quit"):
        console.print("\n[red]Quit ...")
        sys.exit(0)

    return last_command


def display_episodes_list(episodes_manager) -> str:
    """
    Display episodes list and handle user input.

    Returns:
        last_command (str): Last command entered by the user.
    """
    # Set up table for displaying episodes
    table_show_manager = TVShowManager()

    # Check if any episode has non-empty fields
    has_category = False
    has_duration = False
    
    for media in episodes_manager:
        category = media.get('category') if isinstance(media, dict) else getattr(media, 'category', None)
        duration = media.get('duration') if isinstance(media, dict) else getattr(media, 'duration', None)
        
        if category is not None and str(category).strip() != '':
            has_category = True
        if duration is not None and str(duration).strip() != '':
            has_duration = True

    # Add columns to the table
    column_info = {
        "Index": {'color': 'red'},
    }
    
    column_info["Name"] = {'color': 'magenta'}
    
    if has_category:
        column_info["Category"] = {'color': 'green'}
    
    if has_duration:
        column_info["Duration"] = {'color': 'blue'}
    
    table_show_manager.add_column(column_info)

    # Populate the table with episodes information
    for i, media in enumerate(episodes_manager):
        name = media.get('name') if isinstance(media, dict) else getattr(media, 'name', None)
        duration = media.get('duration') if isinstance(media, dict) else getattr(media, 'duration', None)
        category = media.get('category') if isinstance(media, dict) else getattr(media, 'category', None)

        episode_info = {
            'Index': str(i + 1),
            'Name': name,
        }
        if has_category:
            episode_info['Category'] = category
        
        if has_duration:
            episode_info['Duration'] = duration

        table_show_manager.add_tv_show(episode_info)

    # Run the table and handle user input
    last_command = table_show_manager.run()

    if last_command in ("q", "quit"):
        console.print("\n[red]Quit ...")
        sys.exit(0)

    return last_command