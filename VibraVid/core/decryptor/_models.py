# 01.04.26

import logging
from dataclasses import dataclass, field

from VibraVid.utils._mp4dump import parse_file

from ..drm.system import _DRMSystems

logger = logging.getLogger(__name__)


WIDEVINE_SYSTEM_ID = _DRMSystems.WIDEVINE
PLAYREADY_SYSTEM_ID = _DRMSystems.PLAYREADY

SCHEME_TO_MODE: dict[str, str] = {
    "cenc": "ctr",
    "cens": "ctr",
    "cbcs": "cbc",
    "cbc1": "cbc",
    "fps": "cbc",
    "fps ": "cbc",
}
VIDEO_CODEC_MAP: dict[str, str] = {
    "avc1": "H.264",
    "avc3": "H.264",
    "hev1": "HEVC",
    "hevC": "HEVC",
    "hev0": "HEVC",
    "vp9": "VP9",
    "av01": "AV1",
}

_EBML_MAGIC = b"\x1a\x45\xdf\xa3"
_WEBM_SEGMENT = 0x18538067
_WEBM_TRACKS = 0x1654AE6B
_WEBM_TRACK_ENTRY = 0xAE
_WEBM_CONTENT_ENCODINGS = 0x6D80
_WEBM_CONTENT_ENCODING = 0x6240
_WEBM_CONTENT_ENCRYPTION = 0x5035
_WEBM_CONTENT_ENC_KEY_ID = 0x47E2
_WEBM_CONTAINERS = {
    _WEBM_SEGMENT,
    _WEBM_TRACKS,
    _WEBM_TRACK_ENTRY,
    _WEBM_CONTENT_ENCODINGS,
    _WEBM_CONTENT_ENCODING,
    _WEBM_CONTENT_ENCRYPTION,
}



@dataclass
class EncryptionInfo:
    encrypted: bool = False
    scheme: str | None = None
    kid: str | None = None
    pssh_b64: str | None = None 
    video_codec: str | None = None
    encryption_method: str | None = None
    track_ids: list[str] | None = None
    pssh_boxes: list[dict] = field(default_factory=list)


def _walk(atoms):
    """Yield every atom in *atoms* depth-first (including the roots)."""
    stack = list(atoms)
    while stack:
        atom = stack.pop()
        yield atom
        stack.extend(atom.children)


def _find_all(atoms, box_type: str) -> list:
    return [a for a in _walk(atoms) if a.type == box_type]


def _select_preferred_pssh(pssh_boxes: list[dict], kid: str | None) -> str | None:
    """Return a real base64 PSSH box for the preferred system (Widevine first)."""
    if not pssh_boxes:
        return None

    has_widevine = any(box.get("system_id", "").replace(" ", "").lower() == WIDEVINE_SYSTEM_ID for box in pssh_boxes)
    if has_widevine and kid:
        try:
            return _DRMSystems.build_widevine_pssh_from_kid(kid)
        except Exception as exc:
            logger.debug(f"Widevine PSSH synthesis failed for KID {kid}: {exc}")

    if has_widevine:
        return WIDEVINE_SYSTEM_ID
    return pssh_boxes[0].get("system_id")


def _ebml_read_vint(buf: bytes, pos: int, is_id: bool) -> tuple[int, int] | None:
    if pos >= len(buf):
        return None
    first = buf[pos]
    if first == 0:
        return None
    length, mask = 1, 0x80
    while not (first & mask) and length <= 8:
        mask >>= 1
        length += 1
    if pos + length > len(buf):
        return None
    if is_id:
        value = 0
        for i in range(length):
            value = (value << 8) | buf[pos + i]
    else:
        value = first & (mask - 1)
        for i in range(1, length):
            value = (value << 8) | buf[pos + i]
    return value, length


def _detect_webm_encryption(file_path: str) -> EncryptionInfo:
    """Detect ContentEncKeyID by walking the leading portion of a WebM/Matroska file's EBML tree."""
    info = EncryptionInfo()
    try:
        with open(file_path, "rb") as fh:
            buf = fh.read(2_000_000)
    except OSError as exc:
        logger.debug(f"webm read failed for {file_path}: {exc}")
        return info

    if buf[:4] != _EBML_MAGIC:
        return info

    def walk(start: int, end: int) -> None:
        pos = start
        while pos < end:
            idr = _ebml_read_vint(buf, pos, True)
            if not idr:
                return
            eid, idlen = idr
            pos += idlen
            szr = _ebml_read_vint(buf, pos, False)
            if not szr:
                return
            size, szlen = szr
            pos += szlen
            unknown_size = size == (1 << (7 * szlen)) - 1
            child_end = end if unknown_size else min(pos + size, end)
            if eid in _WEBM_CONTAINERS:
                walk(pos, child_end)
            elif eid == _WEBM_CONTENT_ENC_KEY_ID:
                info.kid = buf[pos:child_end].hex()
                info.encrypted = True
                info.scheme = "cenc"
            pos = child_end

    walk(0, len(buf))
    return info


