# 10.12.25

import logging
from pathlib import Path
from typing import Optional

from rich.console import Console

from VibraVid.utils import config_manager

logger = logging.getLogger(__name__)
console = Console()


def _detect_url_type(url: str) -> str:
    """
    Guess the stream type from the URL path.
    Falls back to a HEAD request when the extension is ambiguous.

    Returns: 'mp4' | 'hls' | 'dash' | 'ism'
    """
    clean = url.lower().split('?')[0].rstrip('/')

    if clean.endswith(('.mpd', '.mpp')):
        return 'dash'
    if clean.endswith('.ism') or clean.endswith('.ism/manifest'):
        return 'ism'
    if clean.endswith(('.m3u8', '.m3u')):
        return 'hls'
    if clean.endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm', '.ts', '.m4v')):
        return 'mp4'

    # Ambiguous extension: probe with a HEAD request
    try:
        from VibraVid.utils.http_client import create_client
        with create_client(timeout=10, follow_redirects=True) as client:
            resp = client.head(url)
            ct = (resp.headers.get('content-type') or '').lower()

        if 'mpd' in ct or 'dash' in ct:
            return 'dash'
        if 'mpegurl' in ct or 'm3u8' in ct:
            return 'hls'
        if 'mp4' in ct or 'video' in ct or 'octet-stream' in ct:
            return 'mp4'
        if 'silverlight' in ct or 'ism' in ct:
            return 'ism'
    except Exception as exc:
        logger.debug(f"HEAD probe failed for type detection: {exc}")

    # Last resort: assume HLS (most common streaming format)
    logger.warning(f"Could not detect stream type for URL, defaulting to HLS: {url}")
    return 'hls'


def _parse_headers(headers_list: Optional[list]) -> dict:
    """
    Convert ['Key: Value', 'Key2:Value2', ...] into a plain dict.
    Both 'Key: Value' and 'Key:Value' are accepted.
    """
    result = {}
    for entry in (headers_list or []):
        if ':' in entry:
            k, v = entry.split(':', 1)
            result[k.strip()] = v.strip()
        else:
            logger.warning(f"Ignoring malformed --headers entry (expected 'Key:Value'): {entry!r}")
    return result


def _parse_keys(key_list: Optional[list]) -> Optional[list]:
    """
    Return a list of 'kid:key' strings or None if empty.
    Each element comes from one --key argument.
    """
    if not key_list:
        return None
    cleaned = [k.strip() for k in key_list if k.strip()]
    return cleaned if cleaned else None


def handle_direct_download(args) -> bool:
    """
    Execute a direct URL download when --down is passed.
    Returns True if handled (caller should return immediately), False otherwise.
    """
    url: Optional[str] = getattr(args, 'down', None)
    if not url:
        return False

    url = url.strip()
    headers   = _parse_headers(getattr(args, 'headers', None))
    keys      = _parse_keys(getattr(args, 'key', None))
    output    = (getattr(args, 'output', None) or '').strip() or None
    lic_url   = (getattr(args, 'license_url', None) or '').strip() or None
    lic_hdr   = _parse_headers(getattr(args, 'license_headers', None))
    drm_pref  = (getattr(args, 'drm', None) or 'auto').strip().lower()

    # Build output path
    EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
    if not output:
        # Try to derive a filename from the URL
        from urllib.parse import urlparse
        url_path = urlparse(url).path.rstrip('/')
        stem = Path(url_path).stem or 'download'
        output = f"{stem}.{EXTENSION_OUTPUT}"
    elif not Path(output).suffix:
        output = f"{output}.{EXTENSION_OUTPUT}"

    # Normalise key arg: single string → one-element list kept as list for
    # the segment-based downloaders; MP4 doesn't use keys so it's ignored.
    key_arg = keys

    url_type = _detect_url_type(url)
    console.print(
        f"\n[cyan]  Direct download[/cyan] — detected [bold]{url_type.upper()}[/bold]\n"
        f"  [dim]url :[/dim] {url}\n"
        f"  [dim]out :[/dim] {output}" +
        (f"\n  [dim]drm :[/dim] {drm_pref}" if lic_url or keys else "")
    )

    # Lazy import to avoid circular dependency
    from VibraVid.core.downloader import MP4_Downloader, HLS_Downloader, DASH_Downloader, ISM_Downloader

    try:
        if url_type == 'mp4':
            path, cancelled = MP4_Downloader(
                url=url,
                path=output,
                headers_=headers or None,
            )

        elif url_type == 'hls':
            dl = HLS_Downloader(
                m3u8_url=url,
                headers=headers or None,
                license_url=lic_url,
                license_headers=lic_hdr or None,
                output_path=output,
                drm_preference=drm_pref if drm_pref != 'auto' else 'widevine',
                key=key_arg,
            )
            path, cancelled = dl.start()

        elif url_type == 'dash':
            effective_drm = drm_pref if drm_pref in ('widevine', 'playready') else 'widevine'
            dl = DASH_Downloader(
                mpd_url=url,
                mpd_headers=headers or None,
                license_url=lic_url,
                license_headers=lic_hdr or None,
                output_path=output,
                drm_preference=effective_drm,
                key=key_arg,
            )
            path, cancelled = dl.start()

        elif url_type == 'ism':
            effective_drm = drm_pref if drm_pref in ('widevine', 'playready') else 'playready'
            dl = ISM_Downloader(
                ism_url=url,
                headers=headers or None,
                license_url=lic_url,
                license_headers=lic_hdr or None,
                output_path=output,
                drm_preference=effective_drm,
                key=key_arg,
            )
            path, cancelled = dl.start()

        else:
            console.print(f"[red]Unknown stream type: {url_type}")
            return True

    except Exception as exc:
        logger.exception(f"Direct download failed: {exc}")
        console.print(f"[red]Download error: {exc}")
        return True

    if cancelled:
        console.print("[yellow]Download cancelled.")
    elif path:
        console.print(f"\n[green]Saved → {path}")
    else:
        console.print("[red]Download failed.")

    return True
