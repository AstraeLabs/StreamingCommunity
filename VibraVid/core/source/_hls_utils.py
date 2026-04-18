# 10.04.26

import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlparse


def hls_base_url(playlist_url: str) -> str:
    """Return the base URL directory for a given HLS playlist URL."""
    p = urlparse(playlist_url)
    path = p.path.rsplit("/", 1)[0]
    return f"{p.scheme}://{p.netloc}{path}/"


def parse_hls_variant_playlist(content: str, base_url: str) -> Tuple[List[Dict], Optional[str]]:
    """
    Parse an HLS *variant* (media) playlist.

    Returns a tuple of:
        - List of segment dicts: {"url", "number", "enc"}
        - Optional init segment URL (from EXT-X-MAP)
    """
    segments: List[Dict] = []
    current_enc: Dict = {"method": "NONE", "key_url": None, "iv": None}
    init_url: Optional[str] = None
    seg_num = 0
    map_count = 0

    lines = content.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("#EXT-X-KEY:"):
            method_m = re.search(r"METHOD=([^,\s\"]+)", line)
            uri_m = re.search(r'URI="([^"]+)"', line)
            iv_m = re.search(r"IV=0x([0-9a-fA-F]+)", line, re.I)
            current_enc = {
                "method":  method_m.group(1).upper() if method_m else "NONE",
                "key_url": urljoin(base_url, uri_m.group(1)) if uri_m else None,
                "iv":      iv_m.group(1).lower().zfill(32) if iv_m else None,
            }

        elif line.startswith("#EXT-X-MAP:"):
            map_count += 1

            # Legacy HLS: stop when a second MAP appears to avoid mixing
            # init/segments from different timeline blocks.
            if map_count > 1:
                break

            uri_m = re.search(r'URI="([^"]+)"', line)
            if uri_m:
                init_url = urljoin(base_url, uri_m.group(1))

        elif line.startswith("#EXTINF:"):
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].strip().startswith("#")):
                i += 1
            
            if i < len(lines):
                seg_url = lines[i].strip()
                if seg_url and not seg_url.startswith("#"):
                    segments.append(
                        {
                            "url":    urljoin(base_url, seg_url),
                            "number": seg_num,
                            "enc":    dict(current_enc),
                        }
                    )
                    seg_num += 1
            i += 1
            continue

        i += 1

    return segments, init_url