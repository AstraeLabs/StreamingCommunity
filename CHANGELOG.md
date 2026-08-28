# Changelog

## Setup / Binaries
* Removed **Bento4 (mp4decrypt)** and **Shaka Packager** from installation.
* Dropped the **FlareSolverr** sidecar.

## Decryptor / DRM
* `DRMManager` now ignores **all-zero (unusable)** keys returned by a vault DB and stores keys **idempotently**.
* Widevine and PlayReady CDM extraction **skip gracefully** when there is no `license_url` and no custom license-request function.

## Downloader / Muxing
* Muxing: extract **embedded CEA-608/708 closed captions** from the video elementary stream as `mov_text` subtitles, deduplicating 608 vs 708 per language.

## Manifest Parsing
* DASH: `SegmentURL` `mediaRange` is now honored even when a `media` attribute is also present (previously it was dropped, fetching the whole file).

## Services
* **Discovery+**: added **Widevine (L3) / ClearKey fallback** when PlayReady is unavailable.
* **Mediaset Infinity**: added episode keyframe and season hero/backdrop image URLs.
* **StreamingCommunity**: `--skip-ts` moved to a per-site CLI option (`register_cli_args`) instead of the global `DEFAULT.skip_ts_versions` config.
* CLI multi-select (`TVShowManager` / `get_select_title`): now supports `*` , comma lists and ranges (`1-3`, `3-*`), returning a list of items and processing each in turn.

## GUI / Web
* New **Logs** page: nav link, `cinema_logs.html`, `views/logs_view.py`, `/logs/` plus `api/logs/list/` and `api/logs/content/` endpoints.

## CLI / TUI
* `TVShowManager.run()` dropped its `force_int_input` / `max_int_input` parameters, superseded by the new `*` / comma / range (`1-3`, `3-*`) selection parser.