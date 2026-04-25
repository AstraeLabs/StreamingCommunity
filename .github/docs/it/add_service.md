# Aggiungere un Nuovo Servizio a VibraVid

## Panoramica dell'Architettura del Progetto

Ogni servizio vive nella propria directory sotto `VibraVid/services/` e segue sempre questa struttura:

```
VibraVid/services/mio_nuovo_sito/
├── __init__.py       # Entry point: logica di ricerca e wrapper
├── scrapper.py       # Scraping HTTP: metadati di serie/episodi
├── downloader.py     # Logica di download: film e serie
└── client.py         # (Opzionale) Autenticazione / gestione token
```

Tutti i servizi condividono un livello base comune situato in `VibraVid/services/_base/`, che fornisce:

| Componente Base | Scopo |
|---|---|
| `EntriesManager` / `Entries` | Memorizza e gestisce i risultati di ricerca |
| `site_search_manager.py` | Flusso generalizzato di ricerca + selezione utente |
| `tv_download_manager.py` | Selezione stagione/episodio e orchestrazione del download |
| `tv_display_manager.py` | Helper per la UI da terminale (`map_episode_path`, `map_movie_path`, ecc.) |
| `site_constants` | Costanti del sito (URL, percorsi cartelle, nome del sito) |

---

## Guida Passo-Passo

### 1. Crea la directory del servizio

```
VibraVid/services/mio_nuovo_sito/
```

---

### 2. Scrivi `__init__.py` — L'Entry Point

Questo file collega la funzione di ricerca con la pipeline base di ricerca/elaborazione.

**Variabili richieste a livello di modulo:**

```python
indice = 99             # Indice intero univoco per questo servizio
_useFor = "Film_Serie"  # Categoria: "Film_Serie", "Serie", "Anime", ecc.
_region = ["IT"]        # (Opzionale) Codici ISO delle regioni dove il servizio è disponibile
```

**Funzioni richieste:**

#### `title_search(query: str) -> int`
Esegue la richiesta HTTP di ricerca, popola `entries_manager` e restituisce il numero di risultati trovati.

```python
def title_search(query: str) -> int:
    entries_manager.clear()
    table_show_manager.clear()

    # Controlla facoltativamente la disponibilità regionale
    if not check_region_availability(_region, site_constants.SITE_NAME):
        return 0

    # Esegui la richiesta HTTP all'API di ricerca del sito
    response = create_client(headers=get_headers()).get(SEARCH_URL, params={"q": query})
    response.raise_for_status()

    for item in response.json().get("results", []):
        entries_manager.add(Entries(
            id=item["id"],
            name=item["title"],
            type="tv",          # "tv", "film", o "movie"
            year=str(item.get("year", "")),
            url=item.get("url", ""),
            image=item.get("thumbnail", ""),
        ))

    return len(entries_manager)
```

#### `process_search_result(select_title, selections=None, scrape_serie=None)`
Wrapper sottile attorno a `base_process_search_result`. Passa sempre `download_film` e `download_series` dal tuo `downloader.py`.

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
Wrapper sottile attorno a `base_search`. Mantieni la firma identica in tutti i servizi.

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

### 3. Scrivi `scrapper.py` — Recupero Metadati

Questo file contiene la classe responsabile del recupero dei dati di serie ed episodi dal sito.

**Interfaccia minima richiesta da `tv_download_manager`:**

```python
class GetSerieInfo:
    def __init__(self, ...):
        self.series_name: str = ""
        self.seasons_manager: SeasonManager = SeasonManager()

    def collect_info_title(self) -> None:
        """Popola seasons_manager con oggetti Season."""
        ...

    def collect_info_season(self, number_season: int) -> None:
        """Popola season.episodes con oggetti Episode."""
        ...

    # Richiesto dal layer GUI
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

Usa gli oggetti dati da `VibraVid.services._base.object`:

```python
from VibraVid.services._base.object import SeasonManager, Season, Episode

# Aggiungi una stagione
self.seasons_manager.add(Season(id="s1", number=1, name="Stagione 1"))

# Aggiungi un episodio a una stagione
season = self.seasons_manager.get_season_by_number(1)
season.episodes.add(Episode(id="ep1", number=1, name="Pilota", url="..."))
```

---

### 4. Scrivi `downloader.py` — Logica di Download

Questo file implementa il download effettivo di film ed episodi di serie.

#### `download_film(select_title: Entries) -> Tuple[str, bool]`

```python
def download_film(select_title: Entries):
    start_message()
    # Risolvi l'URL di streaming dal sito
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

Usa gli helper base `process_season_selection` e `process_episode_download` per gestire automaticamente tutta la logica di selezione:

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

### 5. (Opzionale) Scrivi `client.py` — Autenticazione

Aggiungi questo file solo se il servizio richiede login, token o richieste firmate.

```python
_cached_token = None

def get_bearer_token() -> str:
    global _cached_token
    if _cached_token:
        return _cached_token
    # Esegui il login e metti il token in cache
    response = create_client(headers=get_headers()).post(LOGIN_URL, json={...})
    _cached_token = response.json()["access_token"]
    return _cached_token
```

Poi importalo e usalo in `__init__.py` e `downloader.py` dove necessario.

---

## Template Minimo

Un punto di partenza minimale che puoi copiare e adattare:

```
VibraVid/services/mio_nuovo_sito/
├── __init__.py     ← copia da un servizio esistente e sostituisci le chiamate API
├── scrapper.py     ← implementa GetSerieInfo con collect_info_title/season
├── downloader.py   ← implementa download_film, download_episode, download_series
└── client.py       ← solo se è necessaria l'autenticazione
```