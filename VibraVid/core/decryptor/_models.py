# 01.04.26

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..drm.system import _DRMSystems
from VibraVid.utils._mp4dump import parse_file


logger = logging.getLogger(__name__)


WIDEVINE_SYSTEM_ID = _DRMSystems.WIDEVINE
PLAYREADY_SYSTEM_ID = _DRMSystems.PLAYREADY

# Maps CENC protection scheme -> AES mode used by mp4decrypt / Shaka.
SCHEME_TO_MODE: dict[str, str] = {
    "cenc": "ctr",
    "cens": "ctr",
    "cbcs": "cbc",
    "cbc1": "cbc",
    "fps":  "cbc",
    "fps ": "cbc",
}

# Short codec-box identifier -> human-readable name shown in logs/UI.
VIDEO_CODEC_MAP: dict[str, str] = {
    "avc1": "H.264",
    "avc3": "H.264",
    "hev1": "HEVC",
    "hevC": "HEVC",
    "hev0": "HEVC",
    "vp9":  "VP9",
    "av01": "AV1",
}


@dataclass
class EncryptionInfo:
    encrypted: bool = False
    scheme: Optional[str] = None                        # e.g. "cenc", "cbcs"
    kid: Optional[str] = None                           # default KID hex string
    pssh_b64: Optional[str] = None                      # selected PSSH system-id (populated by _finalize)
    video_codec: Optional[str] = None                   # e.g. "H.264", "HEVC"
    encryption_method: Optional[str] = None             # e.g. "SAMPLE_AES"
    track_ids: Optional[list[str]] = None               # list of track IDs (if available)
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


def _select_preferred_pssh(pssh_boxes: list[dict]) -> Optional[str]:
    """Return the system-id of the preferred PSSH box (Widevine first)."""
    if not pssh_boxes:
        return None
    for box in pssh_boxes:
        if box.get("system_id", "").replace(" ", "").lower() == WIDEVINE_SYSTEM_ID:
            return box.get("system_id")
    return pssh_boxes[0].get("system_id")


def detect_encryption_info(file_path: str) -> EncryptionInfo:
    """Detect encryption metadata by walking the MP4 box tree."""
    try:
        atoms = parse_file(file_path, decode_senc_entries=False)
    except Exception as exc:
        logger.debug(f"parse_file failed for {file_path}: {exc}")
        return EncryptionInfo()

    info = EncryptionInfo()
    pssh_boxes = _find_all(atoms, "pssh")
    tenc_boxes = _find_all(atoms, "tenc")
    schm_boxes = _find_all(atoms, "schm")
    encv_boxes = _find_all(atoms, "encv")
    sinf_boxes = _find_all(atoms, "sinf")
    saio_boxes = _find_all(atoms, "saio")
    saiz_boxes = _find_all(atoms, "saiz")
    trak_boxes = _find_all(atoms, "trak")

    for tenc in tenc_boxes:
        kid = tenc.data.get("default_KID")
        if isinstance(kid, (bytes, bytearray)):
            info.kid = kid.hex()
            break

    for schm in schm_boxes:
        scheme = schm.data.get("scheme_type")
        if scheme:
            info.scheme = str(scheme).lower()
            break

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
        return EncryptionInfo()

    info.pssh_b64 = _select_preferred_pssh(info.pssh_boxes)
    return info


def extract_widevine_kid(file_path: str) -> Optional[str]:
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
                return data[idx + 2:idx + 18].hex()
    
    return None
