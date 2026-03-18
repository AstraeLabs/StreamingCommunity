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
from VibraVid.source.utils.language import resolve_locale
from VibraVid.source.utils.codec import (DV_CODEC_PREFIXES, detect_stream_type)


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
            timeout = config_manager.config.get_int("REQUESTS", "timeout", default=30)
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
        is_multi_period = len(all_periods) > 1
        global_seen_fingerprints: set = set()

        for period_idx, period in enumerate(all_periods):
            period_base_url = self._resolve_element_base_url(period, self._base_url)
            period_start = self._parse_iso_duration(period.get("start", ""))
            period_duration = self._parse_iso_duration(period.get("duration", dur_str))
            period_seen_fingerprints: set = set()

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
                        logger.debug(f"DashParser: AdaptationSet skipped — unknown mime={mime!r} codecs={codecs_hint!r}")
                        continue

                adapt_drm = self._extract_drm(adapt)

                for rep in adapt.findall("mpd:Representation", _NS):
                    rep_id = rep.get("id", "")

                    rep_base_url = self._resolve_element_base_url(rep, adapt_base_url)
                    s = self._parse_representation(rep, adapt, stype, adapt_drm, global_duration or period_duration, period_start, rep_base_url)
                    if s is None:
                        continue

                    fingerprint = (s.type, s.id, s.bitrate, (s.codecs or "").lower(), (s.language or "").lower(), (s.channels or ""), s.sample_rate,)
                    if fingerprint in period_seen_fingerprints:
                        logger.debug(f"DASH stream skipped (intra-period duplicate) | id={rep_id!r} period={period_idx} lang={s.language} bw={s.bitrate} codec={s.codecs}")
                        continue
                    period_seen_fingerprints.add(fingerprint)
                    if not is_multi_period:
                        if fingerprint in global_seen_fingerprints:
                            logger.debug(f"DASH stream skipped (global duplicate) | id={rep_id!r} lang={s.language} bw={s.bitrate} codec={s.codecs}")
                            continue
                        global_seen_fingerprints.add(fingerprint)

                    streams.append(s)
                    logger.info(f"DASH stream added | {s}")

        return streams

    def _parse_representation(self, rep, adapt, stype, adapt_drm, global_dur, period_start, base_url):
        rep_id = rep.get("id", "")
        bandwidth = int(rep.get("bandwidth", 0))
        avg_bw = int(rep.get("averageBandwidth", 0)) or int(adapt.get("averageBandwidth", 0))

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
        if tmpl is not None:
            self._apply_segment_template(tmpl, rep_id, s, period_start, base_url)
        elif seg_list is not None:
            self._apply_segment_list(seg_list, s, base_url)
        else:
            rep_base = rep.find("mpd:BaseURL", _NS)
            if rep_base is not None and rep_base.text:
                s.add_segment(Segment(urljoin(base_url, rep_base.text.strip()), 0, "media"))

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
            pssh_el = cp.find(".//cenc:pssh", _NS)
            if pssh_el is not None and pssh_el.text:
                info.set_pssh(pssh_el.text.strip(), drm_type_hint=drm_hint)
            _MSPR_NS = "urn:microsoft:playready"
            pro_el = cp.find(f"{{{_MSPR_NS}}}pro")
            if pro_el is not None and pro_el.text and pro_el.text.strip():
                if not info.get_pssh_for("PR"):
                    info.set_pssh(pro_el.text.strip(), drm_type_hint="PR")
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
        logger.debug(f"DashParser: effective base URL = {self._base_url}")

    @staticmethod
    def _resolve_element_base_url(element, parent_base: str) -> str:
        base_el = element.find("mpd:BaseURL", _NS)
        if base_el is not None and base_el.text and base_el.text.strip():
            resolved = urljoin(parent_base, base_el.text.strip())
            return resolved if resolved.endswith("/") else resolved + "/"
        return parent_base

    
    def _apply_segment_template(self, tmpl, rep_id, stream, period_start, base_url):
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
                logger.warning("DashParser: SegmentTemplate $Number$ without timeline: missing duration — cannot generate segments")
                return
            total_segments = math.ceil(stream.duration * timescale / seg_duration)
            for i in range(start_num, start_num + total_segments):
                stream.add_segment(
                    Segment(urljoin(base_url, media_tpl.replace("$Number$", str(i))), i, "media")
                )
        elif "$Time$" in media_tpl:
            logger.warning("DashParser: SegmentTemplate $Time$ without SegmentTimeline — skipping")
    
    def _apply_segment_list(self, seg_list, stream, base_url):
        init_el = seg_list.find("mpd:Initialization", _NS)
        if init_el is not None:
            src = init_el.get("sourceURL", "")
            if src:
                stream.add_segment(Segment(urljoin(base_url, src), 0, "init"))
        for idx, seg_el in enumerate(seg_list.findall("mpd:SegmentURL", _NS), start=1):
            media_url = seg_el.get("media", "")
            if media_url:
                stream.add_segment(Segment(urljoin(base_url, media_url), idx, "media"))

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