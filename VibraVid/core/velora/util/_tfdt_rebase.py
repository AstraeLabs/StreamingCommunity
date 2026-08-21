# 16.08.26

import struct
from pathlib import Path

_MOOF_SCAN_CHUNK = 1 * 1024 * 1024


def _read_box_header(fh, pos: int) -> tuple[bytes, int, int] | None:
    """Read the box header at *pos* without touching its payload. Returns (type, size, header_len) or None at EOF/malformed. 32-bit sizes only (CMAF/DASH/HLS moof/traf/tfdt boxes are always small)."""
    fh.seek(pos)
    head = fh.read(8)
    if len(head) < 8:
        return None
    size = struct.unpack(">I", head[:4])[0]
    box_type = head[4:8]
    if size == 1:
        head16 = fh.read(8)
        if len(head16) < 8:
            return None
        return box_type, struct.unpack(">Q", head16)[0], 16
    if size == 0:
        return None  # box-extends-to-EOF is never valid for moof/mdat in a fragmented stream we produced
    return box_type, size, 8


def _find_tfdt_offsets(fh, moof_off: int, moof_size: int) -> list[tuple[int, int]]:
    """Return [(value_offset, value_width_bytes), ...] for every traf>tfdt inside one moof."""
    out: list[tuple[int, int]] = []
    end = moof_off + moof_size
    off = moof_off + 8  # past moof's own header
    while off < end:
        hdr = _read_box_header(fh, off)
        if hdr is None:
            break
        box_type, size, header_len = hdr
        if size < header_len or off + size > end:
            break

        if box_type == b"traf":
            traf_end = off + size
            toff = off + header_len
            while toff < traf_end:
                thdr = _read_box_header(fh, toff)
                if thdr is None:
                    break
                ttype, tsize, theader_len = thdr
                if tsize < theader_len or toff + tsize > traf_end:
                    break
                if ttype == b"tfdt":
                    # tfdt is a FullBox: plain box header (theader_len, from the
                    # generic scanner above -- it doesn't know about FullBox
                    # version/flags) + version(1) + flags(3) + value.
                    fh.seek(toff + theader_len)
                    version = fh.read(1)[0]
                    value_off = toff + theader_len + 4  # past version(1)+flags(3)
                    out.append((value_off, 8 if version == 1 else 4))
                toff += tsize
        off += size
    return out


def rebase_fragment_timestamps(merged_path: Path, logger=None) -> bool:
    """In-place: subtract the first fragment's tfdt from every fragment's tfdt (moof>traf>tfdt), resetting an absolute CMAF/DASH-style base media decode time to start at zero"""
    try:
        with open(merged_path, "r+b") as fh:
            fh.seek(0, 2)
            file_size = fh.tell()

            all_tfdt: list[tuple[int, int]] = []
            off = 0
            while off < file_size:
                hdr = _read_box_header(fh, off)
                if hdr is None:
                    break
                box_type, size, _header_len = hdr
                if size <= 0 or off + size > file_size:
                    break

                if box_type == b"moof":
                    all_tfdt.extend(_find_tfdt_offsets(fh, off, min(size, _MOOF_SCAN_CHUNK)))

                off += size

            if not all_tfdt:
                return False

            def _read_value(value_off: int, width: int) -> int:
                fh.seek(value_off)
                raw = fh.read(width)
                return struct.unpack(">Q" if width == 8 else ">I", raw)[0]

            base = _read_value(*all_tfdt[0])
            if base == 0:
                return False  # already zero-based, nothing to do

            for value_off, width in all_tfdt:
                current = _read_value(value_off, width)
                new_value = max(0, current - base)
                fh.seek(value_off)
                fh.write(struct.pack(">Q" if width == 8 else ">I", new_value))

            if logger:
                logger.debug(f"[tfdt_rebase] {merged_path.name}: rebased {len(all_tfdt)} fragment(s) by -{base}")
            return True

    except Exception as exc:
        if logger:
            logger.debug(f"[tfdt_rebase] {merged_path.name}: skipped ({exc})")
        return False
