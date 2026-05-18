# 17.04.26

import json
import base64
import hashlib
import logging
import threading
import concurrent.futures
from urllib.parse import urlparse, parse_qs, unquote

from rich.console import Console

from VibraVid.utils.http_client import create_client, get_userAgent

logger  = logging.getLogger(__name__)
console = Console()

# Servers extracted from player.cinezo.live JS bundle (API endpoint deprecated)
SUBS_API_URL = "https://player.cinezo.live/api/subtitles"
HARDCODED_SERVERS = [
    {"name": "Alpha",        "movieApiUrl": "https://api.tulnex.com/111movies/Alpha/movie/{id}",          "tvApiUrl": "https://api.tulnex.com/111movies/Alpha/tv/{id}/{season}/{episode}"},
    {"name": "Bravo",        "movieApiUrl": "https://api.tulnex.com/111movies/Bravo/movie/{id}",          "tvApiUrl": "https://api.tulnex.com/111movies/Bravo/tv/{id}/{season}/{episode}"},
    {"name": "NgFlix",       "movieApiUrl": "https://api.tulnex.com/111movies/NgFlix/movie/{id}",         "tvApiUrl": "https://api.tulnex.com/111movies/NgFlix/tv/{id}/{season}/{episode}"},
    {"name": "Icefy",        "movieApiUrl": "https://api.tulnex.com/icefy/movie/{id}",                    "tvApiUrl": "https://api.tulnex.com/icefy/tv/{id}/{season}/{episode}"},
    {"name": "MovieBox",     "movieApiUrl": "https://api.tulnex.com/moviebox/movie/{id}",                 "tvApiUrl": "https://api.tulnex.com/moviebox/tv/{id}/{season}/{episode}"},
    {"name": "Onion",        "movieApiUrl": "https://api.tulnex.com/onion/movie/{id}",                    "tvApiUrl": "https://api.tulnex.com/onion/tv/{id}/{season}/{episode}"},
    {"name": "AllMovies",    "movieApiUrl": "https://api.tulnex.com/provider/allmovies/movie/{id}?lang=english", "tvApiUrl": "https://api.tulnex.com/provider/allmovies/tv/{id}/{season}/{episode}?lang=english"},
    {"name": "VidLink",      "movieApiUrl": "https://api.tulnex.com/provider/vidlink/movie/{id}",         "tvApiUrl": "https://api.tulnex.com/provider/vidlink/tv/{id}/{season}/{episode}"},
    {"name": "Tik",          "movieApiUrl": "https://api.tulnex.com/tik/movie/{id}",                      "tvApiUrl": "https://api.tulnex.com/tik/tv/{id}/{season}/{episode}"},
    {"name": "UniqueStream", "movieApiUrl": "https://api.tulnex.com/uniquestream/movie/{id}",             "tvApiUrl": "https://api.tulnex.com/uniquestream/tv/{id}/{season}/{episode}"},
    {"name": "VaPlayer",     "movieApiUrl": "https://api.tulnex.com/vaplayer/movie/{id}",                 "tvApiUrl": "https://api.tulnex.com/vaplayer/tv/{id}/{season}/{episode}"},
    {"name": "Neon",         "movieApiUrl": "https://api.tulnex.com/ve/server/Neon/movie/{id}",           "tvApiUrl": "https://api.tulnex.com/ve/server/Neon/tv/{id}/{season}/{episode}"},
    {"name": "Yoru",         "movieApiUrl": "https://api.tulnex.com/ve/server/Yoru/movie/{id}",           "tvApiUrl": "https://api.tulnex.com/ve/server/Yoru/tv/{id}/{season}/{episode}"},
    {"name": "VEdge",        "movieApiUrl": "https://api.tulnex.com/vidfast/movie/vedge/{id}",            "tvApiUrl": "https://api.tulnex.com/vidfast/tv/vedge/{id}/{season}/{episode}"},
    {"name": "VFast",        "movieApiUrl": "https://api.tulnex.com/vidfast/movie/vfast/{id}",            "tvApiUrl": "https://api.tulnex.com/vidfast/tv/vfast/{id}/{season}/{episode}"},
    {"name": "VidZee",       "movieApiUrl": "https://api.tulnex.com/vidzee/movie/{id}?server=0",          "tvApiUrl": "https://api.tulnex.com/vidzee/tv/{id}/{season}/{episode}?server=0"},
]


