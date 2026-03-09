# Architecture

**Overall:** Plugin-based media downloader with service abstraction layer

## Layers

**CLI/Entry Layer:**

- Purpose: Command-line interface and user interaction orchestration
- Location: `VibraVid/cli/run.py`
- Contains: Main entry point, argument parsing, hook execution, user prompts
- Depends on: Service loader, configuration manager, update system
- Used by: Direct Python execution and PyPI package installation

**Service Layer (Plugin Architecture):**

- Purpose: Provides abstraction for multiple streaming providers
- Location: `VibraVid/services/`
- Contains: 18 service plugins (streamingcommunity, mediasetinfinity, crunchyroll, animeworld, etc.)
- Depends on: Base service abstractions, HTTP client, configuration
- Used by: CLI layer for search and download coordination

**Service Base Abstractions:**

- Purpose: Common interfaces and utilities for all services
- Location: `VibraVid/services/_base/`
- Contains:
    - `object.py`: Domain models (Episode, Season, Media, MediaItem, MediaManager)
    - `site_loader.py`: Lazy module loading with LazySearchModule
    - `site_search_manager.py`: Unified search processing
    - `tv_display_manager.py`: Console UI for media selection
    - `tv_download_manager.py`: Download orchestration
    - `site_costant.py`: Site-specific configuration via stack inspection

**Core Download Pipeline:**

- Purpose: Orchestrates media capture, decryption, and post-processing
- Location: `VibraVid/core/`
- Contains four subsystems:
    - **Downloader**: `core/downloader/` - Format-specific downloaders (HLS, MP4, DASH)
    - **DRM**: `core/drm/` - Content decryption (Widevine, PlayReady)
    - **Parser**: `core/parser/` - MPD manifest parsing for DASH streams
    - **Processors**: `core/processors/` - Post-processing (merge, capture, convert)
    - **Queue**: `core/queue/` - Parallel download queue (`DownloadManager` with `ThreadPoolExecutor`, `DownloadJob` dataclass, `DownloadDisplay` live monitor)

**Utility & Support Layers:**

- Purpose: Cross-cutting utilities and configuration
- Location: `VibraVid/utils/` and `VibraVid/source/`
- Contains:
    - Config manager with caching and remote fetch
    - HTTP client wrapper with session management
    - OS-level utilities and internet connectivity checking
    - M3U8 parser and track selector
    - Console display utilities
    - TMDB integration for media metadata

**Setup & Device Configuration:**

- Purpose: System checks and DRM device installation
- Location: `VibraVid/setup/`
- Contains: Binary path detection, device installer, system checks

**Player Abstraction:**

- Purpose: Support for multiple video player CDNs
- Location: `VibraVid/player/`
- Contains: Video player implementations (VixCloud, SuperVideo, SweetPixel, MediapolisVOD)

## Data Flow

**Search Flow:**

1. User enters search query via CLI (`cli/run.py` → `get_user_site_selection()`)
2. Service selection (anime, Film_Serie, serie)
3. Lazy-load selected service via `LazySearchModule`
4. Call service's `search(query)` function:
    - Scraper fetches and parses web results (`services/{site}/scrapper.py`)
    - Results wrapped in `MediaItem` objects
    - `MediaManager` accumulates results
5. `TVShowManager` displays results in console table
6. Metadata cached in `MediaManager` for download phase

**Download Flow:**

1. Selected `MediaItem` passed to service's download function
2. If `concurrent_download` is enabled, the download is wrapped in a `DownloadJob` and enqueued via `DownloadManager`:
    - Films: enqueued directly by `site_search_manager.py`
    - Series episodes: each episode enqueued individually by `tv_download_manager.py`
    - `DownloadManager` runs up to `max_concurrent_jobs` workers (default 4) via `ThreadPoolExecutor`
    - `DownloadDisplay` shows a live Rich table with queued, running, completed, and failed jobs
3. Service downloader extracts stream metadata (URL, headers, manifest)
4. Stream type detection (HLS, DASH, MP4):
    - HLS: M3U8 URL → `HLS_Downloader`
    - DASH: MPD URL → `DASH_Downloader` + `DRMManager`
    - MP4: Direct URL → `MP4_Downloader`
5. DRM decryption if needed:
    - PSSH extraction → Widevine CDM or PlayReady manager
    - Database lookup for cached keys → CDM extraction → decryption
6. Download segments/chunks with resume capability
7. Post-processing pipeline:
    - `capture.py`: Subtitle/audio/video extraction
    - `merge.py`: Combine video + audio + subtitles
    - `helper/`: Format conversion (video, audio, subtitle codecs)
    - `nfo.py`: Metadata NFO file generation
8. Output saved to configured folder structure

**Thread Context Management:**

- `context_tracker` (`ContextTracker` via `threading.local()`) controls per-thread console output suppression
- `download_tracker` tracks download state and cancellation per thread
- Rich `Console.print`/`Console.log` are monkey-patched in `core/queue/queue.py` to check `context_tracker.should_print`

**State Management:**

- Transient: CLI session state (MediaManager, user selections) - cleared per search
- Persistent: Configuration (config.json, domains.json, login.json)
- Cache: Database keys (local SQLite or Supabase vault)
- Remote: Domains fetched from GitHub on startup

## Key Abstractions

**MediaManager/EpisodeManager/SeasonManager:**

- Purpose: Hold media metadata during search/selection phase
- Pattern: Collection managers that accumulate items from search
- Examples: `services/_base/object.py`

**LazySearchModule:**

- Purpose: Defer service module loading until needed
- Pattern: Proxy pattern with lazy initialization
- Benefits: Reduced startup time, on-demand loading of service plugins
- Location: `services/_base/site_loader.py`

**Service Plugin Interface:**

- Purpose: Standardized contract for all services
- Pattern: Each service must export `search(query, options...)` and `_useFor` category
- Examples: All services implement `services/{site}/__init__.py`

**DRMManager:**

- Purpose: Unified DRM key extraction and caching
- Pattern: Delegation to Widevine/PlayReady specific handlers with database fallback
- Location: `core/drm/manager.py`

**Download Orchestrator:**

- Purpose: Route stream to appropriate downloader
- Pattern: Factory pattern based on stream type detection
- Examples: `core/downloader/` contains format-specific implementations

## Entry Points

**CLI Entry Point:**

- Location: `VibraVid/__main__.py` → `cli/run.py::main()`
- Triggers: `python -m VibraVid` or `VibraVid` (PyPI)
- Responsibilities:
    1. Initialize system (version check, device info, updates)
    2. Load all available services via lazy loader
    3. Parse CLI arguments
    4. Execute pre_run hooks
    5. Route user input to service search or global search
    6. Execute post_run hooks

**Service Entry Point (Plugin Pattern):**

- Location: `services/{site}/__init__.py::search(query, ...)`
- Triggers: Called by CLI when user selects site
- Responsibilities:
    1. Scrape search results
    2. Present options to user
    3. Invoke downloader for selected media

**Hook System Entry Points:**

- Location: `cli/run.py::execute_hooks(stage)`
- Stages: pre_run (before init), post_run (after app complete)
- Execution: Subprocess-based with OS detection, timeout support
