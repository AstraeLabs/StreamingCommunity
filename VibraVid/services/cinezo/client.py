# Cinezo.net client
# Stream via api.cinezo.net → api.tulnex.com → 4-layer decode → HLS URL

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

API_SERVERS_URL = "https://api.cinezo.net/api/servers"
_servers_cache  = None


def _pbkdf2(password: str, salt, iterations: int, length: int, hash_name: str) -> bytes:
    if isinstance(salt, str):
        salt = salt.encode('utf-8')
    return hashlib.pbkdf2_hmac(
        hash_name.lower().replace('-', ''),
        password.encode('utf-8'),
        salt,
        iterations,
        dklen=length
    )


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

    Layer 4 (v): split on '|', base64-decode data part → L3 string
    Layer 3 (h): AES-CBC decrypt with PBKDF2-SHA512 key
    Layer 2:     base64-decode → binary string → chars
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
    aes_key    = _pbkdf2("Sn00pD0g#L3_AES_S3cur3K3y@2025$", salt, 100_000, 32, 'sha512')
    ciphertext = _b64decode_safe(cipher_b64)
    intermediate_b64 = _aes_cbc_decrypt(ciphertext, aes_key, iv).decode('utf-8')

    # L2: atob(r).split(" ").map(parseInt(_, 2)).join("")
    binary_str = _b64decode_safe(intermediate_b64).decode('utf-8', errors='replace')
    hex_str = ''.join(
        chr(int(b, 2)) for b in binary_str.split(' ') if b.strip()
    )

    # L1: XOR with PBKDF2-SHA256 key
    xor_key  = _pbkdf2("Sn00pD0g#L1_X0R_M4st3rK3y!2025", "xK9!mR2@pL5#nQ8", 50_000, 32, 'sha256')
    raw_bytes = bytes.fromhex(hex_str)
    final    = bytes(raw_bytes[i] ^ xor_key[i % len(xor_key)] for i in range(len(raw_bytes)))

    return final.decode('utf-8')


def _parse_stream_result(raw: str):
    """
    Parse the decoded payload. Handles three formats:
      1. JSON string  → direct or proxy URL
      2. {"url": ..., "headers": ...}  → direct/proxy URL + headers
      3. {"server": ..., "streams": [{...}]}  → extract first stream entry
    Returns (m3u8_url, headers_dict).
    """
    try:
        cleaned = json.loads(raw)
    except Exception:
        cleaned = raw.strip().strip('"')

    headers = {}

    if isinstance(cleaned, dict) and 'streams' in cleaned:
        # Format 3: {"server": "...", "streams": [...]}
        streams = cleaned.get('streams') or []
        if not streams:
            return '', {}
        first = streams[0]
        if isinstance(first, dict):
            url     = first.get('url') or first.get('stream') or ''
            headers = first.get('headers') or {}
        else:
            url = str(first) if first else ''
    elif isinstance(cleaned, dict):
        # Format 2: {"url": ..., "headers": ...}
        url     = cleaned.get('url') or cleaned.get('stream') or ''
        headers = cleaned.get('headers') or {}
    else:
        # Format 1: plain string
        url = cleaned or ''

    # Unwrap proxy URL: prxy.tulnex.com/proxy?url=...&headers=...
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    if 'url' in params:
        real_url = unquote(params['url'][0])
        if 'headers' in params:
            try:
                headers = json.loads(unquote(params['headers'][0]))
            except Exception:
                pass
    else:
        real_url = url

    return real_url, headers


def get_servers():
    """Fetch and cache server list from api.cinezo.net."""
    global _servers_cache
    if _servers_cache:
        return _servers_cache
    try:
        r = create_client(headers={'user-agent': get_userAgent(),
                                   'referer': 'https://www.cinezo.net/'}).get(
            API_SERVERS_URL, timeout=10)
        r.raise_for_status()
        _servers_cache = r.json()
        return _servers_cache
    except Exception as e:
        logger.error(f"[Cinezo] Failed to fetch servers: {e}")
        return []


def _try_server(server, tmdb_id, media_type, season, episode, api_headers, found_event):
    """Query a single server. Returns (stream_url, headers) or None."""
    name = server.get('name', '?')
    if found_event.is_set():
        return None
    try:
        if media_type == 'movie':
            url = server.get('movieApiUrl', '').replace('{id}', str(tmdb_id))
        else:
            url = (server.get('tvApiUrl', '')
                   .replace('{id}', str(tmdb_id))
                   .replace('{season}', str(season))
                   .replace('{episode}', str(episode)))

        if not url or found_event.is_set():
            console.print(f"[yellow][Cinezo] {name}: no URL template")
            return None

        r = create_client(headers=api_headers).get(url, timeout=20)
        if not r.ok or found_event.is_set():
            console.print(f"[yellow][Cinezo] {name}: HTTP {r.status_code}")
            return None

        data = r.json()
        v = data.get('v')
        if v != 4 or not data.get('payload'):
            console.print(f"[yellow][Cinezo] {name}: unexpected payload version v={v}, keys={list(data.keys())}")
            return None

        raw = decode_payload(data['payload'])
        stream_url, stream_headers = _parse_stream_result(raw)

        if stream_url and stream_url.startswith('http'):
            console.print(f"[green][Cinezo] {name}: OK")
            logger.info(f"[Cinezo] Server '{name}' OK: {stream_url[:60]}")
            return stream_url, stream_headers

        console.print(f"[yellow][Cinezo] {name}: decoded but no valid URL → {str(stream_url)[:80]}")

    except Exception as e:
        import traceback
        console.print(f"[red][Cinezo] {name}: exception → {e}\n{traceback.format_exc()}")
        logger.debug(f"[Cinezo] Server '{name}' failed: {e}", exc_info=True)
    return None


def get_stream(tmdb_id: int, media_type: str,
               season: int = None, episode: int = None):
    """
    Returns (m3u8_url, headers) for the given TMDB ID.
    Queries all servers in parallel and returns the first successful result.

    media_type: 'movie' or 'tv'
    """
    servers = get_servers()
    if not servers:
        raise RuntimeError(f"[Cinezo] No servers available for tmdb_id={tmdb_id}")

    api_headers = {'user-agent': get_userAgent(), 'referer': 'https://api.cinezo.net/'}
    if media_type == 'tv' and (not season or not episode):
        season, episode = 1, 1

    found_event = threading.Event()

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(servers)) as executor:
        futures = {
            executor.submit(_try_server, server, tmdb_id, media_type,
                            season, episode, api_headers, found_event): server
            for server in servers
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result is not None:
                found_event.set()
                return result

    raise RuntimeError(f"[Cinezo] No working server found for tmdb_id={tmdb_id}")
