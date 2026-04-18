# 01.04.26

import logging
import re
import os
import struct
import subprocess
from typing import Optional

from ._models import EncryptionInfo, WIDEVINE_SYSTEM_ID, SCHEME_TO_MODE, VIDEO_CODEC_MAP


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Box / field utilities
# ---------------------------------------------------------------------------
def _find_boxes_by_name(data, name: str) -> list:
    """Recursively collect all boxes whose ``name`` field matches *name*."""
    found = []
    if isinstance(data, list):
        for item in data:
            found.extend(_find_boxes_by_name(item, name))
    elif isinstance(data, dict):
        if str(data.get("name", "")).lower() == name.lower():
            found.append(data)
        for value in data.values():
            if isinstance(value, (dict, list)):
                found.extend(_find_boxes_by_name(value, name))
    return found


def _clean_kid(kid_raw) -> str:
    """Normalise a raw KID value (list of ints or string) to a lowercase hex string."""
    if isinstance(kid_raw, list):
        return "".join(f"{byte:02x}" for byte in kid_raw)
    return re.sub(r"[\[\]\s]", "", str(kid_raw)).lower()


def _select_preferred_pssh(pssh_boxes: list[dict]) -> Optional[str]:
    """
    Return the system-id of the preferred PSSH box.
    Widevine is preferred; falls back to the first available box.
    """
    if not pssh_boxes:
        return None
    for box in pssh_boxes:
        if box.get("system_id", "").replace(" ", "").lower() == WIDEVINE_SYSTEM_ID:
            return box.get("system_id")
    return pssh_boxes[0].get("system_id")


