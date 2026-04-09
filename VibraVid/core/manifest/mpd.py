# 13.03.26

from __future__ import annotations

import math
import re
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from VibraVid.core.manifest.stream import DRMInfo, Segment, Stream
from VibraVid.utils.http_client import create_client, get_headers
from VibraVid.utils import config_manager
from VibraVid.core.utils.language import resolve_locale
from VibraVid.core.utils.codec import (DV_CODEC_PREFIXES, detect_stream_type)


logger = logging.getLogger(__name__)
_NS = {
    "mpd": "urn:mpeg:dash:schema:mpd:2011",
    "cenc": "urn:mpeg:cenc:2013",
}
_SCHEME_DRM_MAP = {
    DRMInfo.WIDEVINE_SYSTEM_ID: "WV", "edef8ba9": "WV", "widevine": "WV",
    DRMInfo.PLAYREADY_SYSTEM_ID: "PR", "9a04f079": "PR", "playready": "PR", "com.microsoft": "PR",
    DRMInfo.FAIRPLAY_SYSTEM_ID:  "FP", "94ce86fb": "FP", "fairplay": "FP", "com.apple": "FP",
}
_TC_MAP = {
    "1": "SDR", "6": "SDR", "7": "SDR", "13": "SDR", "14": "SDR", "15": "SDR",
    "16": "PQ",   # SMPTE ST 2084 (HDR10 / Dolby Vision PQ)
    "18": "HLG",  # ARIB STD-B67
}
_CP_HDR_HINT = {"9"}  # BT.2020 ColourPrimaries


def _norm(v: Optional[str]) -> str:
    return (v or "").strip().lower()


def _stream_dedup_key(s: Stream):
    """Return a stable key used to drop duplicate streams across repeated MPD periods."""
    sid = _norm(getattr(s, "id", ""))
    if sid and sid != "ext" and not sid.startswith("vid:"):
        return (s.type, "id", sid)

    if s.type == "video":
        return (
            s.type,
            _norm(s.codecs),
            int(s.width or 0),
            int(s.height or 0),
            int(s.bitrate or 0),
            _norm(s.video_range),
        )

    if s.type == "audio":
        return (
            s.type,
            _norm(s.language),
            _norm(s.codecs),
            _norm(s.channels),
            int(s.sample_rate or 0),
            int(s.bitrate or 0),
            bool(s.is_sdh),
            bool(s.forced),
            bool(s.is_cc),
        )
    
    return (
        s.type,
        _norm(s.language),
        _norm(s.codecs),
        bool(s.forced),
        bool(s.is_cc),
        bool(s.is_sdh),
    )


def _drm_hint_from_scheme(scheme_lower: str) -> Optional[str]:
    for fragment, dtype in _SCHEME_DRM_MAP.items():
        if fragment in scheme_lower:
            return dtype
    return None


def _video_range_from_codecs(codecs: str) -> str:
    """Infer HDR type from codec string prefix."""
    c = (codecs or "").lower()
    for prefix in DV_CODEC_PREFIXES:
        if c.startswith(prefix) or f",{prefix}" in c:
            return "DV"
    if re.search(r"hvc1\.2\.|hev1\.2\.", c):
        return "HDR10"
    if re.search(r"hvc1\.8\.|hev1\.8\.", c):
        return "HDR10"
    if re.search(r"av01\.[12]\.", c):
        return "HDR10"
    return ""


def _is_ad_period(period_url: str, period_element) -> bool:
    """Check if a period is advertisement content.
    
    Advertisement periods typically:
    - Have "/ad/" in their base URL (e.g., https://.../ad/ixmedia/encoding-xxx/)
    - Have no content protection (DRM)
    """
    is_ad_presente = "/ad/" in period_url.lower()
    has_drm = period_element.find(".//mpd:ContentProtection", _NS) is not None
    
    logger.info(f"_is_ad_period | url={period_url} | has_/ad/={is_ad_presente} | has_drm={has_drm}")
    
    if is_ad_presente and not has_drm:
        return True
    return False


