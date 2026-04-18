# 01.04.26

from dataclasses import dataclass, field
from typing import Optional


WIDEVINE_SYSTEM_ID = "edef8ba979d64acea3c827dcd51d21ed"

# Maps CENC protection scheme → AES mode used by mp4decrypt / Shaka.
SCHEME_TO_MODE: dict[str, str] = {
    "cenc": "ctr",
    "cens": "ctr",
    "cbcs": "cbc",
    "cbc1": "cbc",
}

# Short codec-box identifier → human-readable name shown in logs/UI.
VIDEO_CODEC_MAP: dict[str, str] = {
    "avc1": "H.264",
    "avc3": "H.264",
    "hev1": "HEVC",
    "hevC": "HEVC",
    "hev0": "HEVC",
    "vp9":  "VP9",
    "av01": "AV1",
}


# ---------------------------------------------------------------------------
# EncryptionInfo
# ---------------------------------------------------------------------------
@dataclass
class EncryptionInfo:
    encrypted: bool = False
    scheme: Optional[str] = None                        # e.g. "cenc", "cbcs"
    kid: Optional[str] = None                           # default KID hex string
    pssh_b64: Optional[str] = None                      # selected PSSH system-id (populated by _finalize)
    video_codec: Optional[str] = None                   # e.g. "H.264", "HEVC"
    encryption_method: Optional[str] = None             # e.g. "SAMPLE_AES"
    pssh_boxes: list[dict] = field(default_factory=list)