# 13.03.26

from __future__ import annotations

import re
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

from VibraVid.core.manifest.stream import DRMInfo, Segment, Stream
from VibraVid.utils.http_client import create_client, get_headers
from VibraVid.utils import config_manager


logger = logging.getLogger(__name__)
_NS = {
    "mpd": "urn:mpeg:dash:schema:mpd:2011",
    "cenc": "urn:mpeg:cenc:2013",
}
_SCHEME_DRM_MAP = {
    DRMInfo.WIDEVINE_SYSTEM_ID: "WV", "edef8ba9": "WV", "widevine": "WV",
    DRMInfo.PLAYREADY_SYSTEM_ID: "PR", "9a04f079": "PR", "playready": "PR", "com.microsoft": "PR",
    DRMInfo.FAIRPLAY_SYSTEM_ID: "FP", "94ce86fb": "FP", "fairplay": "FP", "com.apple": "FP"
}


def _drm_hint_from_scheme(scheme_lower: str) -> Optional[str]:
    """Return 'WV', 'PR', 'FP', or None from a lower-cased schemeIdUri string."""
    for fragment, dtype in _SCHEME_DRM_MAP.items():
        if fragment in scheme_lower:
            return dtype
    return None


class DashParser:
    """
    Fetch and parse an MPEG-DASH MPD manifest.

    Usage::

        parser = DashParser(mpd_url, headers)
        parser.fetch_manifest()           # or pass content= to skip fetch
        streams = parser.parse_streams()  # List[Stream]
        raw_xml = parser.raw_content      # save to temp dir if needed
    """
    def __init__(self, mpd_url: str, headers: Dict[str, str] = None, provided_kid: str = None, content: str = None,):
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
        """Populate raw_content and internal XML root."""
        if self._injected:
            self.raw_content = self._injected
            try:
                self._root = ET.fromstring(self.raw_content)
                return True
            except ET.ParseError as exc:
                logger.error(f"DashParser: injected XML parse error: {exc}")
                self._injected = None  # fall through to network

        try:
            timeout = config_manager.config.get_int("REQUESTS", "timeout", default=30)

            hdrs = dict(self.headers)
            hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))

            with create_client(headers=hdrs, timeout=timeout, follow_redirects=True) as c:
                r = c.get(self.mpd_url)
                r.raise_for_status()
                self.raw_content = r.text
            self._root = ET.fromstring(self.raw_content)
            return True
        except Exception as exc:
            logger.error(f"DashParser: fetch/parse failed: {exc}")
            return False

    def save_raw(self, directory: Path) -> Path:
        """Write raw_content to *directory*/raw.mpd and return the path."""
        path = Path(directory) / "raw.mpd"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.raw_content or "", encoding="utf-8")
        return path

    def parse_streams(self) -> List[Stream]:
        """
        Return a list of Stream objects extracted from the MPD.

        Each Representation becomes one Stream.  DRM info is attached per
        stream (inherited from its AdaptationSet if the Representation itself
        carries no ContentProtection).
        """
        if self._root is None:
            return []

        streams: List[Stream] = []

        # Global presentation duration
        dur_str = self._root.get("mediaPresentationDuration", "")
        global_duration = self._parse_iso_duration(dur_str)

        for adapt in self._root.findall(".//mpd:AdaptationSet", _NS):
            mime = (adapt.get("contentType") or adapt.get("mimeType") or "").lower()

            if "video" in mime:
                stype = "video"
            elif "audio" in mime:
                stype = "audio"
            elif "text" in mime or "subtitle" in mime:
                stype = "subtitle"
            elif "image" in mime:
                continue  # skip thumbnails
            else:
                # Last-resort: check codecs attribute on children
                first_rep = adapt.find(".//mpd:Representation", _NS)
                codecs_hint = ((first_rep.get("codecs") or adapt.get("codecs") or "").lower() if first_rep is not None else "")
                if any(c in codecs_hint for c in ("wvtt", "stpp", "ttml", "vtt", "srt")):
                    stype = "subtitle"
                elif any(c in codecs_hint for c in ("mp4a", "ec-3", "ac-3", "opus", "flac")):
                    stype = "audio"
                elif any(c in codecs_hint for c in ("avc", "hev", "hvc", "av01", "vp09")):
                    stype = "video"
                else:
                    continue

            adapt_drm = self._extract_drm(adapt)

            seen_rep_keys: set = set()
            for rep in adapt.findall(".//mpd:Representation", _NS):
                s = self._parse_representation(rep, adapt, stype, adapt_drm, global_duration)
                if s is None:
                    continue

                # Dedup key: (language, codecs, bitrate) — same track repeated
                dedup_key = (s.language, (s.codecs or "").lower(), s.bitrate)
                if dedup_key in seen_rep_keys:
                    logger.info(f"DashParser: skipping duplicate {stype} stream lang={s.language} codecs={s.codecs} bw={s.bitrate}")
                    continue

                seen_rep_keys.add(dedup_key)
                streams.append(s)

        return streams

    def _parse_representation(self, rep: ET.Element, adapt: ET.Element, stype: str, adapt_drm: DRMInfo, global_dur: float) -> Optional[Stream]:
        rep_id = rep.get("id", "")
        bandwidth = int(rep.get("bandwidth", 0))

        s = Stream(type=stype, id=rep_id, format="dash")
        s.bitrate = bandwidth
        s.duration = global_dur

        # ── Role ──────────────────────────────────────────────────────────
        role_el = adapt.find(".//mpd:Role", _NS)
        if role_el is not None:
            s.role = role_el.get("value", "main")

        # ── Type-specific fields ──────────────────────────────────────────
        if stype == "video":
            s.width = int(rep.get("width", 0))
            s.height = int(rep.get("height", 0))
            s.resolution = f"{s.width}x{s.height}" if s.width and s.height else ""
            s.fps = rep.get("frameRate", adapt.get("frameRate", ""))
            s.codecs = rep.get("codecs") or adapt.get("codecs", "")

        elif stype == "audio":
            s.language = adapt.get("lang", "und")
            s.codecs = rep.get("codecs") or adapt.get("codecs", "")
            s.channels = rep.get("audioChannelConfiguration", adapt.get("audioChannelConfiguration", ""))

            # AudioChannelConfiguration schemeIdUri value
            for acc_el in adapt.findall(".//mpd:AudioChannelConfiguration", _NS):
                val = acc_el.get("value", "")
                if val:
                    s.channels = val
                    break

        elif stype == "subtitle":
            s.language = adapt.get("lang", "und")
            s.codecs = rep.get("codecs") or adapt.get("codecs", "")

        # ── DRM (rep overrides adapt) — FIX #4 + #5: merge with type hints ──
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

        # ── Segment template ──────────────────────────────────────────────
        tmpl = rep.find(".//mpd:SegmentTemplate", _NS) or adapt.find(".//mpd:SegmentTemplate", _NS)
        if tmpl is not None:
            self._apply_segment_template(tmpl, rep_id, s)

        return s

    def _extract_drm(self, element: ET.Element) -> DRMInfo:
        """
        Walk every <ContentProtection> child of *element* and populate a
        DRMInfo object with all PSSH blobs and the default KID.
        """
        info = DRMInfo()

        for cp in element.findall(".//mpd:ContentProtection", _NS):
            scheme = (cp.get("schemeIdUri") or "").lower()

            # set_method first — so info.drm_type is populated before set_pssh
            info.set_method(scheme)

            # Resolve hint from schemeIdUri (most reliable signal for raw blobs)
            drm_hint = _drm_hint_from_scheme(scheme)

            # PSSH blob — pass hint so raw protobuf gets labelled correctly
            pssh_el = cp.find(".//cenc:pssh", _NS)
            if pssh_el is not None and pssh_el.text:
                info.set_pssh(pssh_el.text.strip(), drm_type_hint=drm_hint)

            # Also check mspr:pro (PlayReady Header Object inside <mspr:pro>)
            # Some manifests use this instead of / in addition to <cenc:pssh>.
            _MSPR_NS = "urn:microsoft:playready"
            pro_el = cp.find(f"{{{_MSPR_NS}}}pro")
            if pro_el is not None and pro_el.text and pro_el.text.strip():
                # mspr:pro is a PlayReady Header Object, NOT a full PSSH box.
                # Store it under PR so the DRM manager can use it if needed.
                existing_pr = info.get_pssh_for("PR")
                if not existing_pr:
                    info.set_pssh(pro_el.text.strip(), drm_type_hint="PR")

            # default_KID  (namespace-prefixed or plain attribute)
            for attr in ("{urn:mpeg:cenc:2013}default_KID", "cenc:default_KID", "default_KID"):
                kid = cp.get(attr)
                if kid:
                    info.set_kid(kid)
                    info.default_kid = info.kid
                    break

        # Fallback: if still no drm_type but KID exists, re-scan scheme URIs
        if not info.drm_type and (info.kid or info.default_kid):
            for cp in element.findall(".//mpd:ContentProtection", _NS):
                scheme = (cp.get("schemeIdUri") or "").lower()
                dtype = _drm_hint_from_scheme(scheme)
                if dtype:
                    if dtype not in info._drm_types:
                        info._drm_types.append(dtype)
                    if not info.drm_type:
                        info.drm_type = dtype

        # Inject provided_kid if nothing found
        if not info.kid and self.provided_kid:
            info.set_kid(self.provided_kid)
            info.default_kid = info.kid

        return info

    def _apply_segment_template(self, tmpl: ET.Element, rep_id: str, stream: Stream) -> None:
        init_tpl = tmpl.get("initialization", "").replace("$RepresentationID$", rep_id)
        media_tpl = tmpl.get("media", "").replace("$RepresentationID$", rep_id)
        start_num = int(tmpl.get("startNumber", 1))

        if init_tpl:
            stream.add_segment(Segment(urljoin(self._base_url, init_tpl), 0, "init"))

        timeline = tmpl.find(".//mpd:SegmentTimeline", _NS)
        if timeline is not None:
            seg_num = start_num
            current_time = 0
            use_time = "$Time$" in media_tpl

            for s_el in timeline.findall(".//mpd:S", _NS):
                t = s_el.get("t")
                if t is not None:
                    current_time = int(t)

                d = int(s_el.get("d", 0))
                rep = int(s_el.get("r", 0))

                for _ in range(rep + 1):
                    seg_url = (
                        media_tpl.replace("$Time$", str(current_time))
                        if use_time
                        else media_tpl.replace("$Number$", str(seg_num))
                    )
                    stream.add_segment(
                        Segment(urljoin(self._base_url, seg_url), seg_num, "media")
                    )
                    current_time += d
                    seg_num += 1

    @staticmethod
    def _parse_iso_duration(s: str) -> float:
        """Parse ISO 8601 duration PT1H2M3.4S → total seconds."""
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