# Adding a New Service

## Directory layout

```
VibraVid/services/<name>/
├── __init__.py     # CLI entry point: search(), title_search(), --url resolution
├── client.py       # auth + low-level HTTP
├── scrapper.py     # metadata fetching (title/season/episode info)
└── downloader.py   # builds DASH_Downloader / HLS_Downloader and starts it
```

This split isn't enforced by the loader — only `__init__.py` with a `search()` function is
required

## Registration (auto-discovery)

`VibraVid/services/_base/site_loader.py` scans `VibraVid/services/*/​__init__.py` at startup.
Two module-level names are **required**:

```python
indice = 900          # sort position in the CLI/GUI site list — pick a free number, the
                       # loader renumbers everything to consecutive indices on the next run
_useFor = "Film_Serie"  # one of: Anime, Film_Serie, Serie, Song, Tor
```

A module that defines `_hide = True` is loaded but excluded from CLI/GUI/ARR listings —
useful while a service is still being built.

### Loading services from a remote repository

`imp_service` (in `Conf/config.json`, `DEFAULT.imp_service`) doesn't only accept local folder
paths — an entry can also be an **http(s) URL to a GitHub or Gitea repository** laid out the
same way as `VibraVid/services/` (one subfolder per service, each with its own `__init__.py`
defining `indice`/`_useFor`).

```json
"imp_service": ["default", "https://git.example.com/me/my-vibravid-sites"]
```

For a private repo, put credentials in the URL userinfo —
`https://user:token@host/owner/repo` — and pin a branch/tag with a `#ref` suffix
(`.../owner/repo#dev`); otherwise the repo's default branch is used.


## CLI options (`--url`, etc.)

Expose a `register_cli_args(parser) -> list[str]` function; the CLI calls it to add
site-specific flags and stores their values in `context_tracker.site_options`, keyed by
argparse `dest`:

```python
def register_cli_args(parser) -> list:
    group = parser.add_argument_group("MyService options (--site <n>)")
    group.add_argument("--url", dest="url", default=None, help="Direct title URL.")
    return ["url"]
```

Read it back in `search()`:

```python
def search(string_to_search=None, get_onlyDatabase=False, direct_item=None, selections=None, scrape_serie=None):
    if direct_item is None and not get_onlyDatabase:
        url = (context_tracker.site_options or {}).get("url")
        if url:
            direct_item = _resolve_url_to_item(url)
    return base_search(..., direct_item=direct_item, ...)
```

`base_search`/`base_process_search_result` (in `VibraVid/services/_base/site_search_manager.py`)
handle the rest — season/episode selection, table display, and dispatch to your
`download_film`/`download_series` functions. Reuse them rather than reimplementing the flow.

## Authentication (`client.py`)

There is no fixed auth pattern — services in this codebase use credential logins, static
tokens, and captured session cookies, sometimes more than one at once for different parts of
the same site. What generalizes:

- **Cache the session on disk** if login has any real cost (rate limits, a multi-step
  handshake). Use `VibraVid.utils.disk_cache` (`load`/`save`/`is_fresh`) keyed by service name,
  with an `expiry` timestamp — see `tubitv/client.py` for the reference shape. Skipping this
  means every CLI invocation re-authenticates, and some backends will start throttling or
  blocking logins after a handful of runs in quick succession.
- **A fresh HTTP client per request drops cookies set by a previous response.** If auth relies
  on a session cookie, capture it after login (`dict(http_client.cookies)`) and pass it
  explicitly (`cookies=...`) into every subsequent `create_client(...)` call — don't assume a
  cookie jar persists across separate `create_client` context managers.
- **A "web session" and an "app/API session" can be two unrelated login systems** even on the
  same domain. If an endpoint keeps returning empty/short-form data despite a valid-looking
  session, check whether it actually belongs to a different auth path than the one you're
  authenticating against — don't assume all endpoints on one domain share one cookie.
- For IP/region detection, reuse `VibraVid.utils.http_client.get_my_location()` (already
  cached to `.cache/ip.json`) instead of adding another geolocation call.

## Metadata (`scrapper.py`)

Populate `VibraVid.services._base.object.Episode`/`Season` (CLI path) or the GUI's
`Entries`/`Episode`/`Season` dataclasses (`GUI/searchapp/api/base.py`) with what the site
actually gives you — don't leave `image`/`slug`/`year` empty if the raw API response already
has them:

- **`image` on both search results and episodes** — the GUI's series "hero" background and
  episode thumbnails fall back to whatever `Entries.poster`/`Episode.image` are set to; an
  empty field there is a visible regression, not a cosmetic one.
- **`slug` must be a real, human-readable title slug — never an opaque content ID.** Several
  downstream lookups (TMDB year/poster resolution in particular) use `slug` as a search key;
  passing a content ID through as `slug` makes every such lookup silently miss.
- **Prefer the site's own release date/year field over a TMDB guess.** TMDB slug/name lookup
  is a *fallback* for when the site doesn't already carry the year — check the raw metadata
  response for the real field first.

## Building the download (`downloader.py`)

- If a license/session token is obtainable **up front** (before the manifest is even parsed),
  prefer passing `license_url` + `license_headers` to `DASH_Downloader`/`HLS_Downloader` over a
  per-challenge `license_request_fn` callback. The CDM pipeline
  (`VibraVid/core/drm/widevine.py` / `playready.py`) already does a plain POST with your headers
  — a callback is only needed when the license request itself requires per-challenge
  server-side computation that can't be front-loaded.
- **Always pass a real, non-empty `license_url`, even when a callback does the actual license
  HTTP request itself.** The vault key-storage step keys stored keys by `license_url` — leaving
  it `None`/unset (which becomes an empty string downstream) makes that save call fail with an
  HTTP 400, silently losing the key for future reuse even though the download itself succeeds.
- If the service exposes a quality ceiling gated by DRM scheme (e.g. one scheme tops out well
  below the other), pick `drm_preference` from the user's configured target quality rather than
  hardcoding one scheme — see `appletv/downloader.py`'s `resolve_drm_type`/`_config_wants_uhd`
  for the reference pattern (checks `DOWNLOAD.select_video` in `Conf/config.json`).

## GUI adapter

Add `GUI/searchapp/api/<name>.py` subclassing `GenericStreamingAPI` (`GUI/searchapp/api/generic.py`):

```python
from VibraVid.services.<name>.scrapper import GetSerieInfo
from .base import Entries
from .generic import GenericStreamingAPI

class MyService(GenericStreamingAPI):
    site_name = "<name>"
    log_label = "MyService"

    def _build_scraper(self, media_item: Entries):
        return GetSerieInfo(media_item.id)
```

This is auto-discovered the same way as the CLI module (`GUI/searchapp/api/__init__.py` scans
the package directory) — no separate registration step. `GenericStreamingAPI.search()` and
`get_series_metadata()` already call your CLI-side `search()`/scrapper for you; only override
`_build_entry`/`_map_episode` if the default field mapping doesn't fit.