def _is_file_url(url: str) -> bool:
    """Best-effort detection for URLs that point to a file instead of a directory."""
    try:
        path = (urlparse(url).path or "").rstrip("/")
        tail = path.rsplit("/", 1)[-1]
        return bool(tail and "." in tail)
    except Exception:
        return False



class DashParser:
    """
    Fetch and parse an MPEG-DASH MPD manifest.
    """
    def __init__(self, mpd_url: str, headers: Dict[str, str] = None, provided_kid: str = None, content: str = None):
        self.mpd_url = mpd_url
        self.headers = headers or {}
        self.provided_kid = provided_kid
        self._injected = content
        self.raw_content: Optional[str] = content
        self._root: Optional[ET.Element] = None
        self._base_url = self._calc_base_url(mpd_url)
        self._uses_range_split: bool = False

    @staticmethod
    def _calc_base_url(url: str) -> str:
        p = urlparse(url)
        path = p.path.rsplit("/", 1)[0]
        return f"{p.scheme}://{p.netloc}{path}/"

    def fetch_manifest(self) -> bool:
        if self._injected:
            self.raw_content = self._injected
            try:
                self._root = ET.fromstring(self.raw_content)
                self._resolve_base_url()
                return True
            except ET.ParseError as exc:
                logger.error(f"DashParser: injected XML parse error: {exc}")
                self._injected = None

        try:
            timeout = config_manager.config.get_int("REQUESTS", "timeout")
            hdrs = dict(self.headers)
            hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))
            with create_client(headers=hdrs, timeout=timeout, follow_redirects=True) as c:
                r = c.get(self.mpd_url)
                r.raise_for_status()
                self.raw_content = r.text
            self._root = ET.fromstring(self.raw_content)
            self._resolve_base_url()
            return True
        except Exception as exc:
            logger.error(f"DashParser: fetch/parse failed: {exc}")
            return False

    def save_raw(self, directory: Path) -> Path:
        path = Path(directory) / "raw.mpd"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.raw_content or "", encoding="utf-8")
        return path

    def parse_streams(self) -> List[Stream]:
        if self._root is None:
            return []

        streams: List[Stream] = []
        dur_str = self._root.get("mediaPresentationDuration", "")
        global_duration = self._parse_iso_duration(dur_str)

        all_periods = self._root.findall("mpd:Period", _NS)
        global_seen_keys: set = set()

        # Reset range-split tracking
        self._uses_range_split = False

        for period_idx, period in enumerate(all_periods):
            period_base_url = self._resolve_element_base_url(period, self._base_url)
            if _is_ad_period(period_base_url, period):
                logger.info(f"DASH period skipped (advertisement) | period={period_idx} url={period_base_url}")
                continue
            
            period_start = self._parse_iso_duration(period.get("start", ""))
            period_duration = self._parse_iso_duration(period.get("duration", dur_str))

            for adapt_idx, adapt in enumerate(period.findall("mpd:AdaptationSet", _NS)):
                adapt_base_url = self._resolve_element_base_url(adapt, period_base_url)
                mime = (adapt.get("contentType") or adapt.get("mimeType") or "").lower()

                if "video" in mime:
                    stype = "video"
                elif "audio" in mime:
                    stype = "audio"
                elif "text" in mime or "subtitle" in mime:
                    stype = "subtitle"
                elif "image" in mime:
                    continue
                else:
                    # Last-resort: codec-based detection via codec.py constants
                    first_rep = adapt.find(".//mpd:Representation", _NS)
                    codecs_hint = (
                        (first_rep.get("codecs") or adapt.get("codecs") or "").lower()
                        if first_rep is not None else ""
                    )
                    stype = detect_stream_type(codecs_hint)
                    if not stype:
                        logger.info(f"DashParser: AdaptationSet skipped — unknown mime={mime!r} codecs={codecs_hint!r}")
                        continue

                adapt_drm = self._extract_drm(adapt)

                for rep in adapt.findall("mpd:Representation", _NS):
                    rep_id = rep.get("id", "")
                    rep_base_url = self._resolve_element_base_url(rep, adapt_base_url)
                    if "/ad/" in rep_base_url.lower() and not adapt_drm.is_encrypted():
                        logger.info(f"DASH stream skipped (advertisement) | id={rep_id!r} period={period_idx} url={rep_base_url}")
                        continue
                    
                    s = self._parse_representation(rep, adapt, stype, adapt_drm, global_duration or period_duration, period_start, adapt_base_url)
                    if s is None:
                        continue

                    dedup_key = _stream_dedup_key(s)
                    if dedup_key in global_seen_keys:
                        logger.info(f"DASH stream skipped (duplicate) | id={rep_id!r} period={period_idx} lang={s.language} bw={s.bitrate} codec={s.codecs}")
                        continue
                    global_seen_keys.add(dedup_key)

                    streams.append(s)
                    logger.info(f"DASH add | {s}")

        # After parsing all streams, log if range-split was detected anywhere
        if self._uses_range_split:
            logger.info("DASH manifest uses range-split (SegmentBase with byte ranges). Live decryption is disabled for all streams in this manifest.")
            
        return streams

    def _parse_representation(self, rep, adapt, stype, adapt_drm, global_dur, period_start, base_url):
        rep_id = rep.get("id", "")
        bandwidth = int(rep.get("bandwidth", 0))
        avg_bw = int(rep.get("averageBandwidth", 0)) or int(adapt.get("averageBandwidth", 0))
        rep_base_url = self._resolve_element_base_url(rep, base_url)

        s = Stream(type=stype, id=rep_id, format="dash")
        s.bitrate = bandwidth
        s.avg_bitrate = avg_bw
        s.duration = global_dur

        role_el = adapt.find(".//mpd:Role", _NS)
        if role_el is not None:
            s.role = role_el.get("value", "main")

        label_el = rep.find("mpd:Label", _NS) or adapt.find("mpd:Label", _NS)
        if label_el is not None and label_el.text:
            s.label = label_el.text.strip()

        if stype == "video":
            self._parse_video_fields(rep, adapt, s)
        elif stype == "audio":
            self._parse_audio_fields(rep, adapt, s)
        elif stype == "subtitle":
            self._parse_subtitle_fields(rep, adapt, s)

        # DRM: rep overrides adapt; merge when both present
        rep_drm = self._extract_drm(rep)
        if rep_drm.is_encrypted() and adapt_drm.is_encrypted():
            for dtype, pssh in adapt_drm._pssh_by_type.items():
                if dtype not in rep_drm._pssh_by_type:
                    rep_drm.set_pssh(pssh, drm_type_hint=dtype)
            if not rep_drm.kid and adapt_drm.kid:
                rep_drm.set_kid(adapt_drm.kid)
            if not rep_drm.default_kid and adapt_drm.default_kid:
                rep_drm.default_kid = adapt_drm.default_kid
            s.drm = rep_drm
        elif rep_drm.is_encrypted():
            s.drm = rep_drm
        elif adapt_drm.is_encrypted():
            s.drm = adapt_drm
        else:
            s.drm = DRMInfo()

        # Segments: SegmentTemplate > SegmentList > BaseURL single-file
        tmpl = rep.find("mpd:SegmentTemplate", _NS) or adapt.find("mpd:SegmentTemplate", _NS)
        seg_list = rep.find("mpd:SegmentList", _NS) or adapt.find("mpd:SegmentList", _NS)
        seg_base = rep.find("mpd:SegmentBase", _NS) or adapt.find("mpd:SegmentBase", _NS)
        
        if tmpl is not None:
            self._apply_segment_template(tmpl, rep_id, s, period_start, rep_base_url)
            s.supports_live_decryption = True  # True segments available
        elif seg_list is not None:
            self._apply_segment_list(seg_list, s, rep_base_url)
            s.supports_live_decryption = True  # True segments available
        elif seg_base is not None:
            # SegmentBase: may be range-split (byte ranges) or simple single-file
            # Live decryption NOT suitable for range-split files (no true segments)
            s.supports_live_decryption = False
            self._uses_range_split = True
            
            index_range = seg_base.get("indexRange", "")
            media_range = seg_base.find("mpd:Initialization", _NS)
            media_range = media_range.get("range", "") if media_range is not None else ""
            
            if index_range or media_range:
                logger.info(f"DASH range-split detected for stream {rep_id!r}: indexRange={index_range!r}, mediaRange={media_range!r}. Live decryption disabled.")
            
            # Get the media URL
            rep_base = rep.find("mpd:BaseURL", _NS)
            if rep_base is not None and rep_base.text:
                rep_rel = rep_base.text.strip()
                if rep_base_url.rstrip("/").endswith(rep_rel):
                    media_url = rep_base_url.rstrip("/")
                else:
                    media_url = urljoin(rep_base_url, rep_rel)
            else:
                media_url = rep_base_url.rstrip("/")
            
            # Add ONLY the media segment (no explicit byte_range)
            # The downloader will detect single-file media and call _build_dash_ranged_segments()
            # which will split it into chunks automatically
            s.add_segment(Segment(media_url, 0, "media"))
        else:
            # No segmentation info - single file
            rep_base = rep.find("mpd:BaseURL", _NS)
            if rep_base is not None and rep_base.text:
                rep_rel = rep_base.text.strip()
                if rep_base_url.rstrip("/").endswith(rep_rel):
                    media_url = rep_base_url.rstrip("/")
                else:
                    media_url = urljoin(rep_base_url, rep_rel)
                s.add_segment(Segment(media_url, 0, "media"))
            else:
                s.add_segment(Segment(rep_base_url.rstrip("/"), 0, "media"))
            
            # Single file without ranges might still work with live decryption if we split it
            # But safer to mark as False unless we know it's segmented
            s.supports_live_decryption = False

        return s

    def _parse_video_fields(self, rep, adapt, s):
        s.width = int(rep.get("width", 0))
        s.height = int(rep.get("height", 0))
        s.resolution = f"{s.width}x{s.height}" if s.width and s.height else ""
        s.fps = rep.get("frameRate", adapt.get("frameRate", ""))
        s.codecs = rep.get("codecs") or adapt.get("codecs", "")
        s.scan_type = (rep.get("scanType") or adapt.get("scanType") or "").lower()
        s.video_range = (self._extract_video_range(rep) or self._extract_video_range(adapt) or _video_range_from_codecs(s.codecs))

    def _parse_audio_fields(self, rep, adapt, s):
        raw_lang = adapt.get("lang", "und")
        s.language = raw_lang
        s.resolved_language = resolve_locale(raw_lang)
        s.codecs = rep.get("codecs") or adapt.get("codecs", "")

        for acc_el in adapt.findall(".//mpd:AudioChannelConfiguration", _NS):
            val = acc_el.get("value", "")
            if val:
                s.channels = val
                break
        if not s.channels:
            s.channels = rep.get("audioChannelConfiguration", adapt.get("audioChannelConfiguration", ""))

        # Sample rate: element first, then attribute (space-sep → take last/highest)
        sr_el = rep.find("mpd:AudioSamplingRate", _NS) or adapt.find("mpd:AudioSamplingRate", _NS)
        if sr_el is not None and sr_el.text:
            try:
                s.sample_rate = int(sr_el.text.strip().split()[0])
            except ValueError:
                pass
        if not s.sample_rate:
            sr_attr = rep.get("audioSamplingRate") or adapt.get("audioSamplingRate")
            if sr_attr:
                try:
                    s.sample_rate = int(sr_attr.strip().split()[-1])
                except ValueError:
                    pass

        s.is_sdh = self._is_accessibility_sdh(rep) or self._is_accessibility_sdh(adapt)

    def _parse_subtitle_fields(self, rep, adapt, s):
        raw_lang = adapt.get("lang", "und")
        s.language = raw_lang
        s.resolved_language = resolve_locale(raw_lang)
        s.codecs = rep.get("codecs") or adapt.get("codecs", "")

        role_values = {el.get("value", "").lower() for el in adapt.findall(".//mpd:Role", _NS)}
        if "forced-subtitle" in role_values or "forced_subtitle" in role_values:
            s.forced = True
        if "caption" in role_values or "captions" in role_values:
            s.is_cc = True

        s.is_sdh = self._is_accessibility_sdh(adapt)

        if s.forced and not s.name:
            s.name = f"{s.language} [Forced]"
        elif s.is_cc and not s.name:
            s.name = f"{s.language} [CC]"
    
    def _extract_video_range(self, element) -> str:
        tc_value = None
        cp_value = None
        for tag in ("mpd:SupplementalProperty", "mpd:EssentialProperty"):
            for prop in element.findall(f".//{tag}", _NS):
                scheme = (prop.get("schemeIdUri") or "").lower()
                value = (prop.get("value") or "").strip()
                if "dolbyvision" in scheme or "dolby" in scheme:
                    return "DV"
                val_up = value.upper()
                if val_up in ("HDR10", "HLG", "PQ", "HDR", "DV"):
                    return val_up
                if "transfercharacteristics" in scheme:
                    tc_value = value
                if "colourprimaries" in scheme or "colorprimaries" in scheme:
                    cp_value = value
        if tc_value and tc_value in _TC_MAP:
            vr = _TC_MAP[tc_value]
            return vr if vr != "SDR" else ""
        if cp_value in _CP_HDR_HINT:
            return "HDR10"
        return ""

    
    def _is_accessibility_sdh(self, element) -> bool:
        for acc in element.findall(".//mpd:Accessibility", _NS):
            scheme = (acc.get("schemeIdUri") or "").lower()
            value = (acc.get("value") or "").lower().strip()
            if "audiopurpose" in scheme and value == "1":
                return True
            if "role" in scheme and value in ("caption", "description"):
                return True
            if value in {"1", "2", "hearing-impaired", "description", "caption"}:
                return True
        return False

    def _extract_drm(self, element) -> DRMInfo:
        info = DRMInfo()
        for cp in element.findall(".//mpd:ContentProtection", _NS):
            scheme = (cp.get("schemeIdUri") or "").lower()
            info.set_method(scheme)
            drm_hint = _drm_hint_from_scheme(scheme)
            
            # Try to find PSSH in multiple locations:
            # 1. cenc:pssh
            pssh_el = cp.find(".//cenc:pssh", _NS)
            
            # 2. mpd:pssh
            if pssh_el is None:
                pssh_el = cp.find("mpd:pssh", _NS)
            
            # 3. direct child pssh without namespace
            if pssh_el is None:
                pssh_el = cp.find("pssh")
            
            if pssh_el is not None and pssh_el.text:
                info.set_pssh(pssh_el.text.strip(), drm_type_hint=drm_hint)
            
            # PlayReady specific: check for <pro> element
            _MSPR_NS = "urn:microsoft:playready"
            pro_el = cp.find(f"{{{_MSPR_NS}}}pro")
            if pro_el is not None and pro_el.text and pro_el.text.strip():
                if not info.get_pssh_for("PR"):
                    info.set_pssh(pro_el.text.strip(), drm_type_hint="PR")
                    logger.info("DashParser: PlayReady PSSH extracted from <pro> element")
            
            # Extract KID from multiple possible attribute names
            for attr in ("{urn:mpeg:cenc:2013}default_KID", "cenc:default_KID", "default_KID"):
                kid = cp.get(attr)
                if kid:
                    info.set_kid(kid)
                    info.default_kid = info.kid
                    break

        if not info.drm_type and (info.kid or info.default_kid):
            for cp in element.findall(".//mpd:ContentProtection", _NS):
                scheme = (cp.get("schemeIdUri") or "").lower()
                dtype = _drm_hint_from_scheme(scheme)
                logger.info(f"DashParser: inferring DRM type from scheme | scheme={scheme} | inferred_type={dtype}")

                if dtype:
                    if dtype not in info._drm_types:
                        info._drm_types.append(dtype)
                    if not info.drm_type:
                        info.drm_type = dtype

        if not info.kid and self.provided_kid:
            info.set_kid(self.provided_kid)
            info.default_kid = info.kid
        return info

    def _resolve_base_url(self) -> None:
        if self._root is None:
            return
        base_el = self._root.find("mpd:BaseURL", _NS)
        if base_el is not None and base_el.text and base_el.text.strip():
            candidate = base_el.text.strip()
            if candidate.startswith(("http://", "https://", "//")):
                self._base_url = candidate if candidate.endswith("/") else candidate + "/"
            else:
                self._base_url = urljoin(self._base_url, candidate)
                if not self._base_url.endswith("/"):
                    self._base_url += "/"
        logger.info(f"DashParser: effective base URL = {self._base_url}")

    @staticmethod
    def _resolve_element_base_url(element, parent_base: str) -> str:
        base_el = element.find("mpd:BaseURL", _NS)
        if base_el is not None and base_el.text and base_el.text.strip():
            resolved = urljoin(parent_base, base_el.text.strip())
            if resolved.endswith("/") or not _is_file_url(resolved):
                return resolved if resolved.endswith("/") else resolved + "/"
            return resolved
        return parent_base

    
    def _apply_segment_template(self, tmpl, rep_id, stream, period_start, base_url):
        if "/ad/" in base_url.lower():
            logger.info(f"DashParser: segment skipped (ad path) | url={base_url}")
            return
        init_tpl = tmpl.get("initialization", "").replace("$RepresentationID$", rep_id)
        media_tpl = tmpl.get("media", "").replace("$RepresentationID$", rep_id)
        start_num = int(tmpl.get("startNumber", 1))
        timescale = int(tmpl.get("timescale", 1))

        if init_tpl:
            stream.add_segment(Segment(urljoin(base_url, init_tpl), 0, "init"))

        timeline = tmpl.find(".//mpd:SegmentTimeline", _NS)
        if timeline is not None:
            seg_num = start_num
            current_time = int(period_start * timescale) if period_start else 0
            use_time = "$Time$" in media_tpl
            for s_el in timeline.findall("mpd:S", _NS):
                t = s_el.get("t")
                if t is not None:
                    current_time = int(t)
                d = int(s_el.get("d", 0))
                r = int(s_el.get("r", 0))
                for _ in range(r + 1):
                    seg_url = (
                        media_tpl.replace("$Time$", str(current_time))
                        if use_time
                        else media_tpl.replace("$Number$", str(seg_num))
                    )
                    stream.add_segment(Segment(urljoin(base_url, seg_url), seg_num, "media"))
                    current_time += d
                    seg_num += 1
        elif "$Number$" in media_tpl:
            seg_duration = int(tmpl.get("duration", 0))
            if seg_duration <= 0 or stream.duration <= 0:
                logger.error("DashParser: SegmentTemplate $Number$ without timeline: missing duration — cannot generate segments")
                return
            total_segments = math.ceil(stream.duration * timescale / seg_duration)
            for i in range(start_num, start_num + total_segments):
                stream.add_segment(Segment(urljoin(base_url, media_tpl.replace("$Number$", str(i))), i, "media"))
        elif "$Time$" in media_tpl:
            logger.error("DashParser: SegmentTemplate $Time$ without SegmentTimeline — skipping")
    
    def _apply_segment_list(self, seg_list, stream, base_url):
        init_el = seg_list.find("mpd:Initialization", _NS)
        if init_el is not None:
            # Try sourceURL first (URL-based init), then range (byte-range init)
            src = init_el.get("sourceURL", "")
            if src:
                stream.add_segment(Segment(urljoin(base_url, src), 0, "init"))
            else:
                # Byte-range format: range attribute points to bytes in the base file
                init_range = init_el.get("range", "")
                if init_range:
                    stream.add_segment(Segment(base_url.rstrip("/"), 0, "init", byte_range=init_range))
        
        for idx, seg_el in enumerate(seg_list.findall("mpd:SegmentURL", _NS), start=1):
            # Try media first (URL-based segment), then mediaRange (byte-range segment)
            media_url = seg_el.get("media", "")
            if media_url:
                stream.add_segment(Segment(urljoin(base_url, media_url), idx, "media"))
            else:
                # Byte-range format: mediaRange points to bytes in the base file
                media_range = seg_el.get("mediaRange", "")
                if media_range:
                    stream.add_segment(Segment(base_url.rstrip("/"), idx, "media", byte_range=media_range))

    @staticmethod
    def _parse_iso_duration(s: str) -> float:
        if not s:
            return 0.0
        try:
            m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?", s)
            if m:
                return (
                    int(m.group(1) or 0) * 3600
                    + int(m.group(2) or 0) * 60
                    + float(m.group(3) or 0)
                )
        except Exception:
            pass
        return 0.0