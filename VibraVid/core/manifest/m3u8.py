# 13.03.26

from __future__ import annotations

import re
import base64
import binascii
import logging
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from VibraVid.core.manifest.stream import DRMInfo, Segment, Stream
from VibraVid.utils.http_client import create_client, get_headers
from VibraVid.utils import config_manager


logger = logging.getLogger(__name__)
timeout = config_manager.config.get_int("REQUESTS", "timeout", default=30)


class HLSParser:
    """
    Fetch and parse an HLS master/variant playlist.

    Usage::

        parser = HLSParser(url, headers)
        parser.fetch_manifest()                # or pass content= to skip fetch
        streams = parser.parse_streams()
        raw_text = parser.raw_content          # save to temp dir if needed
    """
    def __init__(self, m3u8_url: str, headers: Dict[str, str] = None, content: str = None):
        self.m3u8_url = m3u8_url
        self.headers = headers or {}
        self._injected = content
        self.raw_content: Optional[str] = content
        self._base_url = self._calc_base_url(m3u8_url)

    @staticmethod
    def _calc_base_url(url: str) -> str:
        p = urlparse(url)
        path = p.path.rsplit("/", 1)[0]
        return f"{p.scheme}://{p.netloc}{path}/"

    def fetch_manifest(self) -> bool:
        """Fetch the manifest; uses injected content if already available."""
        if self._injected:
            self.raw_content = self._injected
            return True

        try:
            hdrs = dict(self.headers)
            hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))

            with create_client(headers=hdrs, timeout=timeout, follow_redirects=True) as c:
                r = c.get(self.m3u8_url)
                r.raise_for_status()
                self.raw_content = r.text
            return True
        except Exception as exc:
            logger.error(f"HLSParser: fetch failed: {exc}")
            return False

    def save_raw(self, directory: Path) -> Path:
        """Write raw_content to *directory*/raw.m3u8 and return the path."""
        path = Path(directory) / "raw.m3u8"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.raw_content or "", encoding="utf-8")
        return path

    def parse_streams(self) -> List[Stream]:
        """
        Parse the master playlist and return a list of Stream objects.

        Handles:
          • #EXT-X-STREAM-INF       → video variant streams
          • #EXT-X-MEDIA TYPE=AUDIO → audio renditions
          • #EXT-X-MEDIA TYPE=SUBTITLES → subtitle renditions
          • #EXT-X-KEY / #EXT-X-SESSION-KEY → DRM / AES-128 per-stream

        If no #EXT-X-STREAM-INF is found (single-rendition / variant playlist),
        a minimal fallback video Stream is synthesised from #EXTINF data.
        """
        if not self.raw_content:
            return []

        # Parse DRM tags once from the master
        master_drm = self._parse_drm_tags(self.raw_content)

        streams: List[Stream] = []
        lines = self.raw_content.splitlines()
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # ── Video variant ─────────────────────────────────────────────
            if line.startswith("#EXT-X-STREAM-INF:"):
                stream = self._parse_stream_inf(line)
                stream.drm = master_drm
                stream.format = "hls"
                if i + 1 < len(lines):
                    nxt = lines[i + 1].strip()
                    if nxt and not nxt.startswith("#"):
                        stream.playlist_url = urljoin(self._base_url, nxt)
                        streams.append(stream)
                i += 2
                continue

            # ── Audio / subtitle rendition ────────────────────────────────
            if line.startswith("#EXT-X-MEDIA:"):
                typ = self._attr(line, "TYPE", "").upper()
                if typ == "AUDIO":
                    s = self._parse_media_tag(line, "audio", master_drm)
                    if s:
                        streams.append(s)
                elif typ == "SUBTITLES":
                    s = self._parse_media_tag(line, "subtitle", master_drm)
                    if s:
                        streams.append(s)
                elif typ == "CLOSED-CAPTIONS":
                    s = self._parse_media_tag(line, "subtitle", master_drm)
                    if s:
                        s.name = f"{s.name} [CC]" if s.name else "[CC]"
                        streams.append(s)

            i += 1

        # Fallback: treat as variant / media playlist
        if not any(s.type == "video" for s in streams):
            streams = self._variant_fallback(streams, master_drm)

        return streams

    def _parse_stream_inf(self, line: str) -> Stream:
        s = Stream(type="video", format="hls")

        m = re.search(r"BANDWIDTH=(\d+)", line)
        if m:
            s.bitrate = int(m.group(1))

        m = re.search(r"RESOLUTION=(\d+)x(\d+)", line)
        if m:
            s.width = int(m.group(1))
            s.height = int(m.group(2))
            s.resolution = f"{s.width}x{s.height}"

        m = re.search(r"FRAME-RATE=([\d.]+)", line)
        if m:
            s.fps = m.group(1)

        m = re.search(r'CODECS="([^"]+)"', line)
        if m:
            s.codecs = m.group(1)

        return s

    def _parse_media_tag(self, line: str, stream_type: str, drm: DRMInfo) -> Optional[Stream]:
        s = Stream(type=stream_type, format="hls")
        s.drm = drm

        m = re.search(r'LANGUAGE="([^"]+)"', line)
        if m:
            s.language = m.group(1)

        m = re.search(r'NAME="([^"]+)"', line)
        if m:
            s.name = m.group(1)

        m = re.search(r'GROUP-ID="([^"]+)"', line)
        if m:
            s.id = m.group(1)

        m = re.search(r'CHANNELS="([^"]+)"', line)
        if m:
            s.channels = m.group(1)

        m = re.search(r'URI="([^"]+)"', line)
        if m:
            s.playlist_url = urljoin(self._base_url, m.group(1))

        # Detect FORCED attribute
        forced = self._attr(line, "FORCED", "NO").upper()
        if forced == "YES":
            s.name = f"{s.name} [Forced]" if s.name else "[Forced]"

        # No URI → muxed track; still valid, return it
        return s

    def _variant_fallback(self, existing: List[Stream], drm: DRMInfo) -> List[Stream]:
        """Build a minimal video Stream when the manifest is already a variant playlist."""
        total_dur = 0.0
        bandwidth = 0

        for line in (self.raw_content or "").splitlines():
            line = line.strip()
            if line.startswith("#EXTINF:"):
                m = re.search(r"#EXTINF:([\d.]+)", line)
                if m:
                    total_dur += float(m.group(1))
            elif line.startswith("#EXT-X-STREAM-INF:"):
                m = re.search(r"BANDWIDTH=(\d+)", line)
                if m:
                    bandwidth = int(m.group(1))

        s = Stream(type="video", format="hls")
        s.bitrate = bandwidth
        s.duration = total_dur
        s.drm = drm
        s.playlist_url = self.m3u8_url
        return [s] + existing

    def _parse_drm_tags(self, content: str) -> DRMInfo:
        """
        Extract DRM / encryption info from #EXT-X-KEY and #EXT-X-SESSION-KEY.

        Detects Widevine, PlayReady, and FairPlay (skd:// URI or 94ce86fb scheme).
        For AES-128 the method is AES-128 and URI points to a key file.
        """
        info = DRMInfo()

        # ── AES-128 / SAMPLE-AES ─────────────────────────────────────────
        aes_m = re.search(r'#EXT-X-(?:SESSION-)?KEY:.*?METHOD=(AES[^,"\s]+)', content, re.IGNORECASE)
        if aes_m:
            info.method = aes_m.group(1)

        # ── FairPlay: skd:// URI ──────────────────────────────────────────
        fp_skd = re.search(r'#EXT-X-(?:SESSION-)?KEY:.*?URI="(skd://[^"]+)"', content, re.IGNORECASE)
        if fp_skd:
            info.drm_type = "FP"
            info.method = "cbcs"
            return info

        # ── Widevine / PlayReady via data: URI ────────────────────────────
        key_re = re.compile(r'#EXT-X-(?:SESSION-)?KEY:(.*?)URI="(data:[^"]+)"', re.IGNORECASE | re.DOTALL)
        seen = set()

        for attrs, full_uri in key_re.findall(content):
            try:
                b64 = full_uri.split(",", 1)[-1].split(";")[0].split('"')[0].strip()

                is_wv = ("edef8ba9" in attrs.lower() or "edef8ba9" in full_uri.lower() or "widevine" in attrs.lower())
                is_pr = ("9a04f079" in attrs.lower() or "9a04f079" in full_uri.lower() or "playready" in attrs.lower() or "com.microsoft" in attrs.lower())
                is_fp = ("94ce86fb" in attrs.lower() or "94ce86fb" in full_uri.lower() or "fairplay" in attrs.lower() or "com.apple" in attrs.lower())

                try:
                    decoded = base64.b64decode(b64)
                except binascii.Error:
                    b64c = re.sub(r"[^A-Za-z0-9+/=]", "", b64)
                    decoded = base64.b64decode(b64c)

                if b64 in seen:
                    continue
                seen.add(b64)

                # ── FairPlay ───────────────────────────────────────────
                if is_fp:
                    info.set_pssh(b64)
                    info.drm_type = "FP"
                    return info

                # ── Widevine probe ─────────────────────────────────────
                try:
                    from pywidevine.pssh import PSSH
                    PSSH(decoded)
                    info.set_pssh(b64)
                    info.drm_type = "WV"
                    return info
                except Exception:
                    pass

                if is_wv:
                    info.set_pssh(b64)
                    info.drm_type = "WV"
                    return info

                # ── PlayReady probe ────────────────────────────────────
                try:
                    from pyplayready.system.pssh import PSSH as PR_PSSH
                    PR_PSSH(decoded)
                    info.set_pssh(b64)
                    info.drm_type = "PR"
                    return info
                except Exception:
                    pass

                if is_pr:
                    info.set_pssh(b64)
                    info.drm_type = "PR"
                    return info

            except Exception as exc:
                logger.debug(f"HLSParser DRM probe error: {exc}")

        return info

    def get_drm_info(self) -> Dict:
        """Return {"widevine": [...], "playready": [...], "fairplay": [...]} PSSH lists."""
        result = {"widevine": [], "playready": [], "fairplay": []}
        if not self.raw_content:
            return result
        info = self._parse_drm_tags(self.raw_content)
        if info.drm_type == "WV" and info.pssh:
            result["widevine"].append({"pssh": info.pssh, "type": "Widevine"})
        elif info.drm_type == "PR" and info.pssh:
            result["playready"].append({"pssh": info.pssh, "type": "PlayReady"})
        elif info.drm_type == "FP" and info.pssh:
            result["fairplay"].append({"pssh": info.pssh, "type": "FairPlay"})
        return result

    def get_kids(self, pssh_list: list) -> list:
        """Extract KIDs from a list of PSSH dicts (best-effort)."""
        kids = []
        for item in pssh_list:
            pssh_b64 = item["pssh"] if isinstance(item, dict) else item
            try:
                data = base64.b64decode(pssh_b64)
                # KID is at offset 32 in a Widevine PSSH (after box header + system_id)
                if len(data) >= 48:
                    kids.append(data[32:48].hex())
                else:
                    kids.append("")
            except Exception:
                kids.append("")
        return kids

    def fetch_segments(self, playlist_url: str):
        """
        Fetch a variant / media playlist and return its segments.

        Returns:
            (segments, bandwidth, encryption_method, key_uri, iv, total_duration)
        """
        try:
            timeout = config_manager.config.get_int("REQUESTS", "timeout", default=30)

            with create_client(
                headers=self.headers, timeout=timeout, follow_redirects=True
            ) as c:
                r = c.get(playlist_url)
                r.raise_for_status()
                content = r.text

            if "WEBVTT" in content[:100]:
                return [Segment(playlist_url, 1, "media")], None, None, None, None, 0.0

            base = playlist_url.rsplit("/", 1)[0] + "/"
            segments: List[Segment] = []
            bandwidth = None
            enc_method = None
            key_uri = None
            iv = None
            total_dur = 0.0

            for i, line in enumerate(content.splitlines()):
                line = line.strip()

                if line.startswith("#EXT-X-STREAM-INF:"):
                    m = re.search(r"BANDWIDTH=(\d+)", line)
                    if m:
                        bandwidth = int(m.group(1))

                elif line.startswith("#EXT-X-KEY:"):
                    m_method = re.search(r"METHOD=([^,\s]+)", line)
                    m_uri = re.search(r'URI="([^"]+)"', line)
                    m_iv = re.search(r"IV=0x([0-9a-fA-F]+)", line)
                    if m_method and m_uri:
                        enc_method = m_method.group(1)
                        key_uri = urljoin(base, m_uri.group(1))
                        iv = m_iv.group(1) if m_iv else None

                elif line.startswith("#EXTINF:"):
                    m = re.search(r"#EXTINF:([\d.]+)", line)
                    if m:
                        total_dur += float(m.group(1))
                    for nxt in content.splitlines()[i + 1 :]:
                        nxt = nxt.strip()
                        if nxt and not nxt.startswith("#"):
                            segments.append(
                                Segment(urljoin(base, nxt), len(segments) + 1, "media")
                            )
                            break

            if not segments:
                for line in content.splitlines():
                    line = line.strip()
                    if line and not line.startswith("#"):
                        segments.append(
                            Segment(urljoin(base, line), len(segments) + 1, "media")
                        )

            return segments, bandwidth, enc_method, key_uri, iv, total_dur

        except Exception as exc:
            logger.error(f"HLSParser.fetch_segments error: {exc}")
            return [], None, None, None, None, 0.0

    @staticmethod
    def _attr(line: str, key: str, default: str = "") -> str:
        """Extract an attribute value from a HLS tag line."""
        m = re.search(rf'{key}="([^"]*)"', line)
        if m:
            return m.group(1)
        m = re.search(rf"{key}=([^,\s]+)", line)
        if m:
            return m.group(1)
        return default