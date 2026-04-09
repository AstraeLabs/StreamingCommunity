# 13.03.26

from __future__ import annotations

import re
import json
import base64
import binascii
import logging
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from VibraVid.core.manifest.stream import DRMInfo, Stream
from VibraVid.utils.http_client import create_client, get_headers
from VibraVid.utils import config_manager
from VibraVid.core.utils.language import resolve_locale


logger = logging.getLogger(__name__)
timeout = config_manager.config.get_int("REQUESTS", "timeout")

_CC_NAME_RE = re.compile(r"\[CC\]|(?<!\w)CC(?!\w)|closed[- _]captions?|SDH", re.IGNORECASE)
_SDH_NAME_RE = re.compile(r"\[SDH\]|(?<!\w)SDH(?!\w)|hearing[- _]impaired|HI(?!\w)", re.IGNORECASE)
_FORCED_NAME_RE = re.compile(r"\[forced\]|\bforced\b", re.IGNORECASE)
_COMPOUND_LANG_RE = re.compile(r"^(.+?)[-_](forced|cc|sdh|hi|default)$", re.IGNORECASE)


def _make_video_id(s: Stream) -> str:
    """Build a stable synthetic ID for a video variant.
    Priority: STABLE-VARIANT-ID (already in s.id) → vid:{res}@{bw}"""
    if s.id and not s.id.startswith("vid:"):
        return s.id
    res = f"{s.width}x{s.height}" if s.width and s.height else (s.resolution or "?x?")
    return f"vid:{res}@{s.bitrate}"


def _make_rendition_id(group_id: str, language: str, name: str) -> str:
    """Build a stable synthetic ID for an audio/subtitle rendition.
    Priority: STABLE-RENDITION-ID (caller) → {group_id}:{language}"""
    parts = [p for p in (group_id, language or name) if p]
    return ":".join(parts) if parts else "unknown"


_HDR_CODEC_PATTERNS = {
    "dvh1": "DV", "dvhe": "DV",
    "hvc1.2": "HDR10", "hev1.2": "HDR10",
    "hvc1.8": "HDR10", "hev1.8": "HDR10",
    "av01.1": "HDR10", "av01.2": "HDR10",
}


def _infer_video_range_from_codecs(codecs: str) -> str:
    c = (codecs or "").lower()
    for prefix, vrange in _HDR_CODEC_PATTERNS.items():
        if prefix in c:
            return vrange
    return ""