# ---------------------------------------------------------------------------
# mp4dump runner
# ---------------------------------------------------------------------------
def run_mp4dump(mp4dump_path: str, file_path: str, fmt: str = "json") -> Optional[str]:
    """
    Run the Bento4 ``mp4dump`` tool on *file_path* and return stdout as a
    string.  Tries multiple encodings.  Returns ``None`` on any failure.
    """
    try:
        cmd = [mp4dump_path, "--verbosity", "0", "--format", fmt, file_path]
        logger.info(f"mp4dump cmd: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        raw = result.stdout
        for enc in ("utf-8", "utf-16", "utf-16-le", "latin-1"):
            try:
                text = raw.decode(enc).lstrip("\ufeff")
                return text if text.strip() else None
            except (UnicodeDecodeError, ValueError):
                continue
    except Exception as exc:
        logger.error(f"mp4dump ({fmt}) failed: {exc}")
    return None


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
def parse_json_dump(text: Optional[str]) -> EncryptionInfo:
    """Parse the JSON output of ``mp4dump`` into an :class:`EncryptionInfo`."""
    import json

    if not text:
        return EncryptionInfo()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return EncryptionInfo()

    info = EncryptionInfo()
    pssh_boxes = _find_boxes_by_name(data, "pssh")
    tenc_boxes = _find_boxes_by_name(data, "tenc")
    schm_boxes = _find_boxes_by_name(data, "schm")
    encv_boxes = _find_boxes_by_name(data, "encv")
    sinf_boxes = _find_boxes_by_name(data, "sinf")
    saio_boxes = _find_boxes_by_name(data, "saio")
    saiz_boxes = _find_boxes_by_name(data, "saiz")

    for box in tenc_boxes:
        if "default_KID" in box:
            info.kid = _clean_kid(box["default_KID"])
            break

    for schm in schm_boxes:
        scheme = schm.get("scheme_type")
        if scheme:
            info.scheme = str(scheme).lower()
            break

    for tenc in tenc_boxes:
        crypt = tenc.get("default_crypt_byte_block", 0)
        skip  = tenc.get("default_skip_byte_block", 0)
        if crypt > 0 and skip > 0:
            info.encryption_method = "SAMPLE_AES"
            break

    for encv in encv_boxes:
        for frma in _find_boxes_by_name([encv], "frma"):
            fmt = frma.get("original_format")
            if fmt:
                info.video_codec = VIDEO_CODEC_MAP.get(fmt, fmt)
                break
        if info.video_codec:
            break

    if pssh_boxes or tenc_boxes or sinf_boxes or saio_boxes or saiz_boxes:
        info.encrypted = True
    info.pssh_boxes = pssh_boxes
    return info


def parse_text_dump(text: Optional[str]) -> EncryptionInfo:
    """Parse the plaintext output of ``mp4dump`` into an :class:`EncryptionInfo`."""
    if not text:
        return EncryptionInfo()

    info = EncryptionInfo()

    def _normalize_spaces(line: str) -> str:
        return re.sub(
            r"(?<!\S)((?:\S )+\S)(?!\S)",
            lambda m: m.group(0).replace(" ", ""),
            line,
        )

    normalized = "\n".join(_normalize_spaces(line) for line in text.splitlines())

    pssh_blocks = re.findall(
        r"\[pssh\].*?system_id\s*=\s*\[([0-9a-f\s]+)\].*?data_size\s*=\s*(\d+)",
        normalized,
        re.IGNORECASE | re.DOTALL,
    )
    for sid_raw, data_size in pssh_blocks:
        sid = sid_raw.replace(" ", "").lower()
        info.pssh_boxes.append({"system_id": sid, "data_size": int(data_size)})
        info.encrypted = True

    scheme_match = re.search(r"scheme_type\s*=\s*[\"']?(\w+)[\"']?", normalized, re.IGNORECASE)
    if scheme_match:
        info.scheme    = scheme_match.group(1).lower()
        info.encrypted = True

    kid_match = re.search(
        r"\[tenc\].*?default_KID\s*=\s*\[([0-9a-f\s]+)\]",
        normalized,
        re.IGNORECASE | re.DOTALL,
    )
    if kid_match:
        info.kid       = _clean_kid(kid_match.group(1))
        info.encrypted = True

    crypt_match = re.search(r"default_crypt_byte_block\s*=\s*(\d+)", normalized, re.IGNORECASE)
    skip_match  = re.search(r"default_skip_byte_block\s*=\s*(\d+)",  normalized, re.IGNORECASE)
    if crypt_match and skip_match:
        if int(crypt_match.group(1)) > 0 and int(skip_match.group(1)) > 0:
            info.encryption_method = "SAMPLE_AES"

    codec_match = re.search(
        r"\[encv\].*?original_format\s*=\s*(\w+)", normalized, re.IGNORECASE | re.DOTALL
    )
    if codec_match:
        codec_raw        = codec_match.group(1)
        info.video_codec = VIDEO_CODEC_MAP.get(codec_raw, codec_raw)

    for marker in (r"\[sinf\]", r"\[saio\]", r"\[saiz\]"):
        if re.search(marker, normalized, re.IGNORECASE):
            info.encrypted = True
            break

    return info


def parse_binary(file_path: str) -> EncryptionInfo:
    """
    Last-resort parser: scan the first 2 MB of *file_path* for raw CENC/PSSH
    markers without relying on mp4dump.
    """
    info = EncryptionInfo()
    try:
        with open(file_path, "rb") as f:
            data = f.read(min(os.path.getsize(file_path), 2 * 1024 * 1024))

        for marker in (b"cenc", b"cens", b"cbcs", b"cbc1"):
            if marker in data:
                info.scheme    = marker.decode()
                info.encrypted = True
                break

        # Try to read the scheme from a proper 'schm' box.
        schm = b"schm"
        idx  = data.find(schm)
        if idx != -1 and idx + 12 <= len(data):
            raw_scheme = data[idx + 8 : idx + 12]
            try:
                scheme = raw_scheme.decode("ascii").lower()
                if scheme in SCHEME_TO_MODE:
                    info.scheme    = scheme
                    info.encrypted = True
            except Exception:
                pass

        # Scan for PSSH boxes and extract KID from the first Widevine one.
        marker = b"pssh"
        cursor = 0
        while True:
            pos = data.find(marker, cursor)
            if pos == -1:
                break

            if pos + 28 <= len(data):
                sid  = data[pos + 8 : pos + 24].hex().lower()
                size = struct.unpack_from(">I", data, pos + 24)[0] if pos + 28 <= len(data) else 0
                info.pssh_boxes.append({"system_id": sid, "data_size": size})
                info.encrypted = True

                if sid == WIDEVINE_SYSTEM_ID and not info.kid:
                    pssh_data_start = pos + 28
                    if pssh_data_start + 18 <= len(data):
                        info.kid = data[pssh_data_start + 2 : pssh_data_start + 18].hex().lower()
            
            cursor = pos + 4

    except Exception as exc:
        logger.error(f"Binary parse failed: {exc}")

    return info


def detect_encryption_info(mp4dump_path: str, file_path: str) -> EncryptionInfo:
    """
    Try to detect encryption metadata using three strategies in order:
    JSON mp4dump → text mp4dump → raw binary scan.

    Returns the first successful result, or a blank :class:`EncryptionInfo`
    if the file appears clear.
    """
    for parse_fn, fmt in ((parse_json_dump, "json"), (parse_text_dump, "text")):
        raw = run_mp4dump(mp4dump_path, file_path, fmt=fmt)
        info = parse_fn(raw)
        if info.encrypted:
            return _finalize(info)

    info = parse_binary(file_path)
    if info.encrypted:
        return _finalize(info)

    return EncryptionInfo()


def _finalize(info: EncryptionInfo) -> EncryptionInfo:
    info.pssh_b64 = _select_preferred_pssh(info.pssh_boxes)
    return info


def extract_widevine_kid(file_path: str) -> Optional[str]:
    """
    Walk the raw bytes of *file_path* looking for a Widevine PSSH box and
    extract the content-key KID from its payload.

    Returns a lowercase hex string or ``None``.
    """
    try:
        with open(file_path, "rb") as f:
            data = f.read(min(os.path.getsize(file_path), 2 * 1024 * 1024))

        marker = b"pssh"
        sid    = bytes.fromhex(WIDEVINE_SYSTEM_ID)
        cursor = 0

        while True:
            pos = data.find(marker, cursor)
            if pos < 4:
                return None

            box_start = pos - 4
            if box_start + 8 > len(data):
                return None

            box_size = int.from_bytes(data[box_start:pos], "big", signed=False)
            if box_size < 32 or box_start + box_size > len(data):
                cursor = pos + 4
                continue

            box = data[box_start : box_start + box_size]
            version = box[8]
            if version not in (0, 1):
                cursor = pos + 4
                continue

            if box[12:28] != sid:
                cursor = pos + 4
                continue

            offset = 28
            if version == 1:
                if offset + 4 > len(box):
                    return None
                kid_count = int.from_bytes(box[offset : offset + 4], "big", signed=False)
                offset += 4 + kid_count * 16

            if offset + 4 > len(box):
                return None

            pssh_size = int.from_bytes(box[offset : offset + 4], "big", signed=False)
            offset   += 4
            if offset + pssh_size > len(box):
                return None

            payload = box[offset : offset + pssh_size]
            if len(payload) >= 18:
                return payload[2:18].hex().lower()
            return None

    except Exception as exc:
        logger.warning(f"Failed extracting KID from Widevine PSSH: {exc}")
    return None