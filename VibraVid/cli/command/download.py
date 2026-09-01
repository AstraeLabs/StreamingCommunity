# 10.12.25

import json
import logging
import shlex
import subprocess
from pathlib import Path

from rich.console import Console

from VibraVid.core.downloader.util._detect import (
    derive_output_path,
    detect_stream_type,
    parse_headers,
    parse_keys,
    parse_raw_key,
)
from VibraVid.core.drm.system import DRMType
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.tui.i18n import t
from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)
console = Console()


def _is_invalid_direct_url(url: str | None) -> bool:
    """Return True when the URL is clearly malformed and should be rejected early."""
    if not url:
        return True

    raw = str(url).strip()
    if not raw or any(ch.isspace() for ch in raw):
        return True

    try:
        from urllib.parse import urlsplit

        parsed = urlsplit(raw)
    except Exception:
        return True

    scheme = (parsed.scheme or "").lower()
    netloc = (parsed.netloc or "").strip()
    if scheme not in {"http", "https"}:
        return True
    if not netloc:
        return True
    return False


def handle_direct_download_json(args) -> tuple[bool, bool]:
    """Run every 'cmd' entry from a TRACKS_JSON file (see debug_track_json) when --down-json is passed."""
    path: str | None = getattr(args, "down_json", None)
    if not path:
        return False, False

    json_path = Path(path.strip())
    if not json_path.is_file():
        console.print(f"[red]--down-json file not found: {json_path}")
        return True, False

    try:
        entries = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        console.print(f"[red]Could not parse {json_path}: {exc}")
        return True, False

    if not isinstance(entries, list):
        console.print(f"[red]{json_path} does not contain a JSON array of track entries.")
        return True, False

    total = len(entries)
    console.print(f"[cyan]{total} command(s) to run from {json_path}")

    failures = []
    for i, entry in enumerate(entries, 1):
        name = (entry or {}).get("name") or f"entry {i}"
        cmd = (entry or {}).get("cmd") if isinstance(entry, dict) else None
        if not cmd:
            console.print(f"[yellow][{i}/{total}] Skipping '{name}': no 'cmd' field")
            continue

        console.print(f"\n[cyan][{i}/{total}] {name}")
        result = subprocess.run(shlex.split(cmd, posix=False))
        if result.returncode != 0:
            console.print(f"[yellow]  exited with code {result.returncode}")
            failures.append(name)

    return True, not failures