def _pbkdf2(password: str, salt, iterations: int, length: int, hash_name: str) -> bytes:
    if isinstance(salt, str):
        salt = salt.encode('utf-8')

    return hashlib.pbkdf2_hmac(hash_name.lower().replace('-', ''), password.encode('utf-8'), salt, iterations, dklen=length)


def _aes_cbc_decrypt(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    from Cryptodome.Cipher import AES
    from Cryptodome.Util.Padding import unpad
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(ciphertext), 16)


def _b64decode_safe(s: str) -> bytes:
    pad = 4 - len(s) % 4
    if pad < 4:
        s += '=' * pad
    return base64.b64decode(s)


def decode_payload(payload: str) -> str:
    """
    Decodes the 4-layer encrypted payload from api.tulnex.com.

    Layer 4 (v): split on '|', base64-decode data part -> L3 string
    Layer 3 (h): AES-CBC decrypt with PBKDF2-SHA512 key
    Layer 2:     base64-decode -> binary string -> chars
    Layer 1:     XOR with PBKDF2-SHA256 key
    """
    # L4: split on '|'
    sep = payload.index('|')
    data_b64 = payload[sep + 1:]
    l3_string = _b64decode_safe(data_b64).decode('utf-8')

    # L3: AES-CBC decrypt
    parts = l3_string.split('.')
    if len(parts) != 3:
        raise ValueError(f"L3: expected 3 parts, got {len(parts)}")

    iv_b64, key_material_b64, cipher_b64 = parts
    iv         = _b64decode_safe(iv_b64)
    salt       = _b64decode_safe(key_material_b64)
    aes_key    = _pbkdf2("Sn00pD0g#L3_AES_S3cur3K3y@2026$sex", salt, 100_000, 32, 'sha512')
    ciphertext = _b64decode_safe(cipher_b64)
    intermediate_b64 = _aes_cbc_decrypt(ciphertext, aes_key, iv).decode('utf-8')

    # L2: atob(r).split(" ").map(parseInt(_, 2)).join("")
    binary_str = _b64decode_safe(intermediate_b64).decode('utf-8', errors='replace')
    hex_str = ''.join(
        chr(int(b, 2)) for b in binary_str.split(' ') if b.strip()
    )

    # L1: XOR with PBKDF2-SHA256 key
    xor_key  = _pbkdf2("Sn00pD0g#L1_X0R_M4st3rK3y!2026sex", "xK9!mR2@pL5#nQ8sex", 50_000, 32, 'sha256')
    raw_bytes = bytes.fromhex(hex_str)
    final    = bytes(raw_bytes[i] ^ xor_key[i % len(xor_key)] for i in range(len(raw_bytes)))

    return final.decode('utf-8')


def _subs_to_tracks(subs) -> list:
    """Convert API subtitle list to other_tracks format for HLS_Downloader."""
    tracks = []
    for s in (subs or []):
        if not isinstance(s, dict) or not s.get('url'):
            continue
        tracks.append({
            "type":      "subtitle",
            "language":  s.get("language") or "und",
            "name":      s.get("display") or s.get("name") or s.get("language") or "Subtitle",
            "url":       s["url"],
            "extension": "vtt",
        })
    return tracks


def _unwrap_proxy_url(url, headers=None):
    """
    Unwrap proxy URL (e.g. pronhub.tulnex.com/m3u8-proxy.m3u8?url=...&headers=...).
    Returns (real_url, headers_dict).
    """
    if headers is None:
        headers = {}
    parsed = urlparse(url)
    params = parse_qs(parsed.query)
    if 'url' in params:
        real_url = unquote(params['url'][0])
        if 'headers' in params:
            try:
                headers = json.loads(unquote(params['headers'][0]))
            except Exception:
                pass
        return real_url, headers
    return url, headers


