# Configuration

All settings live in `config.json`. The sections below cover each configuration block.
ARR automation settings are documented separately in the [ARR Integration](arr.md) guide.

## DEFAULT

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

## OUTPUT

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
"Movie/%{site_name}"  ->  "Movie/Crunchyroll"
"Serie/%{site_name}"  ->  "Serie/Crunchyroll"
```

### Movie Format

**Default:** `"%(title_name) (%(title_year))/%(title_name) (%(title_year))"`

```
%(title_name) (%(title_year))/   ->  folder    Inception (2010)/
%(title_name) (%(title_year))    ->  filename  Inception (2010).mkv
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
| `%(audio_flags)` | Audio track flags, e.g. `DEFAULT` |
| `%(sub_flags)` | Subtitle track flags, e.g. `CC-SDH-FORCED` |
| `%(original_title)` | Original-language title (requires TMDB API key) |
| `%(original_language)` | Original language code, e.g. `ja` (requires TMDB API key) |
| `%(tmdb_id)` | TMDB ID (requires TMDB API key) |
| `%(tmdb_title)` | TMDB title in the configured lookup language (requires TMDB API key) |
| `%(imdb_id)` | IMDb ID, e.g. `tt0409591` (requires TMDB API key) |

### Episode Format

**Default:** `"%(series_name)/S%(season:02d)/%(episode_name) S%(season:02d)E%(episode:02d)"`

