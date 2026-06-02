# 19.06.24

from .site_costant import site_constants
from .site_loader import load_search_functions
from .object import EntriesManager, Entries
from .output_path import movie_folder, series_folder, anime_folder, music_folder

__all__ = [
    "site_constants",
    "load_search_functions",
    "EntriesManager",
    "Entries",
    "movie_folder",
    "series_folder",
    "anime_folder",
    "music_folder",
]
