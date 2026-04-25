# Adding a New Service to VibraVid

## Project Architecture Overview

Every service lives in its own directory under `VibraVid/services/` and always follows this structure:

```
VibraVid/services/my_new_site/
├── __init__.py       # Entry point: search logic and wrappers
├── scrapper.py       # HTTP scraping: series/episode metadata
├── downloader.py     # Download logic: film and series
└── client.py         # (Optional) Authentication / token management
```

All services share a common base layer located at `VibraVid/services/_base/`, which provides:

| Base Component | Purpose |
|---|---|
| `EntriesManager` / `Entries` | Stores and manages search results |
| `site_search_manager.py` | Generalized search + user selection flow |
| `tv_download_manager.py` | Season/episode selection and download orchestration |
| `tv_display_manager.py` | Terminal UI helpers (`map_episode_path`, `map_movie_path`, etc.) |
| `site_constants` | Site-wide constants (URLs, folder paths, site name) |

---

## Step-by-Step Guide

### 1. Create the service directory

```
VibraVid/services/my_new_site/
```

---

### 2. Write `__init__.py` — The Entry Point

This file wires together the search function and the base search/process pipeline.

**Required module-level variables:**

```python
indice = 99             # Unique integer index for this service
_useFor = "Film_Serie"  # Category: "Film_Serie", "Serie", "Anime", etc.
_region = ["IT"]        # (Optional) ISO region codes where the service is available
```

**Required functions:**

#### `title_search(query: str) -> int`
Performs the HTTP search request, populates `entries_manager`, and returns the number of results found.

```python
def title_search(query: str) -> int:
    entries_manager.clear()
    table_show_manager.clear()

    # Optionally check region availability
    if not check_region_availability(_region, site_constants.SITE_NAME):
        return 0

    # Perform HTTP request to the site's search API
    response = create_client(headers=get_headers()).get(SEARCH_URL, params={"q": query})
    response.raise_for_status()

    for item in response.json().get("results", []):
        entries_manager.add(Entries(
            id=item["id"],
            name=item["title"],
            type="tv",          # "tv", "film", or "movie"
            year=str(item.get("year", "")),
            url=item.get("url", ""),
            image=item.get("thumbnail", ""),
        ))

    return len(entries_manager)
```

#### `process_search_result(select_title, selections=None, scrape_serie=None)`
Thin wrapper around `base_process_search_result`. Always pass `download_film` and `download_series` from your own `downloader.py`.

```python
def process_search_result(select_title, selections=None, scrape_serie=None):
    return base_process_search_result(
        select_title=select_title,
        download_film_func=download_film,
        download_series_func=download_series,
        media_search_manager=entries_manager,
        table_show_manager=table_show_manager,
        selections=selections,
        scrape_serie=scrape_serie,
    )
```

#### `search(...)` 
Thin wrapper around `base_search`. Keep the signature identical across all services.

```python
def search(string_to_search=None, get_onlyDatabase=False, direct_item=None, selections=None, scrape_serie=None):
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
        scrape_serie=scrape_serie,
    )
```

---

### 3. Write `scrapper.py` — Metadata Retrieval

This file contains the class responsible for fetching series and episode data from the site.

**Minimum interface required by `tv_download_manager`:**

```python
class GetSerieInfo:
    def __init__(self, ...):
        self.series_name: str = ""
        self.seasons_manager: SeasonManager = SeasonManager()

    def collect_info_title(self) -> None:
        """Populate seasons_manager with Season objects."""
        ...

    def collect_info_season(self, number_season: int) -> None:
        """Populate season.episodes with Episode objects."""
        ...

    # Required by the GUI layer
    def getNumberSeason(self) -> int:
        if not self.seasons_manager.seasons:
            self.collect_info_title()
        return len(self.seasons_manager.seasons)

    def getEpisodeSeasons(self, season_number: int) -> list:
        season = self.seasons_manager.get_season_by_number(season_number)
        if not season.episodes.episodes:
            self.collect_info_season(season_number)
        return season.episodes.episodes
```

Use the data objects from `VibraVid.services._base.object`:

```python
from VibraVid.services._base.object import SeasonManager, Season, Episode

# Add a season
self.seasons_manager.add(Season(id="s1", number=1, name="Season 1"))

# Add an episode to a season
season = self.seasons_manager.get_season_by_number(1)
season.episodes.add(Episode(id="ep1", number=1, name="Pilot", url="..."))
```

---

### 4. Write `downloader.py` — Download Logic

This file implements the actual download of films and series episodes.

#### `download_film(select_title: Entries) -> Tuple[str, bool]`

```python
def download_film(select_title: Entries):
    start_message()
    # Resolve the streaming URL from the site
    master_playlist = resolve_stream_url(select_title.url)

    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = os.path.join(site_constants.MOVIE_FOLDER, *path_components)

    return HLS_Downloader(
        m3u8_url=master_playlist,
        output_path=os.path.join(movie_path, f"{filename}.{extension_output}")
    ).start()
```

#### `download_episode(obj_episode, index_season, index_episode, scrape_serie)`

```python
def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie):
    start_message()
    master_playlist = resolve_stream_url(obj_episode.url)

    path_components, filename = map_episode_path(
        scrape_serie.series_name, getattr(scrape_serie, "year", None),
        index_season_selected, index_episode_selected, obj_episode.name
    )
    episode_path = os.path.join(site_constants.SERIES_FOLDER, *path_components)

    return HLS_Downloader(
        m3u8_url=master_playlist,
        output_path=os.path.join(episode_path, f"{filename}.{extension_output}")
    ).start()
```

#### `download_series(select_season, season_selection=None, episode_selection=None, scrape_serie=None)`

Use the base helpers `process_season_selection` and `process_episode_download` to handle all selection logic automatically:

```python
def download_series(select_season, season_selection=None, episode_selection=None, scrape_serie=None):
    start_message()
    if scrape_serie is None:
        scrape_serie = GetSerieInfo(select_season.url)
        scrape_serie.collect_info_title()
    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number, download_all, episode_selection=None):
        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie)

        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=download_video_callback,
            download_all=download_all,
            episode_selection=episode_selection,
        )

    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback,
    )
```

---

### 5. (Optional) Write `client.py` — Authentication

Add this file only if the service requires login, tokens, or signed requests.

```python
_cached_token = None

def get_bearer_token() -> str:
    global _cached_token
    if _cached_token:
        return _cached_token
    # Perform login and cache the token
    response = create_client(headers=get_headers()).post(LOGIN_URL, json={...})
    _cached_token = response.json()["access_token"]
    return _cached_token
```

Then import and use it inside `__init__.py` and `downloader.py` as needed.

---

## Minimal File Template

A barebones starting point you can copy and adapt:

```
VibraVid/services/my_new_site/
├── __init__.py     ← copy from an existing service and replace API calls
├── scrapper.py     ← implement GetSerieInfo with collect_info_title/season
├── downloader.py   ← implement download_film, download_episode, download_series
└── client.py       ← only if authentication is needed
```