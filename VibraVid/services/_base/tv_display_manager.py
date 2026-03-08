# 19.06.24

import sys
import logging
import re as _re
from typing import List

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import config_manager, os_manager, tmdb_client
from VibraVid.utils.console import TVShowManager


msg = Prompt()
console = Console()
MOVIE_FORMAT = config_manager.config.get('OUTPUT', 'movie_format')
EPISODE_FORMAT = config_manager.config.get('OUTPUT', 'episode_format')


def _apply_format_token(token: str, value: int) -> str:
    """
    Applies an inline format specifier to a numeric value.

    Supported specifiers:
        - '02d'  -> zero-padded to 2 digits  (e.g. 01, 10, 100)
        - '03d'  -> zero-padded to 3 digits  (e.g. 001, 010, 100)
        - 'd'    -> no padding               (e.g. 1, 10, 100)

    Parameters:
        token (str): The format specifier extracted from the template.
        value (int): The numeric value to format.

    Returns:
        str: The formatted number string.
    """
    if token == 'd':
        return str(value)
    if token.endswith('d') and token[:-1].isdigit():
        return str(value).zfill(int(token[:-1]))
    return str(value).zfill(2)


def _replace_format_key(fmt_string: str, key: str, value) -> str:
    """
    Replaces all occurrences of %(key:FORMAT) in a format string.

    Supported syntax:
        %(season:02d)   -> zero-padded to 2 digits
        %(season:03d)   -> zero-padded to 3 digits
        %(season:d)     -> no padding
        %(episode:02d)  -> zero-padded to 2 digits
        %(episode:d)    -> no padding

    Parameters:
        fmt_string (str): The format string to process.
        key (str): The token key to replace (e.g. 'season', 'episode').
        value: The numeric value to substitute.

    Returns:
        str: The format string with all occurrences of the key replaced.
    """
    def replacer(match):
        token = match.group(1)
        try:
            n = int(str(value))
            return _apply_format_token(token, n)
        except (ValueError, TypeError):
            return str(value)

    pattern = _re.compile(r'%\(' + _re.escape(key) + r':([^)]+)\)')
    return pattern.sub(replacer, fmt_string)


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

        # Support both %(title_name) and %(title_name_slug)
        map_movie_temp = map_movie_temp.replace("%(title_name)", os_manager.get_sanitize_file(title_name))
        map_movie_temp = map_movie_temp.replace("%(title_name_slug)", tmdb_client._slugify(title_name))

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
    Returns the sanitized series name for folder naming.

    Parameters:
        series_name (str): The name of the series.
        series_year (str): The release year of the series (optional).

    Returns:
        str: The formatted series name for folder naming.
    """
    result = series_name

    if series_name is not None:
        result = os_manager.get_sanitize_file(series_name)

    return result


def map_episode_title(tv_name: str, number_season: int, episode_number: int, episode_name: str) -> str:
    """
    Maps the episode title to a specific filename format.

    Parameters:
        tv_name (str): The name of the TV show.
        number_season (int): The season number.
        episode_number (int): The episode number.
        episode_name (str): The original name of the episode.

    Returns:
        str: The mapped episode filename (without extension and path).
    """
    # Extract only the filename part (after the last /)
    episode_format_parts = EPISODE_FORMAT.split('/')
    filename_format = episode_format_parts[-1] if episode_format_parts else EPISODE_FORMAT

    map_episode_temp = filename_format

    if tv_name is not None:
        map_episode_temp = map_episode_temp.replace("%(tv_name)", os_manager.get_sanitize_file(tv_name))
        map_episode_temp = map_episode_temp.replace("%(series_name)", os_manager.get_sanitize_file(tv_name))
        map_episode_temp = map_episode_temp.replace("%(series_name_slug)", tmdb_client._slugify(tv_name))

    season_val = number_season if number_season is not None else 0
    map_episode_temp = _replace_format_key(map_episode_temp, 'season', season_val)

    episode_val = episode_number if episode_number is not None else 0
    map_episode_temp = _replace_format_key(map_episode_temp, 'episode', episode_val)

    if episode_name is not None:
        map_episode_temp = map_episode_temp.replace("%(episode_name)", os_manager.get_sanitize_file(episode_name))
        map_episode_temp = map_episode_temp.replace("%(episode_name_slug)", tmdb_client._slugify(episode_name))

    return map_episode_temp


def map_season_name(season_number: int) -> str:
    """
    Maps the season number to a specific format for folder naming.
    Reads the season segment directly from EPISODE_FORMAT.

    Parameters:
        season_number (int): The season number.

    Returns:
        str: The formatted season folder name (e.g., "S01", "S1", "S001").
    """
    # Find the path segment containing %(season:...) to use as folder name template
    episode_parts = EPISODE_FORMAT.split('/')
    season_segment = None
    for part in episode_parts:
        if _re.search(r'[Ss]%\(season:', part):
            season_segment = part
            break

    if season_segment is None:
        season_segment = "S%(season:02d)"

    val = season_number if season_number is not None else 0
    return _replace_format_key(season_segment, 'season', val)


def map_episode_path(series_name: str, series_year: str = None, season_number: int = None, episode_number: int = None, episode_name: str = None) -> tuple:
    """
    Maps the complete episode path and filename using the consolidated episode_format config.

    Parameters:
        series_name (str): The name of the series.
        series_year (str): The release year of the series (optional).
        season_number (int): The season number.
        episode_number (int): The episode number.
        episode_name (str): The name of the episode.

    Returns:
        tuple: (path_components, filename) where path_components is a list for path assembly
               and filename is the final episode filename.
    """
    map_episode_temp = EPISODE_FORMAT

    # Replace series_name and its variant
    if series_name is not None:
        map_episode_temp = map_episode_temp.replace("%(series_name)", os_manager.get_sanitize_file(series_name))
        map_episode_temp = map_episode_temp.replace("%(series_name_slug)", tmdb_client._slugify(series_name))

    # Replace series_year if present
    if series_year is not None:
        y = str(series_year).split('-')[0].strip()
        if y.isdigit() and len(y) == 4:
            map_episode_temp = map_episode_temp.replace("%(series_year)", y)
        else:
            map_episode_temp = map_episode_temp.replace("(%(series_year))", "").strip()
            map_episode_temp = map_episode_temp.replace("%(series_year)", "").strip()
    else:
        map_episode_temp = map_episode_temp.replace("(%(series_year))", "").strip()
        map_episode_temp = map_episode_temp.replace("%(series_year)", "").strip()

    # Replace season and episode numbers (honours inline format spec e.g. :02d, :d, :03d)
    season_val = season_number if season_number is not None else 0
    map_episode_temp = _replace_format_key(map_episode_temp, 'season', season_val)

    episode_val = episode_number if episode_number is not None else 0
    map_episode_temp = _replace_format_key(map_episode_temp, 'episode', episode_val)

    # Replace episode_name and its variant
    if episode_name is not None:
        map_episode_temp = map_episode_temp.replace("%(episode_name)", os_manager.get_sanitize_file(episode_name))
        map_episode_temp = map_episode_temp.replace("%(episode_name_slug)", tmdb_client._slugify(episode_name))
    
    # Split into path components and filename
    parts = map_episode_temp.split('/')
    filename = parts[-1] if parts else map_episode_temp
    path_components = parts[:-1] if len(parts) > 1 else []
    
    return (path_components, filename)


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

    # Check if 'type', 'id' or 'extra' attributes exist in seasons
    try:
        first = seasons_manager.seasons[0]
        has_type = hasattr(first, 'type') and (first.type) is not None and str(first.type) != ''
        has_id = hasattr(first, 'id') and (first.id) is not None and str(first.id) != ''
    except IndexError:
        has_type = False
        has_id = False

    # Determine if any season has a non-empty extra field
    has_extra = False
    for s in seasons_manager.seasons:
        extra = getattr(s, 'extra', None)
        if extra is not None and str(extra).strip() != '':
            has_extra = True
            break

    # Add columns to the table
    column_info = {
        "Index": {'color': 'red'},
        "Name": {'color': 'yellow'}
    }

    if has_type:
        column_info["Type"] = {'color': 'magenta'}
    
    if has_id:
        column_info["ID"] = {'color': 'cyan'}
    
    if has_extra:
        column_info["Extra"] = {'color': 'green'}

    table_show_manager.add_column(column_info)

    # Populate the table with seasons information
    for i, season in enumerate(seasons_manager.seasons):
        season_name = season.name if hasattr(season, 'name') else 'N/A'
        season_info = {
            'Index': str(i + 1),
            'Name': season_name
        }

        # Add 'Type', 'ID' and 'Extra' if they exist
        if has_type:
            season_type = season.type if hasattr(season, 'type') else 'N/A'
            season_info['Type'] = season_type
        
        if has_id:
            season_id = season.id if hasattr(season, 'id') else 'N/A'
            season_info['ID'] = season_id
        
        if has_extra:
            season_extra = getattr(season, 'extra', '')
            season_info['Extra'] = season_extra

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

    # Check if any episode has non-empty category/duration fields
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