```
%(series_name)/     ->  series folder   Breaking Bad/
S%(season:02d)/     ->  season folder   S01/
%(episode_name)...  ->  filename        Pilot S01E05.mkv
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
| `%(absolute:FORMAT)` | Absolute episode number with inline padding — anime only (AnimeUnity/AnimeWorld) |
| `%(quality)` | Video resolution |
| `%(language)` | Audio languages |
| `%(video_codec)` | Video codec |
| `%(audio_codec)` | Audio codec |
| `%(audio_flags)` | Audio track flags, e.g. `DEFAULT` |
| `%(sub_flags)` | Subtitle track flags, e.g. `CC-SDH-FORCED` |
| `%(original_title)` | Original-language title (requires TMDB API key) |
| `%(original_language)` | Original language code, e.g. `ja` (requires TMDB API key) |
| `%(tmdb_id)` | TMDB ID (requires TMDB API key) |
| `%(tmdb_title)` | TMDB title in the configured lookup language (requires TMDB API key) |
| `%(tmdb_episode_title)` | TMDB episode title, resolved from `%(tmdb_id)` + season/episode number (requires TMDB API key) |
| `%(tmdb_season_number:FORMAT)` | Real TMDB season number, mapped from an absolute/flat episode number when needed (anime) — supports inline padding like `season`/`episode` (requires TMDB API key) |
| `%(tmdb_season_name)` | TMDB season name, e.g. `Season 2`, mapped the same way (requires TMDB API key) |
| `%(imdb_id)` | IMDb ID, e.g. `tt0409591` (requires TMDB API key) |

**Inline padding syntax (for `season`, `episode` and `absolute`):**

| Token | Result (n=1) | Description |
|-------|-------------|-------------|
| `%(season:02d)` | `01` | Zero-pad to 2 digits |
| `%(season:03d)` | `001` | Zero-pad to 3 digits |
| `%(season:d)` | `1` | No padding |

> Tokens that cannot be resolved (e.g. TMDB tokens without an API key, or `%(absolute)` on non-anime services) are removed from the filename together with any surrounding `[]`/`()` wrapper, so they never leak as literal text.

### Recommended configuration (with a TMDB API key)

```json
"movie_format": "%(title_name) (%(title_year)) [%(tmdb_id)]/%(title_name) (%(title_year)) [%(tmdb_title)] [%(quality)]",
"episode_format": "%(series_name) [%(tmdb_id)]/S%(tmdb_season_number:02d) - %(tmdb_season_name)/%(episode_name) - %(tmdb_episode_title) S%(season:02d)E%(episode:02d) [%(tmdb_title)] [%(quality)]",
```

## DOWNLOAD

```json
{
  "DOWNLOAD": {
    "auto_select": true,
    "delay_after_download": 1,
    "skip_download": false,
    "thread_count": 12,
    "decrypt_worker_count": 12,
    "realtime_decrypt": true,
    "concurrent_download": true,
    "select_video": "1920",
    "select_audio": "ita|Ita",
    "select_subtitle": "ita|eng|Ita|Eng",
    "extract_embedded_cc": false,
    "cleanup_tmp_folder": true,
    "embed_poster": true,
    "engine": "ffmpeg"
  }
}
```

### Performance Settings

| Key | Default | Description |
|-----|---------|-------------|
| `auto_select` | `true` | Automatically select streams based on filters. When `false`, enables interactive track selection before download |
| `delay_after_download` | `1` | Delay (seconds) applied after each movie or episode download |
| `skip_download` | `false` | Skip the download step and process existing files |
| `thread_count` | `12` | Number of concurrent segment requests for a single stream |
| `decrypt_worker_count` | `THREAD_COUNT` | Number of segments decrypted in parallel when `realtime_decrypt` is `true`. |
| `realtime_decrypt` | `true` | Decrypt each segment as it downloads (in-flight) instead of decrypting the whole file once after merging. |
| `concurrent_download` | `true` | Download video, audio, and subtitles simultaneously |
| `extract_embedded_cc` | `false` | HLS only: extract embedded CEA-608/708 closed captions (`EXT-X-MEDIA:TYPE=CLOSED-CAPTIONS`, no separate subtitle file) from the downloaded video into a subtitle track. Opt-in because it requires decoding the whole video, adding extra time/CPU per download |
| `cleanup_tmp_folder` | `true` | Remove temporary files after download |
| `embed_poster` | `true` | Embed a poster/still into the downloaded file: the matching TMDB artwork if found, otherwise the site's own poster/still as a fallback |
| `engine` | `"ffmpeg"` | Muxing engine used to combine video, audio and subtitle tracks. `ffmpeg`, `mkvmerge` requires a **full installation** |

### Stream Selection Filters

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
| `"b=8000:f=best"` | Bitrate cap (kbps) — best within range. Useful when the highest-bitrate rendition isn't decryptable with your DRM device/security level |
| `"b=1000-8000:f=best"` | Bitrate range (kbps) — best within range |
| `"b=1000-:f=best"` | Bitrate floor only (no upper bound) — best within range |
| `"false"` | Skip video |

Native `key=value` filters combine multiple constraints (resolution, codec, bitrate, id, fallback) in one string, joined by `:` — keys are `r=` (resolution), `c=` (codec), `b=` (bitrate), `i=` (manifest id, regex), `f=` (fallback: `best`/`worst`/`all`), e.g. `"r=1080:c=hvc1:f=best"`.

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
| `"b=64-192:f=best"` | Bitrate range (kbps) — best within range | Ignores range if no match |
| `"false"` | Skip audio | — |

Same native keys as video, plus `l=` for language, e.g. `"l=ita:c=aac:f=best"` (language + codec + fallback).

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

**Companion Dolby Vision (`select_video` only):**

Add `&dv=<quality>` to the video filter to also download a Dolby Vision companion alongside the main (non-DV) video. `<quality>` is `best`/`worst` (default `worst`) or an explicit height override (e.g. `&dv=720`):

| Value | Description |
|-------|-------------|
| `"best&dv"` | Best non-DV video + DV companion at worst quality |
| `"1080&dv=best"` | 1080p main video + DV companion at best quality, matched to 1080p when available |

When `<quality>` is `best`/`worst`, the companion is picked from DV streams at the **same resolution** as the main video (falling back to the nearest available resolution if none matches exactly). An explicit height override (`&dv=720`) bypasses this matching and always targets that height directly.

The DV track is muxed as an additional video track via mkvmerge.

## PROCESS (Post-Processing)

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

See `VibraVid/core/processors/helper/ex_sub.py` in the repository for conversion logic.

## REQUESTS

```json
{
  "REQUESTS": {
    "timeout": 30,
    "max_retry": 10,
    "use_proxy": false,
    "proxy_scope": "scrap+down",
    "proxy": {
      "http": "http://localhost:8888",
      "https": "http://localhost:8888"
    },
    "flaresolverr_url": "http://localhost:8191",
    "bypasser_url": "http://localhost:8192"
  }
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `timeout` | `30` | Request timeout in seconds |
| `max_retry` | `10` | Maximum retry attempts for failed requests |
| `use_proxy` | `false` | Enable proxy support for HTTP requests |
| `proxy_scope` | `scrap+down` | Where the proxy is applied: `scrap`, `down`, or `scrap+down` (see below) |
| `proxy.http` | — | Proxy URL for HTTP targets |
| `proxy.https` | — | Proxy URL for HTTPS targets |
| `flaresolverr_url` | `http://localhost:8191` | FlareSolverr endpoint used by the **lucida** music service to solve lucida.to's Cloudflare challenge. Keep the localhost default for local runs (sidecar on the same host); in Docker the `FLARESOLVERR_URL` env in `docker-compose.yml` overrides it to the `flaresolverr` service. |
| `bypasser_url` | `http://localhost:8192` | Endpoint of the **bypasser** sidecar that solves monochrome.tf's Cloudflare Turnstile widget for the **monochrome** Amazon Music download. **Required** — there is no in-process fallback. Keep the localhost default for local runs; in Docker the `BYPASSER_URL` env in `docker-compose.yml` overrides it to the `bypasser` service. |

**Proxy scope** — when `use_proxy` is `true`, `proxy_scope` decides *which* traffic goes through the proxy:

| Value | Effect |
|-------|--------|
| `scrap` | Only VibraVid's own HTTP client (search, metadata, manifests, DRM licenses) |
| `down` | Only the Velora download engine (media/subtitle segment downloads) |
| `scrap+down` | Both (default) |

Any invalid value falls back to `scrap+down`. Override per run from the CLI with `--proxy-scope scrap|down|scrap+down`.

**SOCKS5 support** — the `http`/`https` keys refer to the **target** URL scheme, not the proxy protocol. The value can be an HTTP **or** a SOCKS5 proxy URL. Use `socks5h://` (with the `h`) to resolve DNS through the proxy — recommended for geo-restricted sites and to avoid DNS leaks. Authentication is supported via `user:pass@`.

```json
"proxy": {
  "http":  "socks5h://localhost:1080",
  "https": "socks5h://user:pass@localhost:1080"
}
```

## DRM

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

### Remote CDM Services

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

### Local CDM Devices

To use local CDM device files instead of remote services, place them in the binary directory resolved at runtime:

- Default on Linux: `~/.local/bin/binary`
- Override with `VIBRAVID_BINARY_DIR` if you need a different path, for example `/home/user_name/.local/bin/binary`

- **Widevine:** `.wvd` file (from pywidevine)
- **PlayReady:** `.prd` file (from pyplayready)

Set `prefer_remote_cdm` to `false` and local devices will be picked up automatically.
