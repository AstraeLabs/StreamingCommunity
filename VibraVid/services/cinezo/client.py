# Cinezo.net client
# Stream via api.cinezo.net → api.tulnex.com → 4-layer decode → HLS URL

import json
import base64
import hashlib
import logging
from urllib.parse import urlparse, parse_qs, unquote

from VibraVid.utils.http_client import create_client, get_userAgent

logger = logging.getLogger(__name__)

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
    Parse the decoded payload.
    Returns (m3u8_url, headers_dict).
    """
    # Raw might be a JSON string or a proxy URL string
    try:
        cleaned = json.loads(raw)
    except Exception:
        cleaned = raw.strip().strip('"')

    if isinstance(cleaned, dict):
        url     = cleaned.get('url') or cleaned.get('stream') or ''
        headers = cleaned.get('headers') or {}
    else:
        url = cleaned

    # If URL is a proxy URL (prxy.tulnex.com/proxy?url=...&headers=...)
    parsed  = urlparse(url)
    params  = parse_qs(parsed.query)
    headers = {}

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


def get_stream(tmdb_id: int, media_type: str,
               season: int = None, episode: int = None):
    """
    Returns (m3u8_url, headers) for the given TMDB ID.
    Tries each server until one succeeds.

    media_type: 'movie' or 'tv'
    """
    servers = get_servers()
    api_headers = {'user-agent': get_userAgent(), 'referer': 'https://api.cinezo.net/'}

    for server in servers:
        try:
            if media_type == 'movie':
                url = server.get('movieApiUrl', '').replace('{id}', str(tmdb_id))
            else:
                if not season or not episode:
                    season, episode = 1, 1
                url = (server.get('tvApiUrl', '')
                       .replace('{id}', str(tmdb_id))
                       .replace('{season}', str(season))
                       .replace('{episode}', str(episode)))

            if not url:
                continue

            r = create_client(headers=api_headers).get(url, timeout=8)
            if not r.ok:
                continue

            data = r.json()
            if data.get('v') != 4 or not data.get('payload'):
                continue

            raw = decode_payload(data['payload'])
            stream_url, stream_headers = _parse_stream_result(raw)

            if stream_url and stream_url.startswith('http'):
                logger.info(f"[Cinezo] Server '{server.get('name')}' OK: {stream_url[:60]}")
                return stream_url, stream_headers

        except Exception as e:
            logger.debug(f"[Cinezo] Server '{server.get('name')}' failed: {e}")
            continue

    raise RuntimeError(f"[Cinezo] No working server found for tmdb_id={tmdb_id}")