def detect_encryption_info(file_path: str) -> EncryptionInfo:
    """Detect encryption metadata by walking the MP4 box tree (falls back to WebM/EBML)."""
    try:
        atoms = parse_file(file_path, decode_senc_entries=False)
    except Exception as exc:
        logger.debug(f"parse_file failed for {file_path}: {exc}")
        return _detect_webm_encryption(file_path)

    info = EncryptionInfo()
    pssh_boxes = _find_all(atoms, "pssh")
    tenc_boxes = _find_all(atoms, "tenc")
    schm_boxes = _find_all(atoms, "schm")
    encv_boxes = _find_all(atoms, "encv")
    sinf_boxes = _find_all(atoms, "sinf")
    saio_boxes = _find_all(atoms, "saio")
    saiz_boxes = _find_all(atoms, "saiz")
    trak_boxes = _find_all(atoms, "trak")

    seen_kids: set[str] = set()
    for tenc in tenc_boxes:
        kid = tenc.data.get("default_KID")
        if isinstance(kid, (bytes, bytearray)):
            seen_kids.add(kid.hex())
            if info.kid is None:
                info.kid = kid.hex()
    if len(seen_kids) > 1:
        # Multiple tenc boxes with DIFFERENT default_KIDs -- e.g. a track
        # with more than one protected stsd entry, each carrying its own
        # tenc (the same content shape that caused a real silent-corruption
        # bug in flux's clear-lead detection, which assumed a single
        # protected entry). Picking the first arbitrarily could be wrong for
        # this file; at minimum make that ambiguity visible in the logs.
        logger.warning(
            f"{file_path}: multiple distinct KIDs found across {len(tenc_boxes)} tenc boxes "
            f"({sorted(seen_kids)}) -- using the first one ({info.kid}); this may be wrong if "
            f"different stsd entries actually use different keys"
        )

    seen_schemes: set[str] = set()
    for schm in schm_boxes:
        scheme = schm.data.get("scheme_type")
        if scheme:
            seen_schemes.add(str(scheme).lower())
            if info.scheme is None:
                info.scheme = str(scheme).lower()
    if len(seen_schemes) > 1:
        logger.warning(
            f"{file_path}: multiple distinct schemes found across {len(schm_boxes)} schm boxes "
            f"({sorted(seen_schemes)}) -- using the first one ({info.scheme})"
        )

    for encv in encv_boxes:
        frma_boxes = _find_all([encv], "frma")
        if frma_boxes:
            fmt = frma_boxes[0].data.get("original_format")
            if fmt:
                info.video_codec = VIDEO_CODEC_MAP.get(fmt, fmt)
                break

    for pssh in pssh_boxes:
        sid = pssh.data.get("system_id", b"")
        sid = sid.hex() if isinstance(sid, (bytes, bytearray)) else str(sid).replace(" ", "").lower()
        info.pssh_boxes.append({"system_id": sid, "data_size": pssh.data.get("data_size", 0)})

    track_ids = []
    for trak in trak_boxes:
        has_enc = bool(
            _find_all([trak], "tenc")
            or _find_all([trak], "schm")
            or _find_all([trak], "sinf")
            or _find_all([trak], "saio")
            or _find_all([trak], "saiz")
        )
        if not has_enc:
            continue
        tkhd_boxes = _find_all([trak], "tkhd")
        if tkhd_boxes:
            tid = tkhd_boxes[0].data.get("track_id")
            if tid is not None:
                track_ids.append(str(tid))

    if track_ids:
        info.track_ids = track_ids

    if pssh_boxes or tenc_boxes or sinf_boxes or saio_boxes or saiz_boxes:
        info.encrypted = True

    if not info.encrypted and _find_all(atoms, "4snf"):
        info.encrypted = True
        info.scheme = "fps"
        info.encryption_method = "SAMPLE_AES"

    if not info.encrypted:
        return _detect_webm_encryption(file_path)

    info.pssh_b64 = _select_preferred_pssh(info.pssh_boxes, info.kid)
    return info


def extract_widevine_kid(file_path: str) -> str | None:
    """Extract the content-key KID from a Widevine PSSH payload, or ``None``."""
    try:
        atoms = parse_file(file_path, decode_senc_entries=False)
    except Exception as exc:
        logger.debug(f"parse_file failed for {file_path}: {exc}")
        return None

    for atom in _walk(atoms):
        if atom.type != "pssh":
            continue
        sid = atom.data.get("system_id", b"")
        if not (isinstance(sid, (bytes, bytearray)) and sid.hex() == WIDEVINE_SYSTEM_ID):
            continue

        data = atom.data.get("data", b"")
        if isinstance(data, (bytes, bytearray)):
            idx = bytes(data).find(b"\x12\x10")
            if idx != -1 and len(data) >= idx + 18:
                return data[idx + 2 : idx + 18].hex()

    return None
