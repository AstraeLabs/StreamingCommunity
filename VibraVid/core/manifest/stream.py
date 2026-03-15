# 13.03.26

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional


logger = logging.getLogger(__name__)


class DRMInfo:
    WIDEVINE_SYSTEM_ID = "edef8ba9-79d6-4ace-a3c8-27dcd51d21ed"
    PLAYREADY_SYSTEM_ID = "9a04f079-9840-4286-ab92-e65be0885f95"
    FAIRPLAY_SYSTEM_ID = "94ce86fb-07ff-4f43-adb8-93d2fa968ca2"

    def __init__(self):
        self.pssh = None
        self.kid = None
        self.key = None
        self.system_id = None
        self.drm_type = None  # 'WV' | 'PR' | 'FP' | 'UNK'
        self.default_kid = None
        self.method = None  # 'cenc' | 'cbcs' | …

        # Multi-DRM support
        self._pssh_by_type: Dict[str, str] = {}
        self._drm_types: List[str] = []

    def set_pssh(self, pssh_base64: str, drm_type_hint: str = None) -> None:
        """
        Store a PSSH blob keyed by its DRM type.

        Detection order
        ───────────────
        1. pywidevine PSSH parser — handles both full ISO PSSH boxes
           (WV / PR / FP system IDs) AND raw Widevine protobuf blobs
           (e.g. the "CAES…" blobs Amazon / Netflix embed in <cenc:pssh>).
        2. pyplayready PSSH parser — secondary check specifically for PR
           PSSH boxes that pywidevine might classify ambiguously.
        3. Manual box-magic fallback — works when neither lib is installed.
        4. drm_type_hint — caller-supplied label derived from schemeIdUri
           (most reliable for raw protobuf / WMRM blobs).
        5. self.drm_type already resolved by set_method().
        6. "UNK" as last resort.
        """
        detected: Optional[str] = None

        # ── 1. pywidevine (handles raw WV protobuf + full PSSH boxes) ────────
        try:
            from pywidevine.pssh import PSSH as WV_PSSH
            from uuid import UUID as _UUID

            _WV_UUID = _UUID(hex="edef8ba979d64acea3c827dcd51d21ed")
            _PR_UUID = _UUID(hex="9a04f07998404286ab92e65be0885f95")
            _FP_UUID = _UUID(hex="94ce86fb07ff4f43adb893d2fa968ca2")

            wv_obj = WV_PSSH(pssh_base64)
            sid = wv_obj.system_id  # UUID instance
            self.system_id = str(sid)

            if sid == _WV_UUID:
                detected = "WV"
            elif sid == _PR_UUID:
                detected = "PR"
            elif sid == _FP_UUID:
                detected = "FP"
            else:
                detected = "UNK"

            logger.debug(f"DRMInfo.set_pssh [pywidevine] detected={detected} sid={sid}")

        except ImportError:
            logger.debug("DRMInfo.set_pssh: pywidevine not installed, falling back")
        except Exception as exc:
            logger.debug(f"DRMInfo.set_pssh [pywidevine] error: {exc}")

        # ── 2. pyplayready — secondary check for PR blobs ────────────────────
        if (not detected or detected == "UNK") and ((drm_type_hint or "").upper() in ("PR", "PLAYREADY") or (self.drm_type or "") == "PR"):
            try:
                from pyplayready.system.pssh import PSSH as PR_PSSH

                PR_PSSH(pssh_base64)  # raises on invalid PR data
                detected = "PR"
                logger.debug("DRMInfo.set_pssh [pyplayready] detected=PR")
            except ImportError:
                logger.debug("DRMInfo.set_pssh: pyplayready not installed")
            except Exception as exc:
                logger.debug(f"DRMInfo.set_pssh [pyplayready] error: {exc}")

        # ── 3. Manual ISO box-magic fallback ─────────────────────────────────
        if not detected or detected == "UNK":
            try:
                data = base64.b64decode(pssh_base64)
                if len(data) >= 28 and data[4:8] == b"pssh":
                    sid_bytes = data[12:28]
                    sid_str = "-".join([sid_bytes[0:4].hex(), sid_bytes[4:6].hex(), sid_bytes[6:8].hex(), sid_bytes[8:10].hex(), sid_bytes[10:16].hex()])
                    self.system_id = sid_str
                    sid_lo = sid_str.lower()
                    if sid_lo == self.WIDEVINE_SYSTEM_ID:
                        detected = "WV"
                    elif sid_lo == self.PLAYREADY_SYSTEM_ID:
                        detected = "PR"
                    elif sid_lo == self.FAIRPLAY_SYSTEM_ID:
                        detected = "FP"
                    else:
                        detected = "UNK"
                    logger.debug(f"DRMInfo.set_pssh [manual] detected={detected}")
            except Exception as exc:
                logger.debug(f"DRMInfo.set_pssh [manual] error: {exc}")

        # ── 4-6. Hint / context / UNK fallback ───────────────────────────────
        if not detected or detected == "UNK":
            if drm_type_hint:
                detected = drm_type_hint.upper()
                logger.debug(f"DRMInfo.set_pssh [hint] detected={detected}")
            elif self.drm_type and self.drm_type != "UNK":
                detected = self.drm_type
                logger.debug(f"DRMInfo.set_pssh [context] detected={detected}")
            else:
                detected = "UNK"
                logger.debug("DRMInfo.set_pssh: falling back to UNK")

        # ── Store ─────────────────────────────────────────────────────────────
        self._pssh_by_type[detected] = pssh_base64
        if detected not in self._drm_types:
            self._drm_types.append(detected)

        # self.pssh / self.drm_type always point at the highest-priority type
        for pref in ("WV", "PR", "FP", "UNK"):
            if pref in self._pssh_by_type:
                self.pssh = self._pssh_by_type[pref]
                self.drm_type = pref
                break

    # ─────────────────────────────────────────────────────────────────────────

    def get_pssh_for(self, drm_type: str) -> Optional[str]:
        return self._pssh_by_type.get(drm_type.upper())

    def get_all_drm_types(self) -> List[str]:
        return list(self._drm_types)

    def set_kid(self, kid_hex: str) -> None:
        self.kid = kid_hex.lower().replace("-", "")

    def set_key(self, key_hex: str) -> None:
        self.key = key_hex.lower().replace("-", "")

    def set_method(self, scheme_id_uri: str) -> None:
        if not scheme_id_uri:
            return
        s = scheme_id_uri.lower()

        if "cbcs" in s:
            self.method = "cbcs"
        elif "cenc" in s or "mp4protection" in s:
            self.method = "cenc"
        else:
            self.method = (scheme_id_uri.split(":")[-1] if ":" in scheme_id_uri else scheme_id_uri)

        detected = None
        if self.WIDEVINE_SYSTEM_ID in s or "widevine" in s:
            detected = "WV"
        elif self.PLAYREADY_SYSTEM_ID in s or "playready" in s or "com.microsoft" in s:
            detected = "PR"
        elif self.FAIRPLAY_SYSTEM_ID in s or "fairplay" in s or "com.apple" in s:
            detected = "FP"

        if detected and detected not in self._drm_types:
            self._drm_types.append(detected)
        if not self.drm_type and detected:
            self.drm_type = detected

    def is_encrypted(self) -> bool:
        return bool(self.pssh or self.kid or self.default_kid or self._pssh_by_type)

    def get_drm_display(self) -> str:
        if self._drm_types:
            return "+".join(self._drm_types)
        if self.drm_type:
            return self.drm_type
        if self.default_kid:
            return self.default_kid[:8] + "…"
        return "-"

    def get_key_pair(self) -> Optional[str]:
        kid = self.kid or self.default_kid
        if kid and self.key:
            return f"{kid}:{self.key}"
        return None

    def __repr__(self) -> str:
        if not self.is_encrypted():
            return "DRMInfo(plain)"
        kid = (self.kid or self.default_kid or "")[:8]
        types = "+".join(self._drm_types) if self._drm_types else (self.drm_type or "?")
        return f"DRMInfo({types}, KID={kid}…)"


