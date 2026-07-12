# 01.04.25

import logging
import struct
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client
from VibraVid.core.ui.bar_manager import DownloadBarManager

from .util._hls import hls_base_url, parse_hls_variant_playlist
from .util._dash import build_dash_ranged_segments
from .util._stream_helpers import safe_name


logger = logging.getLogger("manual")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")


class VodStreamMixin:
    def _apply_max_time(self, dl_segs: List[Dict]) -> List[Dict]:
        if not self.max_time or self.max_time <= 0:
            return dl_segs
        
        acc = 0.0
        result = []
        for seg in dl_segs:
            result.append(seg)
            if seg.get("seg_type") == "init":
                continue

            acc += seg.get("duration", 0.0)
            if acc >= self.max_time:
                break

        if len(result) < len(dl_segs):
            logger.info(f"Limiting download to {acc:.1f}s of content (max_time={self.max_time:.0f}s)")
        return result

    def _assign_segment_durations(self, stream, dl_segs: List[Dict], headers: Dict) -> None:
        """Populate each media segment's ``"duration"`` (seconds) for the ``--max-time``"""
        if stream.is_live or not self.max_time:
            return

        media = [s for s in dl_segs if s.get("seg_type") == "media"]
        if not media:
            return

        durations = self._segment_durations_from_sidx(dl_segs, headers)
        if durations:
            for seg, dur in zip(media, durations):
                seg["duration"] = dur
            logger.info(f"max_time: per-segment durations from sidx ({len(durations)} segs, total {sum(durations):.0f}s)")
        elif stream.duration > 0:
            avg = stream.duration / len(media)
            logger.info(f"max_time: no sidx, using manifest average {avg:.3f}s/seg")
            for seg in media:
                seg["duration"] = avg

    def _segment_durations_from_sidx(self, dl_segs: List[Dict], headers: Dict) -> Optional[List[float]]:
        """Exact per-segment durations from the file's ``sidx`` (segment index) box."""
        media = [s for s in dl_segs if s.get("seg_type") == "media"]
        init_seg = next((s for s in dl_segs if s.get("seg_type") == "init"), None)
        if init_seg is None or not media:
            return None
        try:
            with create_client(headers=headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                r = c.get(init_seg["url"], headers=init_seg.get("headers"))
                r.raise_for_status()
                data = r.content

            idx = data.find(b"sidx")
            if idx < 0:
                return None
            p = idx + 4
            version = data[p]                        # version (1) + flags (3)
            p += 4
            p += 4                                   # reference_ID
            timescale = struct.unpack(">I", data[p:p + 4])[0]
            p += 4
            if timescale <= 0:
                return None
            p += 8 if version == 0 else 16           # earliest_presentation_time + first_offset
            p += 2                                   # reserved
            ref_count = struct.unpack(">H", data[p:p + 2])[0]
            p += 2

            durs: List[float] = []
            for _ in range(ref_count):
                p += 4                               # reference type (1 bit) + size (31 bits)
                subdur = struct.unpack(">I", data[p:p + 4])[0]
                p += 4
                p += 4                               # SAP
                durs.append(subdur / timescale)

            if len(durs) < len(media):
                return None
            return durs[:len(media)]
        except Exception as e:
            logger.debug(f"sidx parse failed: {e}")
            return None

    def _stream_task_key(self, stream) -> str:
        if stream.type == "video":
            return self._video_task_key

        if stream.type == "subtitle":
            lang = (stream.resolved_language or stream.language or "und").lower()
            return f"sub_{lang.split('-')[0]}{self._sub_discriminator(stream)}"

        lang = (stream.resolved_language or stream.language or "und").lower()
        return f"aud_{lang.split('-')[0]}"

    def _make_stream_dir(self, stream, protocol: str) -> Path:
        if stream.type == "video":
            name = f"v_{safe_name(stream.resolution or 'unknown')}"
        elif stream.type == "subtitle":
            lang = safe_name((stream.language or "und").lower())
            name = f"s_{lang}{self._sub_discriminator(stream)}"
        else:
            name = f"a_{safe_name((stream.language or 'und').lower())}"

        d = self._tmp_dir / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ------------------------------------------------------------------
    # Dispatch per stream type
    # ------------------------------------------------------------------
    def _download_stream(self, stream, bar_manager: DownloadBarManager) -> None:
        effective_live = self._session_live_decrypt

        if self.manifest_type == "HLS":
            if stream.is_live:
                playlist_url = stream.playlist_url
                all_headers = self._build_headers()
                first_content: Optional[str] = None
                base_url: Optional[str] = None

                try:
                    with create_client(headers=all_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                        resp = c.get(playlist_url)
                        resp.raise_for_status()
                        first_content = resp.text

                    base_url = hls_base_url(playlist_url)
                except Exception as exc:
                    logger.error(f"Failed to fetch HLS playlist for live detection: {exc}")
                    return
                self._download_hls_live_stream(stream, bar_manager, live_decryption=effective_live, first_content=first_content, base_url=base_url)
            else:
                self._download_hls_stream(stream, bar_manager, effective_live)

        if self.manifest_type == "DASH":
            if stream.is_live:
                self._download_dash_live_stream(stream, bar_manager, live_decryption=effective_live, mpd_url=self.url, headers=self._build_headers())
            else:
                self._download_dash_stream(stream, bar_manager, effective_live)

        if self.manifest_type == "ISM":
            self._download_ism_stream(stream, bar_manager)

    def _download_hls_stream(self, stream, bar_manager: DownloadBarManager, live_decryption: bool = False) -> None:
        playlist_url = stream.playlist_url
        if not playlist_url:
            logger.error(f"HLS stream has no playlist_url: {stream}")
            return

        all_headers = self._build_headers()
        try:
            with create_client(headers=all_headers, timeout=REQUEST_TIMEOUT, follow_redirects=True) as c:
                resp = c.get(playlist_url)
                resp.raise_for_status()
                playlist_content = resp.text
        except Exception as exc:
            logger.error(f"Failed to fetch HLS variant playlist: {exc}")
            return

        base_url = hls_base_url(playlist_url)
        media_segs, init_url = parse_hls_variant_playlist(playlist_content, base_url)

        if not media_segs and not init_url:
            logger.error(f"HLS variant playlist has no segments: {playlist_url}")
            return

        dl_segs: List[Dict] = []
        if init_url:
            dl_segs.append({"url": init_url, "number": 0, "seg_type": "init", "enc": {"method": "NONE"}})

        offset = len(dl_segs)
        for seg in media_segs:
            dl_segs.append({
                "url":      seg["url"],
                "number":   seg["number"] + offset,
                "seg_type": "media",
                "enc":      seg["enc"],
                "duration": seg.get("duration", 0.0),
            })

        if self.max_segments and self.max_segments > 0:
            dl_segs = dl_segs[:1 + self.max_segments] if init_url else dl_segs[:self.max_segments]
            logger.debug(f"Limiting HLS download to {len(dl_segs)} segments (max_segments={self.max_segments})")

        dl_segs = self._apply_max_time(dl_segs)

        def _refresh_hls_seg_urls(failed_numbers: List[int]) -> Dict[int, str]:
            logger.info(f"HLS token refresh: {len(failed_numbers)} failed segment(s), attempting manifest refresh to get new token")
            if not self.manifest_refresh_fn:
                return {}

            fresh_master = self.manifest_refresh_fn()
            if not fresh_master:
                logger.error("HLS token refresh: manifest_refresh_fn returned no URL")
                return {}

            fresh_query = urlsplit(fresh_master).query
            failed_set = set(failed_numbers)
            return {
                s["number"]: urlunsplit(urlsplit(s["url"])._replace(query=fresh_query))
                for s in dl_segs if s["number"] in failed_set
            }

        self._download_stream_generic(dl_segs, stream, "hls", "ts", bar_manager, live_decryption=live_decryption, seg_url_refresh_fn=_refresh_hls_seg_urls)

    def _download_dash_stream(self, stream, bar_manager: DownloadBarManager, live_decryption: bool = False) -> None:
        if not stream.segments:
            logger.error(f"DASH stream has no segments: {stream}")
            return

        # Multi-period tracks (same representation id spread across Periods that use
        # distinct source files and/or mix clear + encrypted content) can't be
        # concatenated into one file: each Period has its own init/moov and DRM.
        # Hand them to the Period-aware path (merge + decrypt + concat per Period).
        media_periods = {s.period_idx for s in stream.segments if s.seg_type == "media"}
        if len(media_periods) > 1:
            logger.info(f"DASH multi-period stream detected ({len(media_periods)} periods) — using per-period pipeline | {stream.type} {stream.resolution or stream.language}")
            self._download_dash_multiperiod(stream, bar_manager, live_decryption)
            return

        all_headers = self._build_headers()
        chunk_size = max(8 * 1024 * 1024, 1 * 1024 * 1024)
        media_segments = [s for s in stream.segments if s.seg_type == "media"]
        unique_media_urls = {s.url for s in media_segments}
        is_single_file = len(unique_media_urls) == 1 and not any(s.byte_range for s in media_segments)

        dl_segs: List[Dict] = []
        next_num = 0
        single_file_emitted = False
        for seg in stream.segments:
            if seg.byte_range:
                dl_segs.append({
                    "url":      seg.url,
                    "number":   next_num,
                    "seg_type": seg.seg_type,
                    "enc":      {"method": "NONE"},
                    "headers":  {"Range": f"bytes={seg.byte_range}"},
                })
                next_num += 1

            elif is_single_file and seg.seg_type == "media":

                # Emit the byte-range split once; skip the duplicate period refs.
                if single_file_emitted:
                    continue

                single_file_emitted = True
                ranged = build_dash_ranged_segments(seg.url, all_headers, chunk_size, REQUEST_TIMEOUT)

                if ranged:
                    for part in ranged:
                        part["number"]   = next_num
                        part["seg_type"] = seg.seg_type
                        dl_segs.append(part)
                        next_num += 1

                    continue
                else:
                    dl_segs.append({"url": seg.url, "number": next_num, "seg_type": seg.seg_type, "enc": {"method": "NONE"}})
                    next_num += 1
            else:
                dl_segs.append({"url": seg.url, "number": next_num, "seg_type": seg.seg_type, "enc": {"method": "NONE"}})
                next_num += 1

        self._assign_segment_durations(stream, dl_segs, all_headers)

        if self.max_segments and self.max_segments > 0:
            dl_segs = dl_segs[:self.max_segments]
            logger.debug(f"Limiting DASH download to {len(dl_segs)} segments (max_segments={self.max_segments})")

        dl_segs = self._apply_max_time(dl_segs)

        def _refresh_dash_seg_urls(failed_numbers: List[int]) -> Dict[int, str]:
            logger.info(f"DASH token refresh: {len(failed_numbers)} failed segment(s), attempting manifest refresh to get new token")
            if not self.manifest_refresh_fn:
                return {}

            fresh_master = self.manifest_refresh_fn()
            if not fresh_master:
                logger.error("DASH token refresh: manifest_refresh_fn returned no URL")
                return {}

            fresh_query = urlsplit(fresh_master).query
            failed_set = set(failed_numbers)
            return {
                s["number"]: urlunsplit(urlsplit(s["url"])._replace(query=fresh_query))
                for s in dl_segs if s["number"] in failed_set
            }

        # Single-file byte-range DASH: every media segment is a byte range of ONE file.
        byte_range_single_file = bool(media_segments) and all(s.byte_range for s in media_segments)
        effective_live = live_decryption and not byte_range_single_file
        if byte_range_single_file and live_decryption:
            logger.info("DASH byte-range single-file stream: decrypting after merge (not per-segment)")

        self._download_stream_generic(dl_segs, stream, "dash", "mp4", bar_manager, live_decryption=effective_live, seg_url_refresh_fn=_refresh_dash_seg_urls)

    def _download_ism_stream(self, stream, bar_manager: DownloadBarManager) -> None:
        if not stream.segments:
            logger.error(f"ISM stream has no segments: {stream}")
            return

        all_headers = self._build_headers()
        chunk_size = max(8 * 1024 * 1024, 1 * 1024 * 1024)
        media_segments = [s for s in stream.segments if s.seg_type == "media"]
        unique_media_urls = {s.url for s in media_segments}
        is_single_file = len(unique_media_urls) == 1 and not any(s.byte_range for s in media_segments)

        dl_segs: List[Dict] = []
        next_num = 0
        single_file_emitted = False

        ism_enc_dict = {"method": "NONE"}
        if stream.drm and stream.drm.is_encrypted():
            ism_enc_dict = {"method": "playready-piff"}
            if hasattr(stream.drm, 'kid') and stream.drm.kid != "N/A":
                ism_enc_dict["kid"] = stream.drm.kid
                self._probe_ism_init(stream)

        for seg in stream.segments:
            if seg.byte_range:
                dl_segs.append({
                    "url":      seg.url,
                    "number":   next_num,
                    "seg_type": seg.seg_type,
                    "enc":      ism_enc_dict,
                    "headers":  {"Range": f"bytes={seg.byte_range}"},
                })
                next_num += 1
            elif is_single_file and seg.seg_type == "media":

                # Emit the byte-range split once; skip the duplicate period refs.
                if single_file_emitted:
                    continue

                single_file_emitted = True
                ranged = build_dash_ranged_segments(seg.url, all_headers, chunk_size, REQUEST_TIMEOUT)

                if ranged:
                    for part in ranged:
                        part["number"]   = next_num
                        part["seg_type"] = seg.seg_type
                        part["enc"] = ism_enc_dict
                        dl_segs.append(part)
                        next_num += 1

                    continue
                else:
                    dl_segs.append({"url": seg.url, "number": next_num, "seg_type": seg.seg_type, "enc": ism_enc_dict})
                    next_num += 1
            else:
                dl_segs.append({"url": seg.url, "number": next_num, "seg_type": seg.seg_type, "enc": ism_enc_dict})
                next_num += 1

        self._assign_segment_durations(stream, dl_segs, all_headers)

        if self.max_segments and self.max_segments > 0:
            dl_segs = dl_segs[:self.max_segments]
            logger.debug(f"Limiting ISM download to {len(dl_segs)} segments (max_segments={self.max_segments})")

        dl_segs = self._apply_max_time(dl_segs)

        def _refresh_ism_seg_urls(failed_numbers: List[int]) -> Dict[int, str]:
            logger.info(f"ISM token refresh: {len(failed_numbers)} failed segment(s), attempting manifest refresh to get new token")
            if not self.manifest_refresh_fn:
                return {}

            fresh_master = self.manifest_refresh_fn()
            if not fresh_master:
                logger.error("ISM token refresh: manifest_refresh_fn returned no URL")
                return {}

            fresh_query = urlsplit(fresh_master).query
            failed_set = set(failed_numbers)
            return {
                s["number"]: urlunsplit(urlsplit(s["url"])._replace(query=fresh_query))
                for s in dl_segs if s["number"] in failed_set
            }

        # Force live_decryption=False -> no per-segment decrypt worker
        self._download_stream_generic(dl_segs, stream, "ism", "mp4", bar_manager, live_decryption=False, seg_url_refresh_fn=_refresh_ism_seg_urls)
