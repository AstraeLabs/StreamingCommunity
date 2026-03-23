<div align="center">

[![PyPI Version](https://img.shields.io/pypi/v/vibravid?logo=pypi&logoColor=white&labelColor=2d3748&color=3182ce&style=for-the-badge)](https://pypi.org/project/vibravid/)
[![Sponsor](https://img.shields.io/badge/💖_Sponsor-ea4aaa?style=for-the-badge&logo=github-sponsors&logoColor=white&labelColor=2d3748)](https://ko-fi.com/arrowar)

[![Windows](https://img.shields.io/badge/🪟_Windows-0078D4?style=for-the-badge&logo=windows&logoColor=white&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_win_2025_x64.exe)
[![macOS](https://img.shields.io/badge/🍎_macOS-000000?style=for-the-badge&logo=apple&logoColor=white&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_mac_15_x64)
[![Linux](https://img.shields.io/badge/🐧_Linux_latest-FCC624?style=for-the-badge&logo=linux&logoColor=black&labelColor=2d3748)](https://github.com/AstraeLabs/VibraVid/releases/latest/download/VibraVid_linux_24_04_x64)

_⚡ **Quick Start:** `pip install VibraVid && VibraVid`_

</div>

## 📖 Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Login](.github/doc/login.md)
- [Downloaders](#downloaders)
- [Configuration](#configuration)
- [Usage Examples](#usage-examples)
- [Global Search](#global-search)
- [Advanced Features](#advanced-features)
- [Docker](#docker)
- [GUI](GUI/README.md)
- [Related Projects](#related-projects)

---

## Installation

### Manual Clone

```bash
git clone https://github.com/AstraeLabs/VibraVid.git
cd VibraVid
pip install -r requirements.txt
python manual.py
```

### Update

```bash
python update.py
```

### Additional Documentation

- 📝 [Login Guide](.github/doc/login.md) - Authentication for supported services

---

## Quick Start

```bash
# If installed via PyPI
VibraVid

# If cloned manually
python manual.py
```

## Downloaders

| Type     | Description                 | Example                                  |
| -------- | --------------------------- | ---------------------------------------- |
| **HLS**  | HTTP Live Streaming (m3u8)  | [View example](./Test/Downloads/HLS.py)  |
| **MP4**  | Direct MP4 download         | [View example](./Test/Downloads/MP4.py)  |
| **DASH** | MPEG-DASH with DRM bypass\* | [View example](./Test/Downloads/DASH.py) |

**\*DASH with DRM bypass:** Requires a valid L3 CDM (Content Decryption Module). This project does not provide or facilitate obtaining CDMs. Users must ensure compliance with applicable laws.

---

## Configuration

Key configuration parameters in `config.json`:

### Output Directories

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

- **`root_path`**: Base directory where videos are saved
    - Windows: `C:\\MyLibrary\\Folder` or `\\\\MyServer\\Share`
    - Linux/MacOS: `Desktop/MyLibrary/Folder`

- **`movie_folder_name`**: Subfolder name for movies (default: `"Movie"`)
    - Supports `%{site_name}` placeholder: `"Movie/%{site_name}"` → `"Movie/Crunchyroll"`
    - Example with year: `"Movie (%{site_name})"`

- **`serie_folder_name`**: Subfolder name for TV series (default: `"Serie"`)
    - Supports `%{site_name}` placeholder: `"Serie/%{site_name}"` → `"Serie/Crunchyroll"`
    - Example with year: `"Series by %{site_name}"`

- **`anime_folder_name`**: Subfolder name for anime (default: `"Anime"`)
    - Supports `%{site_name}` placeholder: `"Anime/%{site_name}"` → `"Anime/Crunchyroll"`

---

#### Movie Format Configuration

**Default format:** `"%(title_name) (%(title_year))/%(title_name) (%(title_year))"`

Results in:

```
%(title_name) (%(title_year))/  → Movie folder  (Inception (2010))
%(title_name) (%(title_year))   → Filename      (Inception (2010).mkv)
```

**Format variables:**
- `%(title_name)`: Movie title
- `%(title_name_slug)`: Movie title as slug
- `%(title_year)`: Movie release year (optional, removed if not available)
- `%(quality)`: Video resolution
- `%(language)`: Audio languages
- `%(video_codec)`: Video codec
- `%(audio_codec)`: Audio codec
---

#### Episode Format Configuration

**Default format:** `"%(series_name)/S%(season:02d)/%(episode_name) S%(season:02d)E%(episode:02d)"`

Results in:

```
%(series_name)/      → Series folder  (Breaking Bad)
S%(season:02d)/      → Season folder  (S01, S02, ...)
%(episode_name)...   → Filename       (Pilot S01E05.mkv)
```

**Format variables:**

- `%(series_name)`: Series name
- `%(series_name_slug)`: Series name as slug
- `%(series_year)`: Series release year
- `%(season:FORMAT)`: Season number — padding controlled inline (see table below)
- `%(episode:FORMAT)`: Episode number — padding controlled inline (see table below)
- `%(episode_name)`: Episode title (sanitized)
- `%(episode_name_slug)`: Episode title as slug
- `%(quality)`: Video resolution
- `%(language)`: Audio languages
- `%(video_codec)`: Video codec
- `%(audio_codec)`: Audio codec

**Inline Padding Syntax:**

| Token           | Result (n=1) | Description          |
| --------------- | ------------ | -------------------- |
| `%(season:02d)` | `01`         | Zero-pad to 2 digits |
| `%(season:03d)` | `001`        | Zero-pad to 3 digits |
| `%(season:d)`   | `1`          | No padding           |


---

**Note:** The legacy `add_siteName` option has been removed. Use `%{site_name}` placeholder in folder names instead:

```json
"movie_folder_name": "Movie/%{site_name}",
"serie_folder_name": "Serie/%{site_name}"
```

### Download Settings

```json
{
	"DOWNLOAD": {
		"thread_count": 12,
		"retry_count": 40,
		"concurrent_download": true,
		"max_speed": "30MB",
		"select_video": "1920",
		"select_audio": "ita|Ita",
		"select_subtitle": "ita|eng|Ita|Eng",
		"cleanup_tmp_folder": true
	}
}
```

#### Performance Settings

- **`skip_download`**: Skip the download step and process existing files (default: `false`)
- **`thread_count`**: Number of parallel download threads (default: `12`)
- **`retry_count`**: Maximum retry attempts for failed segments (default: `40`)
- **`concurrent_download`**: Enable parallel download queue for films and series episodes (default: `true`). When `true`, downloads are queued and processed by a thread pool with a live Download Monitor table. When `false`, downloads run sequentially. When only one item is in the queue, it will download immediately regardless of this setting.
- **`max_speed`**: Speed limit per stream (e.g., `"30MB"`, `"10MB"`)
- **`cleanup_tmp_folder`**: Remove temporary files after download (default: `true`)

### Processing Settings

```json
{
	"PROCESS": {
		"generate_nfo": false,
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

- **`generate_nfo`**: Generate .nfo metadata file alongside the video (default: `false`)
- **`use_gpu`**: Enable hardware acceleration (default: `false`)
- **`param_video`**: FFmpeg video encoding parameters
    - Example: `["-c:v", "libx265", "-crf", "28", "-preset", "medium"]` (H.265/HEVC encoding)
- **`param_audio`**: FFmpeg audio encoding parameters
    - Example: `["-c:a", "libopus", "-b:a", "128k"]` (Opus audio at 128kbps)
- **`param_final`**: Final FFmpeg parameters (default: `["-c", "copy"]` for stream copy)
- **`audio_order`**: List of strings to order audio tracks (e.g., `["ita", "eng"]`)
- **`subtitle_order`**: List of strings to order subtitle tracks (e.g., `["ita", "eng"]`)
- **`merge_audio`**: Merge all audio tracks into a single output file (default: `true`)
- **`merge_subtitle`**: Merge all subtitle tracks into a single output file (default: `true`)
- **`subtitle_disposition_language`**: Mark a specific subtitle as default/forced
- **`force_subtitle`**: How subtitles are handled before remuxing
    - `"auto"` (default): subtitles are renamed/converted according to their detected format; VTT files are also sanitized (unmatched `<` replaced) to avoid data loss when muxed as SRT.
    - `"copy"`: do not convert or rename, just mux the original file as-is (useful if you want to preserve VTT output). This also skips the VTT sanitization step, so any problematic `<` characters remain untouched.
    - `"srt"`, `"vtt"`, `"ass"`: force-convert all subtitle tracks to the specified format using ffmpeg, applying sanitization for `vtt` as needed.
    - See `VibraVid/core/processors/helper/ex_sub.py` for conversion logic.
- **`extension`**: Output file format (`"mkv"` or `"mp4"`)

### Request Settings

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

- **`timeout`**: Request timeout in seconds (default: `30`)
- **`max_retry`**: Maximum retry attempts for failed requests (default: `10`)
- **`use_proxy`**: Enable proxy support for HTTP requests (default: `false`)
- **`proxy`**: Proxy configuration for HTTP and HTTPS connections
    - **`http`**: HTTP proxy URL (e.g., `"http://localhost:8888"`)
    - **`https`**: HTTPS proxy URL (e.g., `"http://localhost:8888"`)

### Default Settings

```json
{
	"DEFAULT": {
		"close_console": true,
		"show_message": false,
		"fetch_domain_online": true,
		"auto_update_check": true
	}
}
```

- **`close_console`**: Automatically close console after download completion (default: `true`)
- **`show_message`**: Display debug messages (default: `false`)
- **`fetch_domain_online`**: Automatically fetch latest domains from GitHub (default: `true`)
- **`auto_update_check`**: Check for new VibraVid updates automatically at startup (default: `true`). If enabled, notifies you when a new version is available.

---

## Usage Examples

### Basic Commands

```bash
# Show help and available sites
python manual.py -h

# Search and download
python manual.py --site streamingcommunity --search "interstellar"

# Auto-download first result
python manual.py --site streamingcommunity --search "interstellar" --auto-first

# Use site by index
python manual.py --site 0 --search "interstellar"
```

## Global Search

Search across multiple streaming sites simultaneously:

```bash
# Global search
python manual.py --global -s "cars"

# Search by category
python manual.py --category 1    # Anime
python manual.py --category 2    # Movies & Series
python manual.py --category 3    # Series only
```

Results display title, media type, and source site in a consolidated table.

---

## Advanced Features

### Hook System

Execute custom scripts before/after downloads. Configure in `config.json`:

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
				"env": {
					"MY_FLAG": "1"
				},
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

#### Hook Configuration Options

- **Stages available**: `pre_run`, `post_download`, `post_run`
- **`name`**: Descriptive name for the hook
- **`type`**: Script type - `python`, `bash`, `sh`, `shell`, `bat`, `cmd`
- **`path`**: Path to script file (alternative to `command`)
- **`command`**: Inline command to execute (alternative to `path`)
- **`args`**: List of arguments passed to the script
- **`env`**: Additional environment variables as key-value pairs
- **`cwd`**: Working directory for script execution (supports `~` and environment variables)
- **`os`**: Optional OS filter - `["windows"]`, `["darwin"]` (macOS), `["linux"]`, or combinations
- **`timeout`**: Maximum execution time in seconds (hook fails if exceeded)
- **`enabled`**: Enable/disable the hook without removing configuration
- **`continue_on_error`**: If `false`, stops execution when hook fails

#### Hook Types

- **Python hooks**: Run with current Python interpreter
- **Bash/sh/shell hooks**: All three types execute via `/bin/bash -c` on macOS/Linux
- **Bat/cmd/shell hooks**: Execute via `cmd /c` on Windows
- **Inline commands**: Use `command` instead of `path` for simple one-liners. Note: `args` are ignored when using `command`; they only apply when using `path`.

#### Hook Context Placeholders

Hooks can interpolate download context in `path`, `command`, `args`, `env`, and `cwd`.

- **`{download_path}`**: Absolute path of the downloaded file
- **`{download_dir}`**: Directory containing the downloaded file
- **`{download_filename}`**: Filename of the downloaded file
- **`{download_id}`**: Internal download identifier
- **`{download_title}`**: Download title
- **`{download_site}`**: Source site name
- **`{download_media_type}`**: Media type
- **`{download_status}`**: Final download status
- **`{download_error}`**: Error message, if present
- **`{download_success}`**: `1` on success, `0` on failure
- **`{stage}`**: Current hook stage

The same values are also exposed as environment variables with the `SC_` prefix, such as `SC_DOWNLOAD_PATH`, `SC_DOWNLOAD_FILENAME`, `SC_DOWNLOAD_SUCCESS`, and `SC_HOOK_STAGE`.

Hooks are automatically executed before the main flow (`pre_run`), after each completed download (`post_download`), and at the end of the main execution flow (`post_run`). In the GUI, `post_download` runs for every individual completed item, while `post_run` is triggered once when the overall execution ends.

---

## Docker

### Basic Setup

```bash
# Build image
docker build -t vibravid-api .

# Run with Cloudflare DNS
docker run -d --name vibravid --dns 1.1.1.1 -p 8000:8000 vibravid-api
```

### Volumes and Permissions

When mounting a local folder as a volume, you might encounter permission issues. Using `-u root` ensures the container has the necessary rights to write to your host machine:

```bash
docker run -d --name vibravid --dns 1.1.1.1 -p 8000:8000 -u root -v D:\Download:/app/Video vibravid-api
```

### Docker Compose Example

Recommended for stability and easy DNS configuration:

```yaml
services:
    vibravid:
        build: .
        container_name: vibravid
        user: root
        dns:
            - 1.1.1.1
            - 8.8.8.8
        ports:
            - "8000:8000"
        #environment:
        # Replace these example values with your public domain and private LAN IP.
        #ALLOWED_HOSTS: "streaming.example.local localhost 127.0.0.1 192.168.1.50"
        #CSRF_TRUSTED_ORIGINS: "https://streaming.example.local"
        #USE_X_FORWARDED_HOST: "true"
        #SECURE_PROXY_SSL_HEADER_ENABLED: "true"
        #CSRF_COOKIE_SECURE: "true"
        #SESSION_COOKIE_SECURE: "true"
        volumes:
            - ./Video:/app/Video
        restart: unless-stopped
```

The `environment` section is intended for deployments behind an HTTPS reverse proxy. Replace the example domain and private IP with your own values.

---

## Related Projects

- **[MammaMia](https://github.com/UrloMythus/MammaMia)** - Stremio addon for Italian streaming (by UrloMythus)
- **[Unit3Dup](https://github.com/31December99/Unit3Dup)** - Torrent automation for Unit3D tracker (by 31December99)
- **[N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE)** - Universal downloader for HLS/DASH/ISM (by nilaoda)
- **[pywidevine](https://github.com/devine-dl/pywidevine)** - Widevine L3 decryption library (by devine-dl)
- **[pyplayready](https://git.gay/ready-dl/pyplayready)** - PlayReady decryption library (by ready-dl)

---

## Disclaimer

> This software is for **educational and research purposes only**. The authors:
>
> - **DO NOT** assume responsibility for illegal use
> - **DO NOT** provide or facilitate DRM circumvention tools, CDMs, or decryption keys
> - **DO NOT** endorse piracy or copyright infringement
>
> By using this software, you agree to comply with all laws and have rights to any content you process. No warranty is provided. If you do not agree, do not use this software.

---

<div align="center">
**Made with ❤️ for streaming lovers**
*If you find this project useful, consider starring it! ⭐**
</div>