def _parse_stream_result(raw: str):
    """
    Parse the decoded payload. Handles four formats:
      1. JSON string  -> direct or proxy URL
      2. {"url": ..., "headers": ..., "subtitles": [...]}
      2b. {"sources": [{"url": ...}], "subtitles": [...]}
      3. {"server": ..., "streams": [{...}], "subtitles": [...]}
    Returns (m3u8_url, headers_dict, subtitle_tracks).
    """
    try:
        cleaned = json.loads(raw)
    except Exception:
        cleaned = raw.strip().strip('"')

    headers  = {}
    raw_subs = []

    if isinstance(cleaned, dict) and 'streams' in cleaned:
        # Format 3: {"server": "...", "streams": [...], "subtitles": [...]}
        streams  = cleaned.get('streams') or []
        raw_subs = cleaned.get('subtitles') or []
        if not streams:
            return '', {}, []
        first = streams[0]
        if isinstance(first, dict):
            url      = first.get('url') or first.get('stream') or ''
            headers  = first.get('headers') or {}
            raw_subs = raw_subs or first.get('subtitles') or []
        else:
            url = str(first) if first else ''
    elif isinstance(cleaned, dict) and 'sources' in cleaned:
        # Format 2b: {"sources": [{"url": ...}], "subtitles": [...]}
        sources  = cleaned.get('sources') or []
        raw_subs = cleaned.get('subtitles') or []
        if not sources:
            return '', {}, []
        first = sources[0] if isinstance(sources, list) else sources
        if isinstance(first, dict):
            url     = first.get('url') or first.get('file') or first.get('stream') or ''
            headers = first.get('headers') or {}
        else:
            url = str(first) if first else ''
    elif isinstance(cleaned, dict):
        # Format 2: {"url": ..., "headers": ..., "subtitles": [...]}
        url      = cleaned.get('url') or cleaned.get('stream') or ''
        headers  = cleaned.get('headers') or {}
        raw_subs = cleaned.get('subtitles') or []
    else:
        # Format 1: plain string — no subtitles
        url = cleaned or ''

    # Unwrap proxy URL
    real_url, headers = _unwrap_proxy_url(url, headers)

    return real_url, headers, _subs_to_tracks(raw_subs)


def get_servers():
    """Return hardcoded server list (old API endpoint deprecated)."""
    return HARDCODED_SERVERS


