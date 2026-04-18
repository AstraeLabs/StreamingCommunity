# 10.04.26

import logging
from typing import Dict, List, Tuple


logger = logging.getLogger(__name__)


def split_http_ranges(total_size: int, chunk_size: int) -> List[Tuple[int, int]]:
    """Partition *total_size* bytes into ``(start, end)`` inclusive Range pairs."""
    ranges: List[Tuple[int, int]] = []
    start = 0

    while start < total_size:
        end = min(start + chunk_size - 1, total_size - 1)
        ranges.append((start, end))
        start = end + 1
    
    return ranges


def build_dash_ranged_segments(media_url: str, headers: Dict, chunk_size: int, request_timeout: int) -> List[Dict]:
    """
    Return synthetic DASH chunk segments using HTTP Range headers, when the
    server advertises ``Accept-Ranges: bytes`` and the file is large enough
    to benefit from splitting.

    Returns an empty list when Range-download is not applicable or on any
    network/HTTP error (caller falls back to a single-segment download).
    """
    from VibraVid.utils.http_client import create_client

    try:
        with create_client(headers=headers, timeout=request_timeout, follow_redirects=True) as c:
            r = c.head(media_url)
            r.raise_for_status()

        content_len = int((r.headers.get("content-length") or "0").strip() or "0")
        accept_ranges = (r.headers.get("accept-ranges") or "").lower()

        if content_len <= chunk_size or "bytes" not in accept_ranges:
            return []

        ranges = split_http_ranges(content_len, chunk_size)
        logger.debug("DASH range-split | url=%s | size=%s | chunk=%s | parts=%s", media_url, content_len, chunk_size, len(ranges))
        return [
            {
                "url":     media_url,
                "number":  0,
                "enc":     {"method": "NONE"},
                "headers": {"Range": f"bytes={start}-{end}"},
            }
            for start, end in ranges
        ]
    except Exception as exc:
        logger.debug("DASH range-split skipped for %s: %s", media_url, exc)
        return []