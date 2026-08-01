# 17.04.26
# by @nu00

import json
import logging
from urllib.parse import parse_qs, unquote, urlparse

from rich.console import Console

from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)
console = Console()

API_BASE = "https://api.cinezo.live"
REFERER = "https://player.cinezo.live/"


def _unwrap_proxy_url(url, headers=None):
    """
    Unwrap proxy URL (e.g. proxy.flikhub.net/m3u8-proxy.m3u8?url=...&headers=...).
    Returns (real_url, headers_dict).
    """
    if headers is None:
        headers = {}
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if "url" in params:
        real_url = unquote(params["url"][0])
        if "headers" in params:
            try:
                headers = json.loads(unquote(params["headers"][0]))
            except Exception:
                pass
        return real_url, headers
    return url, headers


def _subs_to_tracks(tracks) -> list:
    """Convert API 'tracks' list to other_tracks format for HLS_Downloader."""
    out = []
    for t in tracks or []:
        if not isinstance(t, dict) or not t.get("file"):
            continue
        out.append(
            {
                "type": "subtitle",
                "language": t.get("label") or "und",
                "name": t.get("label") or "Subtitle",
                "url": t["file"],
                "extension": "vtt",
            }
        )
    return out


def _resolve_source(source: dict):
    """Resolve one 'sources[]' entry into (stream_url, headers) or None if unreachable."""
    name = source.get("scraperName") or source.get("server") or "?"
    chosen_url = source.get("proxiedUrl") or source.get("url")
    if not chosen_url:
        return None

    headers = {}
    if source.get("referer"):
        headers["referer"] = source["referer"]
    if source.get("origin"):
        headers["origin"] = source["origin"]

    stream_url, headers = _unwrap_proxy_url(chosen_url, headers)
    if not stream_url or not stream_url.startswith("http"):
        console.print(f"[yellow][Cinezo] {name}: no valid URL")
        return None

    try:
        client = create_client(headers=headers)
        r = client.get(stream_url, timeout=15)
        client.close()
        if not r.ok:
            console.print(f"[yellow][Cinezo] {name}: HTTP {r.status_code}")
            return None
    except Exception as e:
        console.print(f"[yellow][Cinezo] {name}: exception → {e}")
        return None

    console.print(f"[green][Cinezo] {name}: OK")
    return stream_url, headers


def get_stream(tmdb_id: int, media_type: str, season: int | None = None, episode: int | None = None):
    """
    Returns (m3u8_url, headers, subtitle_tracks) for the given TMDB ID.

    media_type: 'movie' or 'tv'
    """
    api_headers = {"user-agent": get_userAgent(), "referer": REFERER}

    if media_type == "movie":
        url = f"{API_BASE}/movie/sources"
        params = {"tmdb": tmdb_id}
    else:
        if not season or not episode:
            season, episode = 1, 1
        url = f"{API_BASE}/tv/sources"
        params = {"tmdb": tmdb_id, "season": season, "episode": episode}

    client = create_client(headers=api_headers)
    r = client.get(url, params=params, timeout=30)
    client.close()
    if not r.ok:
        raise RuntimeError(f"[Cinezo] Sources API HTTP {r.status_code} for tmdb_id={tmdb_id}")

    data = r.json()
    sources = data.get("sources") or []
    subtitle_tracks = _subs_to_tracks(data.get("tracks"))

    if not sources:
        raise RuntimeError(f"[Cinezo] No sources available for tmdb_id={tmdb_id}")

    # Prefer sources the API itself marked accessible, but fall back to all of them.
    ordered = sorted(sources, key=lambda s: not s.get("accessible", True))

    for source in ordered:
        resolved = _resolve_source(source)
        if resolved is not None:
            stream_url, headers = resolved
            return stream_url, headers, subtitle_tracks

    raise RuntimeError(f"[Cinezo] No working server found for tmdb_id={tmdb_id}")
