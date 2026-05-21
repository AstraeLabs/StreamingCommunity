<div align="center">

[![PyPI Version](https://img.shields.io/pypi/v/vibravid?logo=pypi&logoColor=white&labelColor=2d3748&color=3182ce&style=for-the-badge)](https://pypi.org/project/vibravid/)
[![Sponsor](https://img.shields.io/badge/💖_Sponsor-ea4aaa?style=for-the-badge&logo=github-sponsors&logoColor=white&labelColor=2d3748)](https://ko-fi.com/arrowar)

[![Windows](https://img.shields.io/badge/🪟_Windows-0078D4?style=for-the-badge&logo=windows&logoColor=white&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_win_2025_x64.exe)
[![macOS](https://img.shields.io/badge/🍎_macOS-000000?style=for-the-badge&logo=apple&logoColor=white&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_mac_15_x64)
[![Linux](https://img.shields.io/badge/🐧_Linux_latest-FCC624?style=for-the-badge&logo=linux&logoColor=black&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_linux_24_04_x64)

_⚡ **Quick Start:** `pip install VibraVid && VibraVid`_

**🌍 Language / Lingua**

[🇬🇧 English](README.md) | [🇮🇹 Italiano](./.github/docs/it/README.md)

</div>

---

## 📖 Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Login](.github/docs/en/login.md)
- [Downloaders](#downloaders)
- [Configuration](#configuration)
- [Usage Examples](#usage-examples)
- [Global Search](#global-search)
- [Advanced Features](#advanced-features)
- [ARR Integration](#arr-integration)
- [Docker](#docker)
- [Gui](./.github/docs/en/gui.md)
- [Known Issues](#known-issues)
- [Related Projects](#related-projects)

---

## Installation

### Option 1 — PyPI (recommended)

```bash
pip install VibraVid
VibraVid
```

### Option 2 — uv

```bash
uv tool install VibraVid
VibraVid
```

### Option 3 — Manual Clone

```bash
git clone https://github.com/AstraeLabs/VibraVid.git
cd VibraVid
```

Then install and run with either **pip** or **uv**:

**pip:**
```bash
pip install -r requirements.txt   # install
python manual.py                  # run
python update.py                  # update
pip install -r requirements.txt --upgrade  # sync deps
```

**uv:**
```bash
uv sync              # install
uv run manual.py     # run
uv run update.py     # update
uv sync --upgrade    # sync deps
```

### Option 4 — Unraid

```
You can find the app in the Community Application
```

### Additional Documentation

- 📝 [Login Guide](.github/doc/login.md) — Authentication for supported services

---

## Quick Start

```bash
# PyPI or uv install
VibraVid

# Manual clone
python manual.py
```

---

## Downloaders

| Type     | Description                  | Example                                  |
| -------- | ---------------------------- | ---------------------------------------- |
| **HLS**  | HTTP Live Streaming (m3u8)   | [View example](./Test/Downloads/HLS.py)  |
| **MP4**  | Direct MP4 download          | [View example](./Test/Downloads/MP4.py)  |
| **DASH** | MPEG-DASH with DRM bypass\*  | [View example](./Test/Downloads/DASH.py) |
| **ISM** | Smooth Streaming with DRM bypass\*  | [View example](./Test/Downloads/ISM.py) |

> **\*DASH with DRM bypass:** Requires a valid L3\L2\L1\SL3000\SL2000 CDM (Content Decryption Module). This project does not provide or facilitate obtaining CDMs. Users must ensure compliance with applicable laws.

---

## Configuration

All settings live in `config.json`. The sections below cover each configuration block.

### DEFAULT

```json
{
  "DEFAULT": {
    "debug_track_json": false,
    "log_level": "INFO",
    "close_console": true,
    "show_message": false,
    "fetch_domain_online": true,
    "auto_update_check": true,
    "imp_service": ["default"],
    "installation": "essential"
  }
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `close_console` | `true` | Automatically close the console after download completes |
| `debug_track_json` | `false` | Log a `TRACKS_JSON` payload with selected tracks, keys, and manifest metadata — useful for debugging stream selection |
| `log_level` | `"INFO"` | Logging verbosity. Accepts standard Python values: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `show_message` | `false` | Show the startup banner and clear the console before printing it |
| `fetch_domain_online` | `true` | Automatically fetch the latest domains from GitHub |
| `auto_update_check` | `true` | Notify you at startup when a new VibraVid version is available |
| `imp_service` | `["default"]` | Service source paths to load site modules from. `"default"` loads all built-in sites. Add absolute paths to directories containing custom site modules — each must have `__init__.py` defining `indice` and `_useFor`. Custom modules take precedence over built-ins with the same name. |
| `installation` | `"essential"` | Controls which bundled binaries are auto-downloaded at setup: `none` skips all, `essential` downloads Bento4, FFmpeg, and Velora, `full` also adds Dovi Tool and MKVToolNix |

**Custom `imp_service` example:**
```json
"imp_service": ["default", "/home/user/my_custom_sites"]
```

---

### OUTPUT

```json
{
  "OUTPUT": {
    "root_path": "Video",
    "movie_folder_name": "Movie",
    "serie_folder_name": "Serie",
    "anime_folder_name": "Anime",
    "movie_format": "%(title_name) (%(title_year))/%(title_name) (%(title_year))",
    "episode_format": "%(series_name)/S%(season:02d)/%(episode_name) S%(season:02d)E%(episode:02d)"
  }
}
```

**`root_path`** — Base directory where videos are saved.
- Windows: `C:\\MyLibrary\\Folder` or `\\\\MyServer\\Share`
- Linux/macOS: `Desktop/MyLibrary/Folder`
- Docker / NAS: set the `VIBRAVID_OUTPUT_ROOT` environment variable instead of editing `config.json` — the value is applied at startup and overrides this field without touching the persisted config file. Example: `VIBRAVID_OUTPUT_ROOT=/app/Video` (container path matching the bind-mount target).

**`movie_folder_name`**, **`serie_folder_name`**, **`anime_folder_name`** — Subfolder names for each content type (defaults: `"Movie"`, `"Serie"`, `"Anime"`). All support the `%{site_name}` placeholder:

```
"Movie/%{site_name}"  →  "Movie/Crunchyroll"
"Serie/%{site_name}"  →  "Serie/Crunchyroll"
```

---

#### Movie Format

**Default:** `"%(title_name) (%(title_year))/%(title_name) (%(title_year))"`

```
%(title_name) (%(title_year))/   →  folder    Inception (2010)/
%(title_name) (%(title_year))    →  filename  Inception (2010).mkv
```

| Variable | Description |
|----------|-------------|
| `%(title_name)` | Movie title |
| `%(title_name_slug)` | Movie title as slug |
| `%(title_year)` | Release year (omitted if unavailable) |
| `%(quality)` | Video resolution |
| `%(language)` | Audio languages |
| `%(video_codec)` | Video codec |
| `%(audio_codec)` | Audio codec |

---

#### Episode Format

**Default:** `"%(series_name)/S%(season:02d)/%(episode_name) S%(season:02d)E%(episode:02d)"`

```
%(series_name)/     →  series folder   Breaking Bad/
S%(season:02d)/     →  season folder   S01/
%(episode_name)...  →  filename        Pilot S01E05.mkv
```

| Variable | Description |
|----------|-------------|
| `%(series_name)` | Series name |
| `%(series_name_slug)` | Series name as slug |
| `%(series_year)` | Series release year |
| `%(season:FORMAT)` | Season number with inline padding (see below) |
| `%(episode:FORMAT)` | Episode number with inline padding (see below) |
| `%(episode_name)` | Episode title (sanitized) |
| `%(episode_name_slug)` | Episode title as slug |
| `%(quality)` | Video resolution |
| `%(language)` | Audio languages |
| `%(video_codec)` | Video codec |
| `%(audio_codec)` | Audio codec |

**Inline padding syntax (for `season` and `episode`):**

| Token | Result (n=1) | Description |
|-------|-------------|-------------|
| `%(season:02d)` | `01` | Zero-pad to 2 digits |
| `%(season:03d)` | `001` | Zero-pad to 3 digits |
| `%(season:d)` | `1` | No padding |

---

### DOWNLOAD

```json
{
  "DOWNLOAD": {
    "auto_select": true,
    "delay_after_download": 1,
    "skip_download": false,
    "thread_count": 12,
    "concurrent_download": true,
    "select_video": "1920",
    "select_audio": "ita|Ita",
    "select_subtitle": "ita|eng|Ita|Eng",
    "cleanup_tmp_folder": true
  }
}
```

#### Performance Settings

| Key | Default | Description |
|-----|---------|-------------|
| `auto_select` | `true` | Automatically select streams based on filters. When `false`, enables interactive track selection before download |
| `delay_after_download` | `1` | Delay (seconds) applied after each movie or episode download |
| `skip_download` | `false` | Skip the download step and process existing files |
| `thread_count` | `12` | Number of concurrent segment requests for a single stream |
| `concurrent_download` | `true` | Download video, audio, and subtitles simultaneously |
| `cleanup_tmp_folder` | `true` | Remove temporary files after download |

#### Stream Selection Filters

Use `select_video`, `select_audio`, and `select_subtitle` to control which tracks are downloaded.

**Video (`select_video`):**

| Value | Description |
|-------|-------------|
| `"best"` | Best available resolution |
| `"worst"` | Worst available resolution |
| `"1080"` | Exact height (falls back to worst if not found) |
| `"1080,H265"` | Height + codec constraint |
| `"1080\|best"` | Height with fallback to best |
| `"1080\|best,H265"` | Height + codec with fallback to best |
| `"false"` | Skip video |

**Audio (`select_audio`):**

| Value | Description | If not found |
|-------|-------------|--------------|
| `"best"` | Best bitrate per language | Selects best across all |
| `"worst"` | Worst bitrate per language | Selects worst across all |
| `"all"` | All audio tracks | Downloads all |
| `"default"` | Streams marked as default | DROP |
| `"non-default"` | Streams NOT marked as default | DROP |
| `"ita"` | Italian audio | DROP |
| `"ita\|it"` | Pipe-separated language codes | DROP if none found |
| `"ita,MP4A"` | Language + codec | DROP if combination not found |
| `"ita\|best"` | Language with fallback to best | Fallback to best |
| `"ita\|best,AAC"` | Language + codec with fallback | Fallback to best |
| `"false"` | Skip audio | — |

**Subtitle (`select_subtitle`):**

| Value | Description |
|-------|-------------|
| `"all"` | All subtitles |
| `"default"` | Streams marked as default |
| `"non-default"` | Streams NOT marked as default |
| `"ita\|eng"` | Pipe-separated language codes |
| `"ita_forced"` | Language with flag (`forced`, `cc`, `sdh`) |
| `"ita_forced\|eng_cc"` | Multiple languages with flags |
| `"false"` | Skip subtitles |

---

### PROCESS (Post-Processing)

```json
{
  "PROCESS": {
    "use_gpu": false,
    "param_video": ["-c:v", "libx265", "-crf", "28", "-preset", "medium"],
    "param_audio": ["-c:a", "libopus", "-b:a", "128k"],
    "param_final": ["-c", "copy"],
    "audio_order": ["ita", "eng"],
    "subtitle_order": ["ita", "eng"],
    "merge_audio": true,
    "merge_subtitle": true,
    "subtitle_disposition_language": "ita_forced",
    "extension": "mkv"
  }
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `use_gpu` | `false` | Enable hardware acceleration. GPU type is auto-detected at runtime: `cuda` (NVIDIA), `qsv` (Intel), `vaapi` (AMD) |
| `param_video` | H.265/HEVC | FFmpeg video encoding parameters, e.g. `["-c:v", "libx265", "-crf", "28", "-preset", "medium"]` |
| `param_audio` | Opus 128k | FFmpeg audio encoding parameters, e.g. `["-c:a", "libopus", "-b:a", "128k"]` |
| `param_final` | `["-c", "copy"]` | Final FFmpeg parameters. When set, takes full precedence over `param_video` and `param_audio` |
| `audio_order` | — | Order of audio tracks in the output, e.g. `["ita", "eng"]` |
| `subtitle_order` | — | Order of subtitle tracks in the output, e.g. `["ita", "eng"]` |
| `merge_audio` | `true` | Merge all audio tracks into a single output file |
| `merge_subtitle` | `true` | Merge all subtitle tracks into a single output file |
| `subtitle_disposition_language` | — | Mark a specific subtitle track as default/forced |
| `extension` | `"mkv"` | Output container format: `"mkv"` or `"mp4"` |

**`force_subtitle`** — Controls how subtitles are handled before remuxing:

| Value | Behaviour |
|-------|-----------|
| `"auto"` (default) | Subtitles are renamed/converted based on their detected format. VTT files are sanitized (unmatched `<` replaced) to prevent data loss when muxed as SRT |
| `"copy"` | No conversion or renaming — the original file is muxed as-is. Also skips VTT sanitization |
| `"srt"` / `"vtt"` / `"ass"` | Force-convert all subtitles to the specified format using FFmpeg, with sanitization applied for `vtt` |

See `VibraVid/core/processors/helper/ex_sub.py` for conversion logic.

---

### REQUESTS

```json
{
  "REQUESTS": {
    "timeout": 30,
    "max_retry": 10,
    "use_proxy": false,
    "proxy": {
      "http": "http://localhost:8888",
      "https": "http://localhost:8888"
    }
  }
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `timeout` | `30` | Request timeout in seconds |
| `max_retry` | `10` | Maximum retry attempts for failed requests |
| `use_proxy` | `false` | Enable proxy support for HTTP requests |
| `proxy.http` | — | HTTP proxy URL |
| `proxy.https` | — | HTTPS proxy URL |

---

### ARR

The `ARR` block enables VibraVid to work as an automation layer between **Seerr/Jellyseerr**, **Sonarr**, **Radarr**, and the final media library. When enabled, VibraVid polls Sonarr/Radarr for missing media, receives webhook events, downloads through its provider pipeline, and reports the resulting files back so that Sonarr/Radarr can import them.

> **The ARR integration requires the VibraVid web GUI to be running.** All polling loops, webhook listeners, and download workers are managed by the Django application server. The CLI (`vibraNid` / `python -m VibraVid`) does not start the ARR stack.

#### Configuration reference

```json
{
  "ARR": {
    "enabled": false,
    "enable_polling": false,
    "enable_seerr_webhook": false,
    "enable_sonarr_webhook": false,
    "enable_radarr_webhook": false,
    "polling_interval": 300,
    "full_resync_interval": 21600,
    "max_concurrent_downloads": 1,
    "webhook_priority_enabled": true,
    "native_webhook_priority_window_seconds": 120,
    "seerr_fallback_delay_seconds": 20,
    "download_italian_anime_default": true,
    "provider_fallback": ["streamingcommunity", "animeunity"],
    "path_mapping": {},
    "sonarr": { "url": "", "api_key": "" },
    "radarr": { "url": "", "api_key": "" },
    "seerr": { "webhook_secret": "" },
    "sonarr_webhook": { "webhook_secret": "" },
    "radarr_webhook": { "webhook_secret": "" }
  }
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `false` | Enables the ARR integration globally |
| `enable_polling` | `false` | Periodically scans Sonarr/Radarr for missing media |
| `enable_seerr_webhook` | `false` | Enables the Seerr/Jellyseerr webhook endpoint |
| `enable_sonarr_webhook` | `false` | Enables the Sonarr native webhook endpoint |
| `enable_radarr_webhook` | `false` | Enables the Radarr native webhook endpoint |
| `polling_interval` | `300` | Seconds between incremental polling cycles |
| `full_resync_interval` | `21600` | Seconds between full reconciliation syncs |
| `max_concurrent_downloads` | `1` | Maximum parallel ARR-triggered downloads |
| `webhook_priority_enabled` | `true` | Native Sonarr/Radarr webhooks take priority over Seerr to avoid duplicates |
| `native_webhook_priority_window_seconds` | `120` | Dedup window for near-simultaneous webhook events |
| `seerr_fallback_delay_seconds` | `20` | Delay before processing a Seerr event when a native webhook may follow |
| `download_italian_anime_default` | `true` | When an anime provider returns both an original and an `(ITA)` dubbed version, prefer the Italian dub |
| `provider_fallback` | `[]` | Ordered list of providers tried in sequence when the primary provider finds no match. If empty, `streamingcommunity` is the built-in default |
| `path_mapping` | `{}` | Translates VibraVid host paths to the paths seen by Radarr/Sonarr containers. Leave empty when both services share the same filesystem view |
| `sonarr.url` | — | Base URL of the Sonarr instance, e.g. `http://sonarr:8989` |
| `sonarr.api_key` | — | Sonarr API key |
| `radarr.url` | — | Base URL of the Radarr instance, e.g. `http://radarr:7878` |
| `radarr.api_key` | — | Radarr API key |
| `seerr.webhook_secret` | — | Secret expected in Seerr/Jellyseerr webhook requests |
| `sonarr_webhook.webhook_secret` | — | Secret expected in Sonarr webhook requests |
| `radarr_webhook.webhook_secret` | — | Secret expected in Radarr webhook requests |

> All ARR settings can also be edited directly from the VibraVid web GUI under **Settings → Configuration editor**, without touching `config.json` manually.

These keys can also be set via environment variables (useful in Docker):

```bash
USE_ARR_SERVICES=true
SONARR_URL=http://sonarr:8989
SONARR_API_KEY=your-key
RADARR_URL=http://radarr:7878
RADARR_API_KEY=your-key
SEERR_WEBHOOK_SECRET=your-secret
SONARR_WEBHOOK_SECRET=your-secret
RADARR_WEBHOOK_SECRET=your-secret
```

#### Webhook setup

VibraVid exposes one webhook endpoint per ARR application. Add **one connection only** per app — adding multiple webhooks for the same app causes duplicate processing.

**Radarr** → Settings → Connect → Webhook

| Field | Value |
|-------|-------|
| URL | `http://<vibravid-host>:<port>/api/arr/webhook/radarr/` |
| Triggers | On Movie Added, On Movie File Delete |
| Secret | any value mirrored in `radarr_webhook.webhook_secret` |

**Sonarr** → Settings → Connect → Webhook

| Field | Value |
|-------|-------|
| URL | `http://<vibravid-host>:<port>/api/arr/webhook/sonarr/` |
| Triggers | On Series Add, On Episode File Delete |
| Secret | any value mirrored in `sonarr_webhook.webhook_secret` |

Then enable in `config.json`:
```json
"enable_sonarr_webhook": true,
"enable_radarr_webhook": true
```

> Webhooks and polling can be used together: webhooks trigger an immediate download when media is added, polling acts as a safety net for items that arrive without a webhook event.

#### Provider selection

VibraVid determines which provider to use for each item through two mechanisms — you can use one or both.

**Method 1 — Per-item tag (advanced, requires tagging each title)**

Add a tag directly to the movie or series in Sonarr/Radarr using the format `provider-<site>`. VibraVid reads the tag at download time and uses that provider regardless of the fallback list. This is useful when specific titles are only available on a particular service.

Tags are created in Sonarr/Radarr under Settings → Tags, then assigned to individual series or movies from their edit page.

| Tag | Behaviour |
|-----|-----------|
| `provider-animeunity` | Uses AnimeUnity for that title |
| `provider-guardaserie` | Uses GuardaSerie for that title |
| `provider-<site>` | Uses any supported VibraVid site for that title |
| `hold` / `pausa` | Skips the item until the tag is removed |
| `skip-s1`, `skip-s2`, … | Skips a specific season of a series |

**Method 2 — Global fallback list (recommended, zero per-title configuration)**

Configure `provider_fallback` with an ordered list of providers. VibraVid tries them in sequence and stops at the first that finds a matching title. No tagging required — add as many providers as you want as safety nets.

Recommended full configuration (covers general content, anime, and niche services):

```json
"provider_fallback": [
    "streamingcommunity",
    "animeunity",
    "guardaserie",
    "discoveryplus",
    "discovery",
    "dmax",
    "nove",
    "realtime",
    "mediasetinfinity",
    "raiplay",
    "homegardentv",
    "foodnetwork",
    "animeworld",
    "crunchyroll",
    "primevideo",
    "tubitv",
    "cinezo",
    "mostraguarda"
]
```

If `provider_fallback` is empty or omitted, VibraVid tries `streamingcommunity` only and fails if the title is not found there.

**Italian dub preference (`download_italian_anime_default`)**

When `true`, if the selected provider returns both an original-language version and an `(ITA)` dubbed version of the same title, VibraVid automatically picks the Italian dub. This applies regardless of which method selected the provider.

```json
"download_italian_anime_default": true
```

#### Path mapping — essential for split environments

`path_mapping` is one of the most important settings when Radarr/Sonarr run in Docker while VibraVid runs on the host (or vice versa). After a download completes, VibraVid must tell Radarr/Sonarr exactly where the file is so they can import it. If the two services see the same physical folder under different paths, Radarr/Sonarr will receive a path they cannot resolve and the import will fail.

| Setup | `path_mapping` needed? |
|-------|----------------------|
| Both on bare metal | No |
| Both in Docker with identical volume mounts | No |
| VibraVid on host, ARR stack in Docker | **Yes** |
| Both in Docker with different volume mounts | **Yes** |

**Example:** VibraVid on the host sees `/media/Media/Film`. Radarr's Docker Compose mounts the same folder at a different path:

```yaml
volumes:
  - /media/Media/Film:/media/Film
  - /media/Media/Anime:/media/Anime
  - /media/Media/Series:/media/Series
```

Without `path_mapping`, VibraVid reports `/media/Media/Film/my-movie` to Radarr. Radarr looks for that path inside its container — it does not exist there — and the import fails. With the mapping configured, VibraVid automatically translates the path before every API call:

```json
"path_mapping": {
    "/media/Media/Film":   "/media/Film",
    "/media/Media/Anime":  "/media/Anime",
    "/media/Media/Series": "/media/Series"
}
```

Each key is a prefix as seen by VibraVid; the value is the equivalent prefix inside the Radarr/Sonarr container. Entries are checked in order and the first matching prefix is replaced. Leave `path_mapping` as `{}` when both services share the same filesystem view.

---

### DRM

```json
{
  "DRM": {
    "use_cdm": true,
    "prefer_remote_cdm": true,
    "vault": {
      "supa": {
        "url": "https://crqczuxpqjmrjvdvqvlx.supabase.co",
        "token": ""
      }
    }
  }
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `use_cdm` | `true` | Enable CDM-based key extraction. When `false`, only database lookups are attempted |
| `prefer_remote_cdm` | `true` | Prefer remote CDM services over local device files |
| `vault` | — | Optional external DRM key store, queried before CDM extraction |

#### Remote CDM Services

When remote CDM services are available, add one or both of the following blocks to `config.json`:

**Widevine:**
```json
"widevine": {
  "device_type": "ANDROID",
  "system_id": 22590,
  "security_level": 3,
  "host": "https://cdrm-project.com/remotecdm/widevine",
  "secret": "CDRM",
  "device_name": "public"
}
```

| Key | Description |
|-----|-------------|
| `device_type` | Device model: `"ANDROID"` or `"CHROME"` |
| `system_id` | Widevine system ID (default `22590` for Android) |
| `security_level` | Security level 1–3 (3 = L3) |
| `host` | Remote CDM server URL |
| `secret` | Authentication secret |
| `device_name` | Device identifier registered on the remote service |

**PlayReady:**
```json
"playready": {
  "device_name": "public",
  "security_level": 3000,
  "host": "https://cdrm-project.com/remotecdm/playready",
  "secret": "CDRM"
}
```

| Key | Description |
|-----|-------------|
| `device_name` | Device identifier registered on the remote service |
| `security_level` | Security level (e.g. `3000` for SL3000) |
| `host` | Remote CDM server URL |
| `secret` | Authentication secret |

#### Local CDM Devices

To use local CDM device files instead of remote services, place them in the project root:

- **Widevine:** `.wvd` file (from pywidevine)
- **PlayReady:** `.prd` file (from pyplayready)

Set `prefer_remote_cdm` to `false` and local devices will be picked up automatically.

---

## Usage Examples

### Basic Commands

```bash
# Show help and available sites
python manual.py -h

# Search and download
python manual.py --site streamingcommunity --search "interstellar"

# Auto-download the first result
python manual.py --site streamingcommunity --search "interstellar" --auto-first

# Use a site by its index number
python manual.py --site 0 --search "interstellar"
```

### Series Selection

Use `--season` and `--episode` to skip interactive prompts:

```bash
# Specific episode
python manual.py --site streamingcommunity --search "breaking bad" --auto-first --season 1 --episode 3

# Range of episodes
python manual.py --site streamingcommunity --search "breaking bad" --auto-first --season 1 --episode "1-5"

# All episodes of a season
python manual.py --site streamingcommunity --search "breaking bad" --auto-first --season 1 --episode "*"

# All episodes of all seasons
python manual.py --site streamingcommunity --search "breaking bad" --auto-first --season "*"

# Multiple seasons
python manual.py --site streamingcommunity --search "breaking bad" --auto-first --season "1-3"
```

### Year Filter

```bash
# Exact year
python manual.py --site streamingcommunity --search "dune" --year 2021

# Year range
python manual.py --site streamingcommunity --search "batman" --year "1990-2015"
```

### Stream Track Overrides

```bash
# Video resolution
python manual.py --site streamingcommunity --search "interstellar" -sv 1080

# Audio language
python manual.py --site streamingcommunity --search "interstellar" -sa "eng"

# Subtitles
python manual.py --site streamingcommunity --search "interstellar" -ss "eng"
```

### Console Behaviour Override

```bash
# Keep console open (loop mode)
python manual.py --close-console false

# Close console after download
python manual.py --site streamingcommunity --search "interstellar" --close-console true
```

### Proxy

```bash
python manual.py --site streamingcommunity --search "interstellar" --use_proxy
```

### Show Dependency Paths

```bash
python manual.py --dep
```

---

## Global Search

```bash
# Global search
python manual.py --global -s "cars"

# Filter by category
python manual.py --category 1    # Anime
python manual.py --category 2    # Movies & Series
python manual.py --category 3    # Series only
```

---

## Advanced Features

### Hook System

Execute custom scripts at specific points in the download lifecycle. Hooks are configured in `config.json` under the `HOOKS` key.

**Available stages:**
- `pre_run` — runs before the main flow starts
- `post_download` — runs after each individual download completes (in the GUI, once per item)
- `post_run` — runs once when the overall execution ends

```json
{
  "HOOKS": {
    "pre_run": [
      {
        "name": "prepare-env",
        "type": "python",
        "path": "scripts/prepare.py",
        "args": ["--clean"],
        "env": { "MY_FLAG": "1" },
        "cwd": "~",
        "os": ["linux", "darwin"],
        "timeout": 60,
        "enabled": true,
        "continue_on_error": true
      }
    ],
    "post_download": [
      {
        "name": "post-download-env",
        "type": "python",
        "path": "/app/script.py",
        "args": ["{download_path}"],
        "env": { "MY_FLAG": "1" },
        "cwd": "~",
        "os": ["linux"],
        "timeout": 60,
        "enabled": true,
        "continue_on_error": true
      }
    ],
    "post_run": [
      {
        "name": "notify",
        "type": "bash",
        "command": "echo 'Download completed'"
      }
    ]
  }
}
```

#### Hook Options

| Key | Description |
|-----|-------------|
| `name` | Descriptive label for the hook |
| `type` | Script type: `python`, `bash`, `sh`, `shell`, `bat`, `cmd` |
| `path` | Path to script file (alternative to `command`) |
| `command` | Inline command to execute (alternative to `path`). Note: `args` are ignored when using `command` |
| `args` | List of arguments passed to the script |
| `env` | Additional environment variables as key-value pairs |
| `cwd` | Working directory for execution (supports `~` and env vars) |
| `os` | Optional OS filter: `["windows"]`, `["darwin"]`, `["linux"]`, or any combination |
| `timeout` | Maximum execution time in seconds (hook fails if exceeded) |
| `enabled` | Enable or disable the hook without removing it |
| `continue_on_error` | If `false`, stops execution when the hook fails |

#### Hook Types

- **Python:** runs with the current Python interpreter
- **Bash / sh / shell:** executed via `/bin/bash -c` on macOS/Linux
- **Bat / cmd / shell:** executed via `cmd /c` on Windows
- **Inline commands:** use `command` instead of `path` for simple one-liners

#### Context Placeholders

| Placeholder | Description |
|-------------|-------------|
| `{download_path}` | Absolute path of the downloaded file |
| `{download_dir}` | Directory containing the downloaded file |
| `{download_filename}` | Filename of the downloaded file |
| `{download_id}` | Internal download identifier |
| `{download_title}` | Download title |
| `{download_site}` | Source site name |
| `{download_media_type}` | Media type |
| `{download_status}` | Final download status |
| `{download_error}` | Error message, if any |
| `{download_success}` | `1` on success, `0` on failure |
| `{stage}` | Current hook stage |

The same values are also exposed as environment variables with the `SC_` prefix (e.g. `SC_DOWNLOAD_PATH`, `SC_DOWNLOAD_SUCCESS`, `SC_HOOK_STAGE`).

---

### Source Code Update (`update.py`)

```bash
# Interactive update
python update.py

# Skip first confirmation prompt
python update.py -y

# Cancel automatically without prompting
python update.py -n

# Preview what would be deleted without deleting anything
python update.py --dry-run

# Combine: skip first prompt + dry run
python update.py -y --dry-run
```

The following are **always preserved** during an update: folders `Video`, `Conf`, `.git` and file `update.py`.

---

## ARR Integration

VibraVid can be used as an automation component in a media-server stack with **Seerr/Jellyseerr**, **Sonarr**, and **Radarr**.

Typical flow:

```text
Seerr / Jellyseerr
    ↓ user requests a movie or series
Sonarr / Radarr
    ↓ media is added and marked as missing
VibraVid ARR
    ↓ detects missing media through polling or webhooks
VibraVid downloader
    ↓ searches and downloads through the configured provider or through the provider selected by tag
Sonarr / Radarr
    ↓ rescans/imports the downloaded file
Media library
    ↓ Jellyfin/Plex can detect the final file
```

### Supported integrations

| Service | Role |
|---------|------|
| **Seerr/Jellyseerr** | Handles user requests and sends approval/pending webhook events |
| **Sonarr** | Manages TV series, missing episodes, episode metadata, rescans and imports |
| **Radarr** | Manages movies, missing movie metadata, rescans and imports |
| **VibraVid** | Searches, downloads and hands files back to Sonarr/Radarr |

### Webhook endpoints

When the GUI/Django server is running, the ARR integration exposes dedicated webhook endpoints:

```text
POST /api/arr/webhook/seerr/
POST /api/arr/webhook/sonarr/
POST /api/arr/webhook/radarr/
```

Each endpoint can be protected with its own webhook secret. Configure the same secret both in VibraVid and in the corresponding external service.

### Manual and automatic sync

ARR can process media in two ways:

1. **Polling** — VibraVid periodically asks Sonarr/Radarr for wanted or missing media.
2. **Webhooks** — VibraVid reacts immediately when Seerr, Sonarr or Radarr sends an event.

Both modes can be enabled together. Native Sonarr/Radarr webhooks can be prioritized over Seerr events to reduce duplicate processing.

### Sonarr workflow

For series, VibraVid ARR can:

- read missing episodes from Sonarr;
- resolve series, season and episode metadata;
- download the requested episode into the expected series path;
- trigger a Sonarr rescan/import;
- verify whether the episode was imported;
- optionally mark the episode as unmonitored after successful import.

### Radarr workflow

For movies, VibraVid ARR can:

- read missing movies from Radarr;
- resolve title, year and TMDB metadata;
- download the requested movie into the expected movie path;
- trigger a Radarr rescan/import;
- verify whether the movie was imported;
- optionally mark the movie as unmonitored after successful import.

### Recommended setup

Use shared volumes so VibraVid and Sonarr/Radarr see the same filesystem paths. If containers use different internal paths for the same media folder, imports may fail because Sonarr/Radarr will not find the downloaded files.

Example Docker path layout:

```text
/media
├── movies
├── series
└── downloads
```

Mount the same media root into VibraVid, Sonarr and Radarr whenever possible.

---

## Docker

### Recommended: Docker Compose

```bash
docker-compose up -d        # Start
docker-compose logs -f      # View logs
docker-compose down         # Stop (data persists)
```

For NAS users (Synology, TrueNAS, Unraid, etc.), see **[docs/NAS.md](docs/NAS.md)** for a step-by-step setup guide including bind mounts and permission configuration.

### Custom paths and ports

Copy the provided template and edit the values you need:

```bash
cp .env.example .env
```

Key variables (full list in `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `VIBRAVID_PORT` | `8000` | Host port exposed by the container |
| `VIBRAVID_VIDEO_DIR` | named volume | Where downloads land on the host (e.g. `/volume2/Movies`) |
| `VIBRAVID_DB_DIR` | named volume | Host path for the SQLite database |
| `VIBRAVID_CONFIG_DIR` | named volume | Host path for `config.json` / `login.json` |
| `VIBRAVID_LOGS_DIR` | named volume | Host path for application logs |
| `ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hostnames Django accepts |
| `CSRF_TRUSTED_ORIGINS` | `http://localhost:8000,...` | Origins for CSRF validation |

**NAS example** — store downloads on a NAS share, expose on port 9000:

```env
VIBRAVID_PORT=9000
VIBRAVID_VIDEO_DIR=/volume2/Movies
VIBRAVID_DB_DIR=/volume1/docker/vibravid/db
VIBRAVID_CONFIG_DIR=/volume1/docker/vibravid/conf
VIBRAVID_LOGS_DIR=/volume1/docker/vibravid/logs
ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.100
CSRF_TRUSTED_ORIGINS=http://192.168.1.100:9000
```

Then start normally:
```bash
docker-compose up -d
```

### Private Network Deployment

Uncomment and edit the `environment` section in `docker-compose.yml`:

```yaml
environment:
  DJANGO_DEBUG: "false"
  ALLOWED_HOSTS: "streaming.example.local,localhost,127.0.0.1,192.168.1.50"
  CSRF_TRUSTED_ORIGINS: "https://streaming.example.local"
  USE_X_FORWARDED_HOST: "true"
  SECURE_PROXY_SSL_HEADER_ENABLED: "true"
  CSRF_COOKIE_SECURE: "true"
  SESSION_COOKIE_SECURE: "true"
  DJANGO_SECRET_KEY: "your-secure-secret-key-here"
```

ARR-related variables can be added to the same `environment` block:

```yaml
environment:
  USE_ARR_SERVICES: "true"
  SONARR_URL: "http://sonarr:8989"
  SONARR_API_KEY: "your-sonarr-api-key"
  RADARR_URL: "http://radarr:7878"
  RADARR_API_KEY: "your-radarr-api-key"
  SEERR_WEBHOOK_SECRET: "your-seerr-secret"
  SONARR_WEBHOOK_SECRET: "your-sonarr-secret"
  RADARR_WEBHOOK_SECRET: "your-radarr-secret"
```


### Manual Docker Build

```bash
docker build -t vibravid .

docker run -d \
  --name vibravid \
  -p 8000:8000 \
  -v vibravid_db:/app/data \
  -v vibravid_videos:/app/Video \
  -v vibravid_logs:/app/logs \
  -v vibravid_config:/app/Conf \
  --restart unless-stopped \
  vibravid
```

### Binding Local Folders

```bash
# Linux/macOS
docker run -d --name vibravid -p 8000:8000 \
  -v ~/Downloads/Videos:/app/Video \
  vibravid

# Windows (PowerShell)
docker run -d --name vibravid -p 8000:8000 `
  -v "D:\Video:/app/Video" `
  vibravid
```

### Updating (Docker)

When a new version is released, VibraVid shows an **update banner** in the web UI. Click **Update now** to trigger the update.

The container cannot restart itself — it writes a marker file to `/app/data/.update_requested`. A host-side script (`scripts/nas-update.sh`) picks it up and runs:

```bash
docker compose pull
docker compose up -d
```

To use the one-click update, install and start `scripts/nas-update.sh` on the host. See [scripts/README.md](scripts/README.md) for setup instructions (systemd, Synology Scheduled Task, cron).

**Manual update** (works without the script):

```bash
docker compose pull
docker compose up -d
```

---

## Known Issues

The following issues are known and are expected to be addressed in upcoming releases. They do not affect the core download functionality but may impact the user experience in specific scenarios.

**Download progress not shown for some providers**

For certain providers the download progress bar in the GUI may not update or may remain at 0% for the duration of the download. The download is still running in the background and will complete normally — the issue is limited to the progress display. This typically affects providers that stream through intermediate layers rather than exposing direct segment URLs.

**Velora Bridge console errors (connection / rate limit)**

During downloads that go through Velora Bridge you may see warnings or errors in the console such as connection timeouts, stream read failures, or retry messages. These are caused by transient network conditions, proxy rate limiting, or per-session connection limits on the provider side. Velora Bridge retries automatically and the download usually completes successfully. If errors persist, check your proxy configuration and ensure the provider is not applying a rate limit to your IP.

---

## Related Projects

- **[MammaMia](https://github.com/UrloMythus/MammaMia)** — Stremio addon for Italian streaming (by UrloMythus)
- **[Unit3Dup](https://github.com/31December99/Unit3Dup)** — Torrent automation for Unit3D tracker (by 31December99)
- **[N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE)** — Universal downloader for HLS/DASH/ISM (by nilaoda)
- **[pywidevine](https://github.com/devine-dl/pywidevine)** — Widevine L3 decryption library (by devine-dl)
- **[pyplayready](https://git.gay/ready-dl/pyplayready)** — PlayReady decryption library (by ready-dl)

---

## Disclaimer

> This software is for **educational and research purposes only**. The authors:
>
> - **DO NOT** assume responsibility for illegal use
> - **DO NOT** provide or facilitate DRM circumvention tools, CDMs, or decryption keys
> - **DO NOT** endorse piracy or copyright infringement
>
> By using this software, you agree to comply with all applicable laws and confirm you have rights to any content you process. No warranty is provided.

---

<div align="center">

**Made with ❤️ for streaming lovers**

*If you find this project useful, consider starring it! ⭐*

</div>
