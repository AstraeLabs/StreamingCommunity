# 22.02.25

import logging

from VibraVid.core.decryptor._models import detect_encryption_info
from VibraVid.core.drm.system import KNOWN_DRM_SYSTEMS
from VibraVid.utils.os import os_manager

logger = logging.getLogger(__name__)
PROBE_BYTES = 1 * 1024 * 1024  # 1 MB — safety-net ceiling for in-flight accumulation
PROBE_BYTES_FAST = 92 * 1024  # 92 KB — cheap preflight Range probe tried before the real download starts


class DRMProbe:
    def __init__(self, known_systems: dict | None = None) -> None:
        self._systems: dict[str, str] = known_systems if known_systems is not None else KNOWN_DRM_SYSTEMS

    def probe(self, url: str, headers: dict, client, size: int = PROBE_BYTES) -> tuple:
        """Returns ``(encrypted: bool, scheme: str | None, drm_names: list[str], kid: str | None, pssh_b64: str | None)``."""
        try:
            raw = self._fetch_bytes(url, headers, client, size=size)
            if not raw:
                return False, None, [], None, None

            info = self._parse_bytes(raw)
            if not info.encrypted:
                logger.debug("DRMProbe: no encryption markers found in first 4 MB.")
                return False, None, [], None, None

            drm_names = self._resolve_drm_names(info.pssh_boxes)
            self._report(info.scheme, info.kid, drm_names)
            return True, info.scheme, drm_names, info.kid, info.pssh_b64

        except Exception as exc:
            logger.debug(f"DRMProbe failed (non-fatal): {exc}")
            return False, None, [], None, None

    def inspect(self, raw: bytes) -> tuple:
        """Inspect already-downloaded bytes (in-flight probe, no second request).
        Returns ``(encrypted, scheme, drm_names, kid, pssh_b64)``."""
        try:
            if not raw:
                return False, None, [], None, None

            info = self._parse_bytes(raw)
            if not info.encrypted:
                logger.debug("DRMProbe: no encryption markers found in first 4 MB.")
                return False, None, [], None, None

            drm_names = self._resolve_drm_names(info.pssh_boxes)
            self._report(info.scheme, info.kid, drm_names)
            return True, info.scheme, drm_names, info.kid, info.pssh_b64

        except Exception as exc:
            logger.debug(f"DRMProbe.inspect failed (non-fatal): {exc}")
            return False, None, [], None, None

    def _fetch_bytes(self, url: str, headers: dict, client, size: int = PROBE_BYTES) -> bytes | None:
        """Fetch the first *size* bytes of the URL using a Range request, returning the raw bytes (or None on failure)."""
        probe_headers = {**headers, "Range": f"bytes=0-{size - 1}"}
        resp = client.get(url, headers=probe_headers, timeout=15)

        if resp.status_code not in (200, 206):
            logger.debug(f"DRMProbe: unexpected status {resp.status_code} — skipping.")
            return None

        raw = resp.content[:size]
        return raw if raw else None

    @staticmethod
    def _parse_bytes(raw: bytes):
        """Write *raw* to a temp file, run ``detect_encryption_info``, then delete."""
        with os_manager.temp_binary_file(raw, suffix=".mp4probe") as tmp_path:
            return detect_encryption_info(tmp_path)

    def _resolve_drm_names(self, pssh_boxes: list) -> list:
        """Given a list of PSSH boxes, return a list of known DRM system names (if any) and log any unknown system IDs."""
        known_names: list[str] = []
        unknown_ids: list[str] = []
        seen_known: set[str] = set()
        seen_unknown: set[str] = set()

        for box in pssh_boxes:
            sid = box.get("system_id", "").replace(" ", "").lower()
            if not sid:
                continue

            name = self._systems.get(sid)
            if name:
                if name not in seen_known:
                    seen_known.add(name)
                    known_names.append(name)
                continue

            if sid not in seen_unknown:
                seen_unknown.add(sid)
                unknown_ids.append(sid)

        if unknown_ids:
            logger.debug(f"DRMProbe: unknown system_id(s): {', '.join(unknown_ids)}")

        if known_names and unknown_ids:
            return [*known_names, f"Unknown SID x{len(unknown_ids)}"]

        if known_names:
            return known_names

        if unknown_ids:
            return [f"Unknown ({sid[:8]}...)" for sid in unknown_ids]
        return ["Unknown"]

    @staticmethod
    def _report(scheme: str | None, kid: str | None, drm_names: list) -> None:
        """Log a summary of the detected encryption info."""
        label = ", ".join(drm_names) if drm_names else "unknown DRM"
        logger.info(f"DRMProbe: encryption detected — scheme={scheme or 'unknown'}, kid={kid or 'n/a'}, DRM=[{label}]")
