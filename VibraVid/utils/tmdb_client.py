# 24.08.24

import re
import time
import logging
import unicodedata
from difflib import SequenceMatcher

from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client_curl, get_userAgent


console = Console()
logger = logging.getLogger(__name__)
api_key = config_manager.login.get("TMDB", "api_key")


class TMDBClient:
    def __init__(self, api_key: str):
        """Initialize the class with the API key."""
        self.api_key = api_key
        self.base_url = "https://api.themoviedb.org/3"

    def _make_request(self, endpoint, params=None, retries=3):
        """Make a request to the given API endpoint with optional parameters."""
        if params is None:
            params = {}

        if self.api_key is None or self.api_key == "":
            logger.error("TMDB API key is not set. Please provide a valid API key.")
            return {}

        params['api_key'] = self.api_key
        url = f"{self.base_url}/{endpoint}"
        
        for attempt in range(retries + 1):
            try:
                response = create_client_curl(headers={"User-Agent": get_userAgent()}).get(url, params=params)
                response.raise_for_status()
                return response.json()
            
            except Exception as e:
                if attempt < retries:
                    if hasattr(e, 'response') and e.response:
                        status_code = e.response.status_code
                        if status_code in [429, 500, 502, 503, 504]:
                            wait_time = 2 ** attempt
                            console.log(f"[yellow]TMDB API error {status_code}, retrying in {wait_time}s... ({attempt+1}/{retries})[/yellow]")
                            time.sleep(wait_time)
                            continue
                
                console.log(f"[red]Error making request to {endpoint}: {e}[/red]")
                return {}
        
        return {}

    def _slugify(self, text):
        """Normalize and slugify a given text."""
        text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
        text = re.sub(r'[^\w\s-]', '', text).strip().lower()
        text = re.sub(r'[-\s]+', '-', text)
        return text

    def _slugs_match(self, slug1: str, slug2: str, threshold: float = 0.85) -> bool:
        """Check if two slugs are similar enough using fuzzy matching."""
        ratio = SequenceMatcher(None, slug1, slug2).ratio()
        return ratio >= threshold

    def get_type_and_id_by_slug_year(self, slug: str, year: str = None, media_type: str = None, language_preference: str = "it"):
        """Get the type (movie or tv) and ID from TMDB based on slug and year."""
        # Anime often dont have a year, so we should be flexible with it
        if year:
            year = int(year)

        if media_type == "movie":
            movie_results = self._make_request("search/movie", {"query": slug.replace('-', ' '), "language": language_preference}).get("results", [])
            logger.info(f"Found {len(movie_results)} movie results for slug '{slug}' and year '{year}'")

            # 1 result
            if len(movie_results) == 1:
                return {'type': "movie", 'id': movie_results[0]['id']}
            
            # Multiple results
            for movie in movie_results:
                title = movie.get('title')
                release_date = movie.get('release_date')
                
                if release_date:
                    movie_year = int(release_date[:4])
                else:
                    continue
                
                movie_slug = self._slugify(title)
                
                # Use fuzzy matching instead of exact comparison
                if self._slugs_match(movie_slug, slug) and (not year or movie_year == year):
                    return {'type': "movie", 'id': movie['id']}
        
        elif media_type == "tv":
            tv_results = self._make_request("search/tv", {"query": slug.replace('-', ' '), "language": language_preference}).get("results", [])
            logger.info(f"Found {len(tv_results)} TV results for slug '{slug}' and year '{year}'")

            # 1 result
            if len(tv_results) == 1:
                return {'type': "tv", 'id': tv_results[0]['id']}
            
            # Multiple results
            for show in tv_results:
                name = show.get('name')
                first_air_date = show.get('first_air_date')
                
                if first_air_date:
                    show_year = int(first_air_date[:4])
                else:
                    continue
                
                show_slug = self._slugify(name)
                
                # Use fuzzy matching instead of exact comparison
                if self._slugs_match(show_slug, slug) and (not year or show_year == year):
                    return {'type': "tv", 'id': show['id']}
                
        else:
            print("Media type not specified. Searching both movie and tv.")
            return None

    def get_year_by_slug_and_type(self, slug: str, media_type: str, language_preference: str = "it"):
        """Returns the year from the first search result that matches the slug."""
        if media_type == "movie":
            results = self._make_request("search/movie", {"query": slug.replace('-', ' '), "language": language_preference}).get("results", [])
            logger.info(f"Found {len(results)} movie results for slug '{slug}'")

            # 1 result
            if len(results) == 1:
                return int(results[0]['release_date'][:4])
            
            # Multiple results
            for movie in results:
                title = movie.get('title')
                release_date = movie.get('release_date')
                
                if not release_date:
                    continue
                
                movie_slug = self._slugify(title)
                
                # Use fuzzy matching
                if self._slugs_match(movie_slug, slug):
                    return int(release_date[:4])
                    
        elif media_type == "tv":
            results = self._make_request("search/tv", {"query": slug.replace('-', ' '), "language": language_preference}).get("results", [])
            logger.info(f"Found {len(results)} TV results for slug '{slug}'")

            # 1 result
            if len(results) == 1:
                return int(results[0]['first_air_date'][:4])
            
            # Multiple results
            for show in results:
                name = show.get('name')
                first_air_date = show.get('first_air_date')
                
                if not first_air_date:
                    continue
                
                show_slug = self._slugify(name)
                
                # Use fuzzy matching
                if self._slugs_match(show_slug, slug):
                    return int(first_air_date[:4])
        
        return None

    def get_backdrop_url(self, media_type: str, tmdb_id: int, size: str = "w1280"):
        """Get the backdrop URL for a movie or TV show."""
        try:
            logger.info(f"Getting backdrop for {media_type} with TMDB ID {tmdb_id}")
            details = self._make_request(f"{media_type}/{tmdb_id}", {"language": "it"})
            backdrop_path = details.get('backdrop_path')

            if backdrop_path:
                return f"https://image.tmdb.org/t/p/{size}{backdrop_path}"
            
        except Exception as e:
            console.log(f"[red]Error getting backdrop for {media_type} {tmdb_id}: {e}[/red]")
            logger.error(f"Error getting backdrop for {media_type} {tmdb_id}: {e}")

        return None

    def search_movie(self, query: str):
        """Search for a movie and return the TMDB ID of the first result."""
        results = self._make_request("search/movie", {"query": query, "language": "it"}).get("results", [])
        logger.info(f"Found {len(results)} movie results for query '{query}'")

        if results:
            return results[0]['id']
        return None

    def get_movie_details(self, tmdb_id: int):
        """Get movie details including title and IMDB ID."""
        details = self._make_request(f"movie/{tmdb_id}", {"language": "it"})
        logger.info(f"Got details for movie ID {tmdb_id}: {details.get('title')} (IMDB ID: {details.get('imdb_id')})")

        return {
            'title': details.get('title'),
            'imdb_id': details.get('imdb_id')
        }

    def search_movies(self, query: str, language_preference: str = "it"):
        """
        Search for movies and return a list of results with details.
        Only returns movies that have a valid IMDB ID.
        
        Parameters:
            - query (str): The search query
            - language_preference (str): Language preference (default: "it")
            
        Returns:
            - list: List of dicts containing movie info (id, title, release_date, imdb_id, popularity)
        """
        results = self._make_request("search/movie", {"query": query, "language": language_preference}).get("results", [])
        logger.info(f"Found {len(results)} movie results for query '{query}' and language '{language_preference}'")
        
        movies = []
        for movie in results:
            details = self._make_request(f"movie/{movie.get('id')}", {"language": language_preference})
            imdb_id = details.get('imdb_id')
            
            # Only include movies with valid IMDB ID
            if imdb_id:
                movie_data = {
                    'id': movie.get('id'),
                    'title': movie.get('title'),
                    'release_date': movie.get('release_date'),
                    'popularity': movie.get('popularity'),
                    'poster_path': movie.get('poster_path'),
                    'imdb_id': imdb_id
                }
                movies.append(movie_data)
        
        return movies

    def search_series(self, query: str, language_preference: str = "it"):
        """
        Search for TV series and return a list of results with details.
        
        Parameters:
            - query (str): The search query
            - language_preference (str): Language preference (default: "it")
            
        Returns:
            - list: List of dicts containing series info (id, name, first_air_date, popularity)
        """
        results = self._make_request("search/tv", {"query": query, "language": language_preference}).get("results", [])
        logger.info(f"Found {len(results)} TV results for query '{query}' and language '{language_preference}'")
        
        series = []
        for show in results:
            series_data = {
                'id': show.get('id'),
                'name': show.get('name'),
                'first_air_date': show.get('first_air_date'),
                'popularity': show.get('popularity'),
                'poster_path': show.get('poster_path')
            }
            series.append(series_data)
        
        return series


# Istance
tmdb_client = TMDBClient(api_key)
tmdb = tmdb_client