def handle_direct_download(args) -> bool:
    """Execute a direct URL download when --down is passed."""
    url: str | None = getattr(args, "down", None)
    if not url:
        return False, False

    url = url.strip()
    if _is_invalid_direct_url(url):
        console.print(f"[bold red]{t('direct_download_unsupported', default='URL not supported or invalid')}[/bold red]")
        return True, False

    if getattr(args, "meta_title", None):
        context_tracker.title = args.meta_title
    if getattr(args, "meta_type", None):
        context_tracker.media_type = args.meta_type
    if getattr(args, "meta_season", None) is not None:
        context_tracker.season = args.meta_season
    if getattr(args, "meta_episode", None) is not None:
        context_tracker.episode = args.meta_episode
    if getattr(args, "meta_site", None):
        context_tracker.site_name = args.meta_site

    headers = parse_headers(getattr(args, "headers", None))
    keys = parse_keys(getattr(args, "key", None))
    output = (getattr(args, "output", None) or "").strip() or None
    lic_url = (getattr(args, "license_url", None) or "").strip() or None
    lic_hdr = parse_headers(getattr(args, "license_headers", None))
    drm_pref = (getattr(args, "drm", None) or "auto").strip().lower()
    max_segs = getattr(args, "max_segments", None)
    max_time = getattr(args, "max_time", None)
    skip_content_check = bool(getattr(args, "skip_content_check", False))
    skip_sanitize = bool(getattr(args, "skip_sanitize", False))

    hls_method = (getattr(args, "hls_method", None) or "").strip().upper() or None
    try:
        hls_key = parse_raw_key(getattr(args, "hls_key", None))
        hls_iv = parse_raw_key(getattr(args, "hls_iv", None))
    except Exception as exc:
        logger.error(f"Could not decode --hls-key/--hls-iv: {exc}")
        console.print(f"[red]Could not decode --hls-key/--hls-iv: {exc}")
        return True, False

    for _name, _val in (("--hls-key", hls_key), ("--hls-iv", hls_iv)):
        if _val is not None and len(_val) != 16:
            logger.error(f"{_name} must decode to exactly 16 bytes (got {len(_val)})")
            console.print(f"[red]{_name} must decode to exactly 16 bytes (got {len(_val)})")
            return True, False

    # Map DRM string to DRMType constant (or None if not recognized)
    drm_choice = None
    if drm_pref in ("widevine", "wv", DRMType.WIDEVINE):
        drm_choice = DRMType.WIDEVINE
    elif drm_pref in ("playready", "pr", DRMType.PLAYREADY):
        drm_choice = DRMType.PLAYREADY

    # Build output path
    EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
    output = derive_output_path(url, output, EXTENSION_OUTPUT)

    # Normalise key arg: single string → one-element list
    key_arg = keys

    # Allow forcing the stream type (e.g. --type mp4)
    forced_type = (getattr(args, "stream_type", None) or "auto").lower()
    url_type = forced_type if forced_type != "auto" else detect_stream_type(url)

    # yt-dlp can handle many valid media URLs that do not match the built-in stream-type
    # heuristics. Fall back to the generic yt-dlp path instead of rejecting them early.
    if url_type == "unsupported":
        try:
            from VibraVid.core.direct_download.adapter import _resolve_direct_download_root, try_download_with_ytdlp_direct

            fallback_name = "" if output is None else str(Path(output).stem)
            result = try_download_with_ytdlp_direct(
                url=url,
                out_dir=str(_resolve_direct_download_root()),
                filename_base=fallback_name,
                source={
                    "headers": headers or None,
                    "cookies": None,
                    "no_playlist": False,
                },
                download_id=None,
                site_name=getattr(args, "meta_site", None),
                timeout=300,
                stream_output=True,
            )
            if result.get("path"):
                logger.info(f"yt-dlp generic fallback completed: {result['path']}")
                console.print(f"[green]Download completed: {result['path']}[/green]")
                return True, True
            if result.get("error"):
                logger.warning(f"yt-dlp generic fallback failed: {result['error']}")
                console.print(f"[yellow]yt-dlp fallback: {result['error']}[/yellow]")
                return True, False
        except Exception as exc:
            logger.warning(f"yt-dlp generic fallback raised an exception: {exc}", exc_info=True)
            console.print(f"[yellow]yt-dlp fallback failed: {exc}[/yellow]")
            return True, False

    # Lazy import to avoid circular dependency
    from VibraVid.core.downloader import DASH_Downloader, HLS_Downloader, ISM_Downloader, MP4_Downloader

    try:
        if url_type == "mp4":
            path, cancelled, error = MP4_Downloader(
                url=url,
                path=output,
                headers=headers or None,
                key=key_arg,
                check_content_type=not skip_content_check,
                sanitize_path=not skip_sanitize,
            )

            if error:
                logger.error(f"MP4 download error: {error}")
                console.print(f"[red]Download error: {error}")
                return True, False

        elif url_type == "hls":
            dl = HLS_Downloader(
                m3u8_url=url,
                headers=headers or None,
                license_url=lic_url,
                license_headers=lic_hdr or None,
                output_path=output,
                drm_preference=drm_choice or DRMType.WIDEVINE,
                key=key_arg,
                max_segments=max_segs,
                max_time=max_time,
                sanitize_path=not skip_sanitize,
                hls_method=hls_method,
                hls_key=hls_key,
                hls_iv=hls_iv,
            )
            path, cancelled, error = dl.start()

            if error:
                logger.error(f"HLS download error: {error}")
                console.print(f"[red]Download error: {error}")
                return True, False

        elif url_type == "dash":
            effective_drm = drm_choice or DRMType.WIDEVINE
            dl = DASH_Downloader(
                mpd_url=url,
                mpd_headers=headers or None,
                license_url=lic_url,
                license_headers=lic_hdr or None,
                output_path=output,
                drm_preference=effective_drm,
                key=key_arg,
                max_segments=max_segs,
                max_time=max_time,
                sanitize_path=not skip_sanitize,
            )
            path, cancelled, error = dl.start()

            if error:
                logger.error(f"DASH download error: {error}")
                console.print(f"[red]Download error: {error}")
                return True, False

        elif url_type == "ism":
            effective_drm = drm_choice or DRMType.PLAYREADY
            dl = ISM_Downloader(
                ism_url=url,
                headers=headers or None,
                license_url=lic_url,
                license_headers=lic_hdr or None,
                output_path=output,
                drm_preference=effective_drm,
                key=key_arg,
                max_segments=max_segs,
                max_time=max_time,
                sanitize_path=not skip_sanitize,
            )
            path, cancelled, error = dl.start()

            if error:
                logger.error(f"ISM download error: {error}")
                console.print(f"[red]Download error: {error}")
                return True, False

        elif url_type == "custom":
            from VibraVid.core.downloader import Generic_Downloader

            dl = Generic_Downloader(
                sources=[{"url": url, "protocol": "custom", "headers": headers or {}, "key": key_arg}],
                output_path=output,
                max_segments=max_segs,
                max_time=max_time,
            )
            path, cancelled, error = dl.start()

            if error:
                logger.error(f"Custom manifest download error: {error}")
                console.print(f"[red]Download error: {error}")
                return True, False

        else:
            logger.error(f"Unsupported stream type for URL: {url}")
            console.print("[red]Unsupported: could not detect a valid stream (m3u8/dash/hls/ism/custom).")
            return True, False

    except Exception as exc:
        logger.exception(f"Direct download failed: {exc}")
        console.print(f"[red]Download error: {exc}")
        return True, False

    ok = True
    if cancelled:
        console.print("[yellow]Download cancelled.")
        ok = False
    elif path:
        logger.info(f"Download completed: {path}")
    else:
        console.print("[red]Download failed.")
        ok = False

    return True, ok
