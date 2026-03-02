<div align="center">

[![PyPI Version](https://img.shields.io/pypi/v/streamingcommunity?logo=pypi&logoColor=white&labelColor=2d3748&color=3182ce&style=for-the-badge)](https://pypi.org/project/streamingcommunity/)
[![Sponsor](https://img.shields.io/badge/💖_Sponsor-ea4aaa?style=for-the-badge&logo=github-sponsors&logoColor=white&labelColor=2d3748)](https://ko-fi.com/arrowar)

[![Windows](https://img.shields.io/badge/🪟_Windows-0078D4?style=for-the-badge&logo=windows&logoColor=white&labelColor=2d3748)](https://github.com/Arrowar/StreamingCommunity/releases/latest/download/StreamingCommunity_win_2025_x64.exe)
[![macOS](https://img.shields.io/badge/🍎_macOS-000000?style=for-the-badge&logo=apple&logoColor=white&labelColor=2d3748)](https://github.com/Arrowar/StreamingCommunity/releases/latest/download/StreamingCommunity_mac_15_x64)
[![Linux](https://img.shields.io/badge/🐧_Linux_latest-FCC624?style=for-the-badge&logo=linux&logoColor=black&labelColor=2d3748)](https://github.com/Arrowar/StreamingCommunity/releases/latest/download/StreamingCommunity_linux_24_04_x64)

*⚡ **Quick Start:** `pip install StreamingCommunity && StreamingCommunity`*

</div>

## 📖 Table of Contents
- [Installation](#installation)
- [Quick Start](#quick-start)
- [DNS Configuration](#dns-configuration)
- [Downloaders](#downloaders)
- [Configuration](#configuration)
- [Usage Examples](#usage-examples)
- [Global Search](#global-search)
- [Advanced Features](#advanced-features)
- [Docker](#docker)
- [TO DO](#todo)
- [Related Projects](#related-projects)

---

## Installation

### Manual Clone
```bash
git clone https://github.com/Arrowar/StreamingCommunity.git
cd StreamingCommunity
pip install -r requirements.txt
python test_run.py
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
StreamingCommunity

# If cloned manually
python test_run.py
```

---

## DNS Configuration

**Required for optimal functionality and reliability.**

Use one of these DNS providers:

- **Cloudflare DNS**: `1.1.1.1` - [Setup guide](https://developers.cloudflare.com/1.1.1.1/setup/)
- **Quad9 DNS**: `9.9.9.9` - [Setup guide](https://quad9.net/)

---

## Downloaders

| Type | Description | Example |
|------|-------------|---------|
| **HLS** | HTTP Live Streaming (m3u8) | [View example](./Test/Downloads/HLS.py) |
| **MP4** | Direct MP4 download | [View example](./Test/Downloads/MP4.py) |
| **DASH** | MPEG-DASH with DRM bypass* | [View example](./Test/Downloads/DASH.py) |
| **MEGA** | MEGA.nz downloads | [View example](./Test/Downloads/MEGA.py) |

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
        "episode_format": "%(episode_name) S%(season)E%(episode)",
        "season_format": "S%(season)",
        "add_siteName": false
    }
}
```

- **`root_path`**: Base directory where videos are saved
  - Windows: `C:\\MyLibrary\\Folder` or `\\\\MyServer\\Share`
  - Linux/MacOS: `Desktop/MyLibrary/Folder`

- **`movie_folder_name`**: Subfolder name for movies (default: `"Movie"`)
- **`serie_folder_name`**: Subfolder name for TV series (default: `"Serie"`)
- **`anime_folder_name`**: Subfolder name for anime (default: `"Anime"`)

- **`episode_format`**: Episode filename template
  - `%(tv_name)`: TV Show name
  - `%(season)`: Season number (zero-padded)
  - `%(episode)`: Episode number (zero-padded)
  - `%(episode_name)`: Episode title
  - Example: `"%(episode_name) S%(season)E%(episode)"` → `"Pilot S01E01"`

- **`season_format`**: Season folder name template (default: `"S%(season)"`)
  - `%(season)`: Season number (zero-padded)
  - Example: `"S%(season)"` → `"S01"` or `"Stagione %(season)"` → `"Stagione 1"`

- **`add_siteName`**: Append site name to root path (default: `false`)

### Download Settings
```json
{
    "DOWNLOAD": {
        "thread_count": 12,
        "retry_count": 40,
        "concurrent_download": true,
        "max_speed": "30MB",
        "select_video": "res=.*1080.*:for=best",
        "select_audio": "lang='ita|Ita':for=all",
        "select_subtitle": "lang='ita|eng|Ita|Eng':for=all",
        "cleanup_tmp_folder": true
    }
}
```

#### Performance Settings
- **`auto_select`**: Automatically select streams based on filters (default: `true`). When `false`, enables interactive stream selection mode where user can manually choose video/audio/subtitle tracks before download.
- **`skip_download`**: Skip the download step and process existing files (default: `false`)
- **`thread_count`**: Number of parallel download threads (default: `12`)
- **`retry_count`**: Maximum retry attempts for failed segments (default: `40`)
- **`concurrent_download`**: Download video and audio simultaneously (default: `true`)
- **`max_speed`**: Speed limit per stream (e.g., `"30MB"`, `"10MB"`)
- **`cleanup_tmp_folder`**: Remove temporary files after download (default: `true`)

#### Stream Selection

**- `select_video`**
```
OPTIONS: id=REGEX:lang=REGEX:name=REGEX:codecs=REGEX:res=REGEX:frame=REGEX:
         segsMin=number:segsMax=number:ch=REGEX:range=REGEX:url=REGEX:
         plistDurMin=hms:plistDurMax=hms:bwMin=int:bwMax=int:role=string:for=FOR

    for=FOR: Selection type - best (default), best[number], worst[number], all
```
```json
"select_video": "for=best"                                // Select best video
"select_video": "res=3840*:codecs=hvc1:for=best"          // Select 4K HEVC video
"select_video": "res=1080:for=best"                       // Select 1080p video
"select_video": "plistDurMin=1h20m30s:for=best"           // Duration > 1h 20m 30s
"select_video": "role=main:for=best"                      // Main video role
"select_video": "bwMin=800:bwMax=1000:for=best"           // Bandwidth 800-1000 Kbps
```

**- `select_audio`** 
```json
"select_audio": "for=all"                                 // Select all audio tracks
"select_audio": "lang=en:for=best"                        // Select best English audio
"select_audio": "lang='ja|en':for=best2"                  // Best 2 tracks (Japanese or English)
"select_audio": "lang='ita|Ita':for=all"                  // All Italian audio tracks
"select_audio": "role=main:for=best"                      // Main audio role
```

**- `select_subtitle`** 
```json
"select_subtitle": "for=all"                              // Select all subtitles
"select_subtitle": "name=English:for=all"                 // All subtitles containing "English"
"select_subtitle": "lang='ita|eng|Ita|Eng':for=all"       // Italian and English subtitles
"select_subtitle": "lang=en:for=best"                     // Best English subtitle
"select_subtitle": "false"                                // Disable subtitle download
```

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
        "subtitle_disposition": true,
        "subtitle_disposition_language": ["forced-ita", "ita-forced"],
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
- **`subtitle_disposition`**: Automatically set default subtitle track (default: `true`)
- **`subtitle_disposition_language`**: Languages to mark as default/forced
  - Example: `["forced-ita", "ita-forced"]` for Italian forced subtitles
- **`extension`**: Output file format (`"mkv"` or `"mp4"`)

### Request Settings
```json
{
    "REQUESTS": {
        "verify": false,
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

- **`verify`**: Enable SSL certificate verification (default: `false`)
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
        "fetch_domain_online": true
    }
}
```

- **`close_console`**: Automatically close console after download completion (default: `true`)
- **`show_message`**: Display debug messages (default: `false`)
- **`fetch_domain_online`**: Automatically fetch latest domains from GitHub (default: `true`)

---

## Usage Examples

### Basic Commands
```bash
# Show help and available sites
python test_run.py -h

# Search and download
python test_run.py --site streamingcommunity --search "interstellar"

# Auto-download first result
python test_run.py --site streamingcommunity --search "interstellar" --auto-first

# Use site by index
python test_run.py --site 0 --search "interstellar"
```

## Global Search

Search across multiple streaming sites simultaneously:

```bash
# Global search
python test_run.py --global -s "cars"

# Search by category
python test_run.py --category 1    # Anime
python test_run.py --category 2    # Movies & Series
python test_run.py --category 3    # Series only
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
        "env": {"MY_FLAG": "1"},
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
        "args": [
          "{download_path}"
        ],
        "env": {
          "MY_FLAG": "1"
        },
        "cwd": "~",
        "os": [
          "linux"
        ],
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
docker build -t streaming-community-api .

# Run with Cloudflare DNS
docker run -d --name streaming-community --dns 1.1.1.1 -p 8000:8000 streaming-community-api
```

### Volumes and Permissions
When mounting a local folder as a volume, you might encounter permission issues. Using `-u root` ensures the container has the necessary rights to write to your host machine:

```bash
docker run -d --name streaming-community --dns 1.1.1.1 -p 8000:8000 -u root -v D:\Download:/app/Video streaming-community-api
```

### Docker Compose Example
Recommended for stability and easy DNS configuration:

```yaml
services:
  streaming-community:
    build: .
    container_name: streaming-community
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

## TODO

- [ ] Improve the GUI; Enhance the graphical user interface and display images for all episodes.
- [ ] Add images to search results: Show a thumbnail/image for each title in the search results.
- [ ] Add images for each episode: Display a dedicated image or thumbnail for every episode.
- [ ] Improve season selection: Populate the season selector using the actual extracted seasons (real data), instead of assuming a range from 1 to N.
- [ ] Add login for discovery, dmax ...
- [ ] Use asyncio for manual downloader

---

## Related Projects

- **[MammaMia](https://github.com/UrloMythus/MammaMia)** - Stremio addon for Italian streaming
- **[Unit3Dup](https://github.com/31December99/Unit3Dup)** - Torrent automation for Unit3D tracker

---

## Disclaimer
>
> This software is provided strictly for **educational and research purposes only**. The author and contributors:
>
> - **DO NOT** assume any responsibility for illegal or unauthorized use of this software
> - **DO NOT** encourage, promote, or support the download of copyrighted content without proper authorization
> - **DO NOT** provide, include, or facilitate obtaining any DRM circumvention tools, CDM modules, or decryption keys
> - **DO NOT** endorse piracy or copyright infringement in any form
>
> ### User Responsibilities
>
> By using this software, you agree that:
>
> 1. **You are solely responsible** for ensuring your use complies with all applicable local, national, and international laws and regulations
> 2. **You must have legal rights** to access and download any content you process with this software
> 3. **You will not use** this software to circumvent DRM, access unauthorized content, or violate copyright laws
> 4. **You understand** that downloading copyrighted content without permission is illegal in most jurisdictions
>
> ### No Warranty
>
> This software is provided "as is", without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and noninfringement. In no event shall the authors or copyright holders be liable for any claim, damages, or other liability, whether in an action of contract, tort, or otherwise, arising from, out of, or in connection with the software or the use or other dealings in the software.
>
> **If you do not agree with these terms, do not use this software.**

---

<div align="center">
**Made with ❤️ for streaming lovers**
*If you find this project useful, consider starring it! ⭐*
</div>
