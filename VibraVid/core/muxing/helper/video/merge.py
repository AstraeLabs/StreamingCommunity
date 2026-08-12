# 16.04.24

import gzip
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Streaming buffer for raw (non-gzip) segment copies, so a segment is never fully materialised in memory.
_COPY_BUFSIZE = 1024 * 1024
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_SCAN_WINDOW = 64 * 1024


def _find_ts_start(buf: bytes) -> int:
    """Return the offset of the first MPEG-TS packet inside a PNG-wrapped segment."""
    limit = len(buf) - 188 * 4
    for k in range(0, max(0, limit)):
        if buf[k] == 0x47 and all(buf[k + n * 188] == 0x47 for n in range(1, 5)):
            return k
    return 0


def _merge_fmt_size(nb: int) -> str:
    if nb >= 1_073_741_824:
        return f"{nb / 1_073_741_824:.2f}GB"
    if nb >= 1_048_576:
        return f"{nb / 1_048_576:.1f}MB"
    if nb >= 1_024:
        return f"{nb / 1024:.0f}KB"
    return f"{nb}B"


def _segment_number(path: Path) -> int:
    try:
        stem = path.stem
        if stem.startswith("seg_"):
            return int(stem[4:])
    except (ValueError, IndexError):
        pass
    return 999_999_999


def binary_merge_segments(paths: list[Path], output_path: Path, merge_logger: logging.Logger | None = None) -> None:
    """Merge downloaded segments using direct raw binary concatenation."""
    log = merge_logger or logger

    # Single stat() per path: reused for both the size filter and total accounting.
    valid: list[tuple[Path, int, int]] = []
    for p in paths:
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > 0:
            valid.append((p, _segment_number(p), size))

    valid.sort(key=lambda item: item[1])
    if not valid:
        log.error("[binary_merge] No valid segments found")
        return

    total_written = 0
    tmp_output = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with open(tmp_output, "wb") as out_f:
            png_wrapped = 0
            for seg_path, _, seg_size in valid:
                with open(seg_path, "rb") as in_f:
                    head = in_f.read(8)

                    # gzip-compressed segment (magic bytes 1f 8b): the whole segment must be held in memory to decompress it.
                    if head[:2] == b"\x1f\x8b":
                        try:
                            log.info(f"[binary_merge] detected gzip-compressed segment: {seg_path.name}, decompressing ...")
                            data = gzip.decompress(head + in_f.read())
                            out_f.write(data)
                            total_written += len(data)
                            continue
                        except Exception as e:
                            log.warning(f"[binary_merge] failed to decompress {seg_path.name}: {e}, using raw data")
                            in_f.seek(0)
                            shutil.copyfileobj(in_f, out_f, _COPY_BUFSIZE)
                            total_written += seg_size
                            continue

                    # PNG-wrapped segment: a fake image cover hides the real MPEG-TS payload.
                    if head == _PNG_SIGNATURE:
                        prefix = head + in_f.read(_PNG_SCAN_WINDOW - len(head))
                        ts_off = _find_ts_start(prefix)
                        if ts_off > 0:
                            png_wrapped += 1
                            out_f.write(prefix[ts_off:])
                            shutil.copyfileobj(in_f, out_f, _COPY_BUFSIZE)

                            # Written = (prefix after the wrapper) + (streamed tail past the scan window).
                            total_written += (len(prefix) - ts_off) + (seg_size - len(prefix))
                            continue

                        log.warning(f"[binary_merge] PNG-wrapped segment {seg_path.name} has no TS sync, using raw data")
                        out_f.write(prefix)
                        shutil.copyfileobj(in_f, out_f, _COPY_BUFSIZE)
                        total_written += seg_size
                        continue

                    # Raw segment: stream in fixed-size blocks instead of loading it whole.
                    out_f.write(head)
                    shutil.copyfileobj(in_f, out_f, _COPY_BUFSIZE)
                    total_written += seg_size

        if tmp_output.exists() and tmp_output.stat().st_size > 0:
            tmp_output.replace(output_path)
        else:
            try:
                tmp_output.unlink(missing_ok=True)
            except Exception:
                pass

        if png_wrapped:
            log.info(f"[binary_merge] stripped PNG wrapper from {png_wrapped}/{len(valid)} segment(s)")

        if output_path.exists() and output_path.stat().st_size > 0:
            log.debug(f"[binary_merge] raw concat OK: {output_path.name} ({_merge_fmt_size(total_written)})")
        else:
            log.error(f"[binary_merge] output is empty or missing: {output_path}")

    except Exception as exc:
        log.error(f"[binary_merge] exception during merge: {exc}")
        try:
            tmp_output.unlink(missing_ok=True)
        except Exception:
            pass
        raise