class HLSParser:
    """
    Fetch and parse an HLS master/variant playlist.
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
        path = Path(directory) / "raw.m3u8"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.raw_content or "", encoding="utf-8")
        return path

    def parse_streams(self) -> List[Stream]:
        if not self.raw_content:
            return []

        master_drm = self._parse_drm_tags(self.raw_content)
        streams: List[Stream] = []
        seen_ids: set = set()  # Track seen stream IDs to avoid duplicates (different CDN pathways)
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
                        if not stream.id:
                            stream.id = _make_video_id(stream)
                        streams.append(stream)
                        logger.info(f"HLS add | {stream}")
                i += 2
                continue

            # ── Audio / subtitle / CC rendition ───────────────────────────
            if line.startswith("#EXT-X-MEDIA:"):
                typ = self._attr(line, "TYPE", "").upper()
                if typ == "AUDIO":
                    s = self._parse_media_tag(line, "audio", master_drm)
                    if s:
                        # Skip duplicates: same STABLE-RENDITION-ID for different CDN pathways
                        if s.id not in seen_ids:
                            seen_ids.add(s.id)
                            streams.append(s)
                            logger.info(f"HLS add | {s}")

                elif typ == "SUBTITLES":
                    s = self._parse_media_tag(line, "subtitle", master_drm)
                    if s:
                        if s.id not in seen_ids:
                            seen_ids.add(s.id)
                            streams.append(s)
                            logger.info(f"HLS add | {s}")

                elif typ == "CLOSED-CAPTIONS":
                    s = self._parse_media_tag(line, "subtitle", master_drm)
                    if s:
                        s.is_cc = True
                        instream_id = self._attr(line, "INSTREAM-ID", "")
                        if instream_id and not s.id:
                            s.id = instream_id
                        if s.name and "[CC]" not in s.name:
                            s.name = f"{s.name} [CC]"
                        elif not s.name:
                            s.name = "[CC]"
                        if s.id not in seen_ids:
                            seen_ids.add(s.id)
                            streams.append(s)
                            logger.info(f"HLS add | {s}")

            i += 1

        if not any(s.type == "video" for s in streams):
            streams = self._variant_fallback(streams, master_drm)

        for stream in streams:
            # HLS uses true segments (EXTINF), so live decryption is always possible
            stream.supports_live_decryption = True

        return streams

    def parse_variant(self, variant_url: str) -> DRMInfo:
        """Fetch and parse a variant playlist to find additional DRM info."""
        try:
            hdrs = dict(self.headers)
            hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))
            with create_client(headers=hdrs, timeout=timeout, follow_redirects=True) as c:
                r = c.get(variant_url)
                r.raise_for_status()
                variant_content = r.text
                return self._parse_drm_tags(variant_content)
        except Exception as exc:
            logger.error(f"HLSParser: parse_variant failed for {variant_url}: {exc}")
            return DRMInfo()

    def _parse_stream_inf(self, line: str) -> Stream:
        s = Stream(type="video", format="hls")

        stable_id = self._attr(line, "STABLE-VARIANT-ID", "")
        if stable_id:
            s.id = stable_id

        m = re.search(r"(?<![A-Z-])BANDWIDTH=(\d+)", line)
        if m:
            s.bitrate = int(m.group(1))

        m = re.search(r"AVERAGE-BANDWIDTH=(\d+)", line)
        if m:
            s.avg_bitrate = int(m.group(1))
            s.bitrate = s.avg_bitrate           # Override if AVERAGE-BANDWIDTH is present, as it's more accurate

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

        vr = self._attr(line, "VIDEO-RANGE", "").upper()
        s.video_range = vr if vr else _infer_video_range_from_codecs(s.codecs)

        hdcp = self._attr(line, "HDCP-LEVEL", "").upper()
        if hdcp:
            s.hdcp_level = hdcp

        return s

    def _parse_media_tag(self, line: str, stream_type: str, drm: DRMInfo) -> Optional[Stream]:
        s = Stream(type=stream_type, format="hls")
        s.drm = drm

        stable_id = self._attr(line, "STABLE-RENDITION-ID", "")
        group_id = self._attr(line, "GROUP-ID", "")
        lang = self._attr(line, "LANGUAGE", "")
        name = self._attr(line, "NAME", "")

        if lang:
            # Strip compound suffix from language code before resolving locale.
            # "ita-forced" → base_lang="ita", suffix="forced"
            # "eng-cc"     → base_lang="eng", suffix="cc"
            lang_m = _COMPOUND_LANG_RE.match(lang)
            if lang_m:
                base_lang  = lang_m.group(1)
                lang_suffix = lang_m.group(2).lower()
            else:
                base_lang   = lang
                lang_suffix = ""
            s.language          = lang            # preserve original for filename generation
            s.resolved_language = resolve_locale(base_lang)
        else:
            lang_suffix = ""
        if name:
            s.name = name

        s.id = stable_id if stable_id else _make_rendition_id(group_id, lang, name)

        ch = self._attr(line, "CHANNELS", "")
        if ch:
            s.channels = ch

        uri = self._attr(line, "URI", "")
        if uri:
            s.playlist_url = urljoin(self._base_url, uri)

        s.default    = self._attr(line, "DEFAULT",    "NO").upper() == "YES"
        s.autoselect = self._attr(line, "AUTOSELECT", "NO").upper() == "YES"
        s.forced     = self._attr(line, "FORCED",     "NO").upper() == "YES"

        if not s.forced and stream_type == "subtitle":
            if lang_suffix == "forced":
                s.forced = True
            elif name and _FORCED_NAME_RE.search(name):
                s.forced = True
        
        if s.forced:
            s.default = False

        assoc = self._attr(line, "ASSOC-LANGUAGE", "")
        if assoc:
            s.assoc_language = assoc

        chars = self._attr(line, "CHARACTERISTICS", "")
        if chars:
            s.accessibility = chars
            if "describes-music-and-sound" in chars.lower() or "hearing" in chars.lower():
                s.is_sdh = True

        # is_cc: detect from NAME for TYPE=SUBTITLES
        if not s.is_cc and name and _CC_NAME_RE.search(name):
            s.is_cc = True

        # is_sdh: detect from NAME if not already set via CHARACTERISTICS
        if not s.is_sdh and name and _SDH_NAME_RE.search(name):
            s.is_sdh = True

        # Name annotation for display (non-destructive)
        if s.forced and "[Forced]" not in (s.name or ""):
            s.name = f"{s.name} [Forced]" if s.name else "[Forced]"

        return s

    def _variant_fallback(self, existing: List[Stream], drm: DRMInfo) -> List[Stream]:
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
        s.id = _make_video_id(s)
        logger.info(f"HLS add | {s}")
        return [s] + existing

    def _parse_drm_tags(self, content: str) -> DRMInfo:
        info = DRMInfo()

        aes_m = re.search(r'#EXT-X-(?:SESSION-)?KEY:.*?METHOD=(AES[^,"\s]+)', content, re.IGNORECASE)
        if aes_m:
            info.method = aes_m.group(1)

        key_re = re.compile(r'#EXT-X-(?:SESSION-)?KEY:(.*?)URI="([^"]+)"', re.IGNORECASE | re.DOTALL)
        
        for attrs, full_uri in key_re.findall(content):
            try:
                if full_uri.startswith("data:"):
                    b64 = full_uri.split(",", 1)[-1].strip()
                    b64 = b64.split(";")[0].split('"')[0].strip()
                    
                    try:
                        decoded = base64.b64decode(b64)
                    except binascii.Error:
                        b64c = re.sub(r"[^A-Za-z0-9+/=]", "", b64)
                        while len(b64c) % 4 != 0:
                            b64c += "="
                        decoded = base64.b64decode(b64c)

                    # Check if it's JSON (Apple style)
                    try:
                        js = json.loads(decoded)
                        key_list = []
                        if isinstance(js, list):
                            key_list = js

                        for k in key_list:
                            sys = (k.get("system") or k.get("keyformat") or "").lower()
                            pssh = k.get("pssh")
                            kid = k.get("id")
                            
                            if "widevine" in sys:
                                if pssh: 
                                    info.set_pssh(pssh, "WV")
                            elif "playready" in sys:
                                if pssh: 
                                    info.set_pssh(pssh, "PR")
                            elif "streamingkeydelivery" in sys or "fairplay" in sys:
                                info.method = "SAMPLE-AES"
                                uri = k.get("uri")
                                if uri:
                                    info.set_pssh(uri, "FP")
                            
                            if kid and not info.kid:
                                info.kid = kid
                    except (json.JSONDecodeError, TypeError, AttributeError):
                        is_wv = "edef8ba9" in attrs.lower() or "edef8ba9" in full_uri.lower() or "widevine" in attrs.lower()
                        is_pr = "9a04f079" in attrs.lower() or "9a04f079" in full_uri.lower() or "playready" in attrs.lower() or "com.microsoft" in attrs.lower()
                        
                        if is_wv:
                            info.set_pssh(b64, "WV")
                        elif is_pr:
                            info.set_pssh(b64, "PR")
                        else:
                            info.set_pssh(b64)

                elif full_uri.startswith("skd:"):
                    info.method = "SAMPLE-AES"
                    info.set_pssh(full_uri, "FP")

            except Exception as exc:
                logger.error(f"HLSParser DRM probe error: {exc}")

        return info

    def get_drm_info(self) -> Dict:
        result = {"widevine": [], "playready": [], "fairplay": []}
        if not self.raw_content:
            return result
        info = self._parse_drm_tags(self.raw_content)
        pssh_wv = info.get_pssh_for("WV")
        if pssh_wv:
            result["widevine"].append({"pssh": pssh_wv, "type": "Widevine", "kid": info.kid})
    
        pssh_pr = info.get_pssh_for("PR")
        if pssh_pr:
            result["playready"].append({"pssh": pssh_pr, "type": "PlayReady", "kid": info.kid})

        pssh_fp = info.get_pssh_for("FP")
        if pssh_fp:
            result["fairplay"].append({"uri": pssh_fp, "type": "FairPlay", "kid": info.kid})
            
        return result

    @staticmethod
    def _attr(line: str, key: str, default: str = "") -> str:
        m = re.search(rf'{key}="([^"]*)"', line)
        if m:
            return m.group(1)
        m = re.search(rf"{key}=([^,\s]+)", line)
        if m:
            return m.group(1)
        return default