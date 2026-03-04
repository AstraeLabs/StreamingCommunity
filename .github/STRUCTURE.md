# Codebase Structure

## Directory Layout

```
VibraVid/
├── VibraVid/          # Main package root
│   ├── __init__.py              # Package marker
│   ├── __main__.py              # CLI entry point
│   ├── cli/                     # Command-line interface
│   │   ├── __init__.py
│   │   ├── run.py               # Main CLI orchestrator
│   │   └── command/
│   │       └── global_search.py # Cross-service search
│   ├── core/                    # Core download & processing pipeline
│   │   ├── downloader/          # Format-specific downloaders
│   │   │   ├── __init__.py
│   │   │   ├── hls.py           # HTTP Live Streaming (M3U8)
│   │   │   ├── dash.py          # MPEG-DASH with DRM
│   │   │   ├── ism.py           # ISM Smooth Streaming
│   │   │   ├── mp4.py           # Direct MP4 download
│   │   │   └── mega.py          # MEGA.nz downloads
│   │   ├── drm/                 # Digital Rights Management
│   │   │   ├── __init__.py
│   │   │   ├── manager.py       # DRM orchestrator
│   │   │   ├── widevine.py      # Widevine CDM handling
│   │   │   └── playready.py     # PlayReady handling
│   │   ├── parser/              # Manifest parsing
│   │   │   ├── __init__.py
│   │   │   ├── mpd.py           # MPD manifest parser
│   │   │   └── ism.py           # ISM manifest parser
│   │   ├── processors/          # Post-download processing
│   │   │   ├── __init__.py
│   │   │   ├── capture.py       # Subtitle/audio/video capture
│   │   │   ├── merge.py         # Combine media streams
│   │   │   └── helper/          # Format conversion helpers
│   │   │       ├── __init__.py
│   │   │       ├── nfo.py       # Metadata NFO generation
│   │   │       ├── ex_video.py  # Video extraction
│   │   │       ├── ex_audio.py  # Audio extraction
│   │   │       └── ex_sub.py    # Subtitle extraction
│   │   └── queue/               # Parallel download queue
│   │       ├── __init__.py
│   │       ├── queue.py         # DownloadManager, DownloadJob, monkey-patched Console
│   │       └── display.py       # Live Rich table monitor (DownloadDisplay)
│   ├── services/                # Plugin services (streaming providers)
│   │   ├── __init__.py
│   │   ├── _base/               # Shared service abstractions
│   │   │   ├── __init__.py
│   │   │   ├── object.py        # Domain models (Episode, Season, Media)
│   │   │   ├── site_loader.py   # Lazy module loading
│   │   │   ├── site_costant.py  # Site-specific constants
│   │   │   ├── site_search_manager.py    # Search result processing
│   │   │   ├── tv_display_manager.py     # Console UI
│   │   │   └── tv_download_manager.py    # Download orchestration
│   │   └── {site}/              # Individual service plugins
│   │       ├── streamingcommunity/
│   │       ├── crunchyroll/
│   │       ├── mediasetinfinity/
│   │      ...
│   │       ├── realtime/
│   │       └── Each service contains:
│   │           ├── __init__.py   # search() and _useFor definition
│   │           ├── scrapper.py   # Web scraping logic
│   │           ├── downloader.py # Format-specific download
│   │           └── client.py     # (Optional) Auth/API client
│   ├── player/                  # Video player abstractions
│   │   ├── __init__.py
│   │   ├── vixcloud.py
│   │   ├── supervideo.py
│   │   ├── sweetpixel.py
│   │   └── mediapolisvod.py
│   ├── source/                  # Media download & utility sources
│   │   ├── __init__.py
│   │   ├── N_m3u8/              # M3U8 parser & downloader
│   │   │   ├── __init__.py
│   │   │   ├── parser.py        # M3U8 parsing
│   │   │   ├── pattern.py       # Segment patterns
│   │   │   ├── progress_bar.py  # Download UI
│   │   │   ├── trackSelector.py # Track selection
│   │   │   ├── ui.py            # UI helpers
│   │   │   └── wrapper.py       # Download wrapper
│   │   └── utils/               # Utility functions
│   │       ├── __init__.py
│   │       ├── media_players.py # Player executable detection
│   │       ├── object.py        # Shared objects
│   │       ├── tracker.py       # Download progress tracking
│   │       └── trans_codec.py   # Codec transformation
│   ├── setup/                   # System configuration & device setup
│   │   ├── __init__.py
│   │   ├── binary_paths.py      # Detect system binaries
│   │   ├── checker.py           # Pre-flight checks
│   │   ├── device_install.py    # DRM device installation
│   │   └── system.py            # System utilities
│   ├── upload/                  # Update and versioning
│   │   ├── __init__.py
│   │   ├── update.py            # Update logic
│   │   └── version.py           # Version management
│   └── utils/                   # Cross-cutting utilities
│       ├── __init__.py
│       ├── config.py            # ConfigManager, ConfigAccessor
│       ├── http_client.py       # HTTP session management
│       ├── os.py                # OS utilities, OsManager
│       ├── tmdb_client.py       # TMDB metadata integration
│       ├── console/             # Console UI utilities
│       │   ├── __init__.py
│       │   ├── message.py       # Message formatting
│       │   └── table.py         # Table display (TVShowManager)
│       └── vault/               # Credential & key storage
│           ├── __init__.py
│           ├── local_db.py      # Local SQLite vault
│           └── external_supa_db.py # Supabase vault
├── Conf/                        # Configuration files (not in package)
│   ├── config.json              # Default configuration
│   ├── login.json               # Service credentials
│   └── remote_cdm.json          # Remote CDM configuration
├── GUI/                         # Alternative GUI interface (separate module)
│   └── ...
├── Test/                        # Test utilities
│   ├── Downloads/               # Example download scripts
│   │   ├── HLS.py
│   │   ├── MP4.py
│   │   ├── DASH.py
│   │   ├── ISM.py
│   │   └── MEGA.py
│   └── Util/
│       └── hooks.py
├── setup.py                     # Package installation config
├── requirements.txt             # Python dependencies
├── manual.py                  # Entry point for manual testing
├── update.py                    # Update script
├── dockerfile                   # Docker configuration
├── .github/                     # GitHub workflows & docs
└── .gitignore
```