def _try_server(server, tmdb_id, media_type, season, episode, api_headers, found_event):
    """Query a single server. Returns (stream_url, headers, subtitle_tracks) or None."""
    name = server.get('name', '?')
    if found_event.is_set():
        return None
    try:
        if media_type == 'movie':
            url = server.get('movieApiUrl', '').replace('{id}', str(tmdb_id))
        else:
            url = (server.get('tvApiUrl', '').replace('{id}', str(tmdb_id)).replace('{season}', str(season)).replace('{episode}', str(episode)))

        if not url or found_event.is_set():
            console.print(f"[yellow][Cinezo] {name}: no URL template")
            return None

        client = create_client(headers=api_headers)
        r = client.get(url, timeout=20)
        client.close()
        if not r.ok or found_event.is_set():
            console.print(f"[yellow][Cinezo] {name}: HTTP {r.status_code}")
            return None

        data = r.json()

        # Handle error responses
        if data.get('success') is False or data.get('error'):
            err_msg = data.get('error', 'unknown error')
            console.print(f"[yellow][Cinezo] {name}: {err_msg}")
            return None

        # Format A: non-encrypted (e.g. Icefy) — has 'sources' directly
        if 'sources' in data and not data.get('payload'):
            sources = data.get('sources') or []
            raw_subs = data.get('subtitles') or []
            if not sources:
                console.print(f"[yellow][Cinezo] {name}: no sources in response")
                return None
            first = sources[0] if isinstance(sources, list) else sources
            if isinstance(first, dict):
                stream_url = first.get('url') or first.get('file') or first.get('stream') or ''
                stream_headers = first.get('headers') or {}
            else:
                stream_url = str(first)
                stream_headers = {}
            subtitle_tracks = _subs_to_tracks(raw_subs)
            if stream_url and stream_url.startswith('http'):
                stream_url, stream_headers = _unwrap_proxy_url(stream_url, stream_headers)
                sub_info = f", {len(subtitle_tracks)} sub(s)" if subtitle_tracks else ""
                console.print(f"[green][Cinezo] {name}: OK (direct){sub_info}")
                return stream_url, stream_headers, subtitle_tracks
            console.print(f"[yellow][Cinezo] {name}: no valid URL in sources")
            return None

        # Format C: VidLink-style nested data (source/data/stream)
        if 'data' in data and not data.get('payload'):
            try:
                inner = data['data']
                if isinstance(inner, dict) and 'data' in inner:
                    inner = inner['data']
                stream_info = inner.get('stream') or {}
                playlist = stream_info.get('playlist') or stream_info.get('url') or ''
                captions = stream_info.get('captions') or []
                stream_headers = {}
                if playlist:
                    playlist, stream_headers = _unwrap_proxy_url(playlist)
                sub_tracks = []
                for cap in captions:
                    if isinstance(cap, dict) and cap.get('url'):
                        sub_tracks.append({
                            "type": "subtitle",
                            "language": cap.get("language", "und"),
                            "name": cap.get("language", "Subtitle"),
                            "url": cap["url"],
                            "extension": "vtt",
                        })
                if playlist and playlist.startswith('http'):
                    sub_info = f", {len(sub_tracks)} sub(s)" if sub_tracks else ""
                    console.print(f"[green][Cinezo] {name}: OK (vidlink){sub_info}")
                    return playlist, stream_headers, sub_tracks
                console.print(f"[yellow][Cinezo] {name}: no playlist in nested data")
                return None
            except Exception as e:
                console.print(f"[yellow][Cinezo] {name}: failed parsing nested data: {e}")
                return None

        # Format B: encrypted payload
        if not data.get('payload'):
            console.print(f"[yellow][Cinezo] {name}: no payload, keys={list(data.keys())}")
            return None

        raw = decode_payload(data['payload'])
        stream_url, stream_headers, subtitle_tracks = _parse_stream_result(raw)

        if stream_url and stream_url.startswith('http'):
            sub_info = f", {len(subtitle_tracks)} sub(s)" if subtitle_tracks else ""
            console.print(f"[green][Cinezo] {name}: OK{sub_info}")
            logger.info(f"[Cinezo] Server '{name}' OK: {stream_url[:60]}")
            return stream_url, stream_headers, subtitle_tracks

        console.print(f"[yellow][Cinezo] {name}: decoded but no valid URL \u2192 {str(stream_url)[:80]}")

    except Exception as e:
        import traceback
        console.print(f"[red][Cinezo] {name}: exception \u2192 {e}\n{traceback.format_exc()}")
        logger.debug(f"[Cinezo] Server '{name}' failed: {e}", exc_info=True)
    return None


def get_stream(tmdb_id: int, media_type: str, season: int = None, episode: int = None):
    """
    Returns (m3u8_url, headers, subtitle_tracks) for the given TMDB ID.
    Queries all servers in parallel and returns the first successful result.

    media_type: 'movie' or 'tv'
    """
    servers = get_servers()
    if not servers:
        raise RuntimeError(f"[Cinezo] No servers available for tmdb_id={tmdb_id}")

    api_headers = {'user-agent': get_userAgent(), 'referer': 'https://player.cinezo.live/embed/'}
    if media_type == 'tv' and (not season or not episode):
        season, episode = 1, 1

    found_event = threading.Event()

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(servers)) as executor:
        futures = {
            executor.submit(_try_server, server, tmdb_id, media_type, season, episode, api_headers, found_event): server
            for server in servers
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result is not None:
                found_event.set()
                return result  # (stream_url, headers, subtitle_tracks)

    raise RuntimeError(f"[Cinezo] No working server found for tmdb_id={tmdb_id}")
