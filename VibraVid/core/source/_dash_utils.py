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
        content_len = 0
        accept_ranges = ""
        head_ok = False

        with create_client(headers=headers, timeout=request_timeout, follow_redirects=True) as c:
            try:
                r = c.head(media_url)
                r.raise_for_status()
                content_len  = int((r.headers.get("content-length") or "0").strip() or "0")
                accept_ranges = (r.headers.get("accept-ranges") or "").lower()
                head_ok = True
            except Exception as head_exc:
                logger.debug(f"DASH range-split HEAD failed for {media_url}: {head_exc} — retrying with GET Range=0-0")

            if not head_ok or content_len == 0:
                try:
                    probe_headers = dict(headers)
                    probe_headers["Range"] = "bytes=0-0"
                    r2 = c.get(media_url, headers=probe_headers)

                    # 206 Partial Content → server support for Range requests, content length is in Content-Range header
                    if r2.status_code == 206:
                        accept_ranges = "bytes"

                        # Content-Range: bytes 0-0/TOTAL
                        cr = r2.headers.get("content-range", "")
                        if "/" in cr:
                            try:
                                content_len = int(cr.split("/")[-1].strip())
                            except ValueError:
                                pass
                    
                    elif r2.status_code == 200:
                        # server ignored the Range header and sent the whole file → content length is in Content-Length header, but no Range support
                        content_len  = int((r2.headers.get("content-length") or "0").strip() or "0")
                        accept_ranges = (r2.headers.get("accept-ranges") or "").lower()

                except Exception as get_exc:
                    logger.debug(f"DASH range-split GET probe failed for {media_url}: {get_exc}")

        if content_len <= chunk_size or "bytes" not in accept_ranges:
            logger.debug(f"DASH range-split skipped | url={media_url} | size={content_len} | chunk={chunk_size} | accept-ranges={accept_ranges!r}")
            return []

        ranges = split_http_ranges(content_len, chunk_size)
        logger.debug(f"DASH range-split | url={media_url} | size={content_len} | chunk={chunk_size} | parts={len(ranges)}")
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
        logger.warning(f"DASH range-split failed for {media_url}: {exc} — caller will use single-file fallback")
        return []