## Directory Purposes

**VibraVid/**

- Purpose: Main package containing all production code
- Contains: CLI, services, core pipeline, utilities

**cli/**

- Purpose: Command-line interface orchestration
- Contains: Main entry, argument parsing, user interaction
- Key files: `run.py` (348+ lines, main orchestrator)

**core/downloader/**

- Purpose: Format-specific download implementations
- Contains: HLS, DASH, ISM, MP4, MEGA downloaders
- Key files: `hls.py`, `dash.py` implement download logic

**core/drm/**

- Purpose: DRM content decryption
- Contains: Widevine and PlayReady CDM management
- Key files: `manager.py` orchestrates key extraction

**core/processors/**

- Purpose: Post-download media processing
- Contains: Merge, capture, conversion helpers
- Key files: `merge.py` combines video/audio/subtitles

**services/:**

- Purpose: Streaming provider plugins
- Contains: 18 service implementations, each with search + download
- Key pattern: Each service is independent, lazily loaded

**services/\_base/**

- Purpose: Shared abstractions for all services
- Contains: Domain models, search processing, UI management
- Key files: `object.py` (models), `site_loader.py` (lazy loading)

**source/N_m3u8/**

- Purpose: M3U8 playlist parsing and HLS segment downloading
- Contains: Parser, track selector, download wrapper
- Key files: `parser.py`, `wrapper.py`

**utils/**

- Purpose: Cross-cutting utilities
- Contains: Config management, HTTP client, OS utilities
- Key files: `config.py` (ConfigManager), `os.py` (OsManager)

**setup/**

- Purpose: System checks and DRM device installation
- Contains: Binary detection, device setup, system checks

**upload/**

- Purpose: Version management and update logic
- Contains: Update coordination, version tracking

## Key File Locations

**Entry Points:**

- `VibraVid/__main__.py`: Package entry point (calls cli.run.main)
- `VibraVid/cli/run.py`: Main CLI orchestrator (main function at line 348)

**Configuration:**

- `Conf/config.json`: Default settings and feature flags
- `Conf/login.json`: Service credentials
- `Conf/remote_cdm.json`: DRM CDM configuration
- `VibraVid/utils/config.py`: ConfigManager (loads and caches config)

**Core Logic:**

- `VibraVid/core/downloader/hls.py`: HLS download orchestrator
- `VibraVid/core/downloader/dash.py`: DASH download with DRM
- `VibraVid/core/drm/manager.py`: DRM key extraction
- `VibraVid/services/_base/object.py`: Domain models
- `VibraVid/services/_base/site_loader.py`: Service loading

**Testing:**

- `Test/Downloads/*.py`: Example download scenarios
- `manual.py`: Entry point for manual runs

## Naming Conventions

**Files:**

- `*.py`: Python source files
- `{site}/__init__.py`: Service plugin definition (required search function)
- `{site}/scrapper.py`: Web scraping logic
- `{site}/downloader.py`: Format-specific download
- `{site}/client.py`: Authentication/API client (optional)

**Directories:**

- `services/{site}/`: Service provider name (lowercase, no spaces)
- `core/{subsystem}/`: Functional domain (downloader, drm, parser, processors)
- `utils/{feature}/`: Grouped utilities (console, vault)

**Classes:**

- `*Manager`: Manages collections or coordinates behavior (EpisodeManager, DRMManager)
- `*Downloader`: Format-specific downloaders (HLS_Downloader, DASH_Downloader)
- `*Accessor`: Config accessors with caching
- Domain models: MediaItem, Media, Season, Episode (no suffix)

**Functions:**

- `download_*`: Download orchestration functions
- `get_*`: Retrieval functions
- `search()`: Service search entry point (standardized across all services)
- Snake_case for all functions

## Where to Add New Code

**New Service Plugin:**

- Primary code: `VibraVid/services/{new_site}/`
- Files needed:
    - `__init__.py`: Define `search(query, ...)` and `_useFor = "..."` (required)
    - `scrapper.py`: Web scraping logic
    - `downloader.py`: Format-specific download
    - `client.py`: Optional auth/API integration
- Entry point: Automatic discovery via `site_loader.LazySearchModule`

**New Downloader Format:**

- Implementation: `VibraVid/core/downloader/{format}.py`
- Pattern: Inherit download interface pattern from HLS_Downloader
- Export: Add to imports in `VibraVid/core/downloader/__init__.py`

**New Processor/Helper:**

- Implementation: `VibraVid/core/processors/` or `VibraVid/core/processors/helper/`
- Pattern: Follow merge.py or capture.py patterns
- Export: Add to `VibraVid/core/processors/__init__.py`

**New Utilities:**

- Shared helpers: `VibraVid/utils/{feature}/` (create subdir if needed)
- Console UI: `VibraVid/utils/console/`
- Vault/storage: `VibraVid/utils/vault/`

**Configuration:**

- Settings: Edit `Conf/config.json` and reload via ConfigManager
- Credentials: `Conf/login.json`
- Service domains: Fetched remotely from `AstraeLabs/Domains` repo