@dataclass
class Segment:
    url: str
    number: int
    seg_type: str = "media"
    size: int = 0
    downloaded: bool = False

    def __repr__(self) -> str:
        return f"Segment({self.number}, {self.seg_type})"


@dataclass
class Stream:
    """
    Unified stream descriptor for both HLS and DASH content.

    All display helpers (codec, channel, language) delegate to codec.py —
    there is no local translation logic here.
    """
    type: str

    id: str = ""
    resolution: str = ""
    width: int = 0
    height: int = 0
    fps: str = ""
    bitrate: int = 0
    codecs: str = ""
    language: str = "und"
    name: str = ""
    channels: str = ""
    role: str = "main"

    drm: DRMInfo = field(default_factory=DRMInfo)

    encryption_method: Optional[str] = None
    key_uri: Optional[str] = None
    key_data: Optional[bytes] = None
    iv: Optional[str] = None

    playlist_url: Optional[str] = None
    segments: List[Segment] = field(default_factory=list)

    selected: bool = False
    duration: float = 0.0
    format: str = ""

    is_external: bool = False

    def add_segment(self, seg: Segment) -> None:
        self.segments.append(seg)

    @property
    def bitrate_display(self) -> str:
        bw = self.bitrate
        if bw >= 1_000_000:
            return f"{bw / 1e6:.1f} Mbps"
        if bw >= 1_000:
            return f"{bw / 1e3:.0f} Kbps"
        return f"{bw} bps" if bw else "N/A"

    @property
    def fps_float(self) -> float:
        try:
            if "/" in str(self.fps):
                num, den = self.fps.split("/")
                return round(float(num) / float(den), 3)
            return float(self.fps)
        except (TypeError, ValueError, ZeroDivisionError):
            return 0.0

    def get_type_display(self) -> str:
        base = {"video": "Video", "audio": "Audio", "subtitle": "Subtitle", "image": "Thumbnail"}.get(self.type, self.type.capitalize())
        if self.is_external:
            return f"{base} *EXT"
        return base

    def get_duration_display(self) -> str:
        if self.duration <= 0:
            return "N/A"
        h = int(self.duration // 3600)
        m = int((self.duration % 3600) // 60)
        s = int(self.duration % 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def get_short_codec(self) -> str:
        """Delegates to codec.py — single source of truth."""
        from VibraVid.source.utils.codec import get_short_codec as _gsc

        return _gsc(self.type, self.codecs)

    def get_channel_label(self) -> str:
        """Delegates to codec.py — human channel label (e.g. 'Stereo', '5.1')."""
        from VibraVid.source.utils.codec import get_channel_label as _gcl

        return _gcl(self.channels) if self.channels else ""

    def get_language_name(self) -> str:
        """Delegates to codec.py — full English language name."""
        from VibraVid.source.utils.codec import get_language_name as _gln

        return _gln(self.language)

    def __repr__(self) -> str:
        drm_s = f", {self.drm.get_drm_display()}" if self.drm.is_encrypted() else ""
        if self.type == "video":
            return f"Stream(video, {self.resolution}, {self.bitrate_display}{drm_s})"
        if self.type == "audio":
            return f"Stream(audio, {self.language}, {self.bitrate_display}{drm_s})"
        return f"Stream({self.type}, {self.language})"