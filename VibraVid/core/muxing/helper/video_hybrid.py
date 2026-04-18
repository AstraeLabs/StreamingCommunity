# 12.04.26

import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from VibraVid.core.muxing.hybrid import probe_media_file


logger = logging.getLogger(__name__)


def _safe_token(value: str, default: str = "track") -> str:
    cleaned = re.sub(r"[^\w\-]+", "_", str(value or "")).strip("_")
    return cleaned or default


def _split_track_type(track_type: str) -> tuple[str, str]:
    raw = (track_type or "").strip().lower()
    if ":" in raw:
        kind, tag = raw.split(":", 1)
    else:
        kind, tag = raw, ""
    if kind in ("sub", "subtitle", "subtitles"):
        kind = "subtitle"
    elif kind in ("aud", "audio"):
        kind = "audio"
    elif kind in ("vid", "video"):
        kind = "video"
    return kind, tag


def _normalize_keys(keys: Optional[Iterable[str]]) -> List[str]:
    if not keys:
        return []
    return [key.strip() for key in keys if isinstance(key, str) and key.strip()]


def split_other_tracks(other_tracks: Optional[List[Dict[str, Any]]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    video_tracks: List[Dict[str, Any]] = []
    audio_tracks: List[Dict[str, Any]] = []
    subtitle_tracks: List[Dict[str, Any]] = []
    for raw_track in other_tracks or []:
        track = dict(raw_track or {})
        kind, _tag = _split_track_type(track.get("type", ""))
        if kind == "video":
            video_tracks.append(track)
        elif kind == "audio":
            audio_tracks.append(track)
        elif kind == "subtitle":
            subtitle_tracks.append(track)
    return video_tracks, audio_tracks, subtitle_tracks


def _kind_to_filters(kind: str, tag: str) -> Dict[str, str]:
    # backend needs to keep the main media stream enabled to produce an output.
    return {"video": "best", "audio": "false", "subtitle": "false"}


def _track_label(track: Dict[str, Any], kind: str, tag: str) -> str:
    if kind == "video":
        video_tag = _safe_token(tag or track.get("label") or "video", "video")
        return f"Vid {video_tag.upper()}"

    if kind == "audio":
        lang = track.get("language") or tag or "audio"
        return f"Aud {lang}"

    if kind == "subtitle":
        lang = track.get("language") or tag or "sub"
        return f"Sub {lang}"

    return f"Track {kind or 'other'}"


def _pick_status_entry(status: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
    if status.get("video"):
        return status.get("video")
    if kind == "video":
        return status.get("video")
    if kind == "audio":
        audios = status.get("audios") or []
        return audios[0] if audios else None
    if kind == "subtitle":
        subtitles = status.get("subtitles") or status.get("external_subtitles") or []
        return subtitles[0] if subtitles else None
    return None


def download_other_tracks(
    other_tracks: Optional[List[Dict[str, Any]]],
    output_dir: Path,
    filename: str,
    keys: Optional[Iterable[str]] = None,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    max_segments: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Download extra video/audio/subtitle tracks with the manual backend.

    The track list uses the same compact form expected by the hybrid workflow,
    e.g. ``video:dv``, ``audio:en-US`` and ``sub:es-419``.
    """
    results: List[Dict[str, Any]] = []
    if not other_tracks:
        return results

    normalized_keys = _normalize_keys(keys)
    output_dir.mkdir(parents=True, exist_ok=True)
    hybrid_tmp = output_dir / f"{filename}_hybrid_tmp"
    hybrid_tmp.mkdir(parents=True, exist_ok=True)

    from VibraVid.core.source.manual import MediaDownloader as ManualMediaDownloader

    for index, raw_track in enumerate(other_tracks, 1):
        track = dict(raw_track or {})
        kind, tag = _split_track_type(track.get("type", ""))
        if kind not in {"video", "audio"}:
            logger.info("Skipping unsupported other_track type: %s", track.get("type"))
            continue

        url = track.get("url")
        if not url:
            logger.warning("Skipping other_track without url: %s", track)
            continue

        label_token = _safe_token(tag or track.get("language") or str(index))
        track_filename = f"{filename}.{_safe_token(kind)}.{label_token}"
        track_dir = hybrid_tmp / f"{kind}_{index}"
        track_dir.mkdir(parents=True, exist_ok=True)

        label = _track_label(track, kind, tag)
        logger.info("Downloading other track with manual backend: %s (%s)", label, url)

        downloader = ManualMediaDownloader(
            url=url,
            output_dir=str(track_dir),
            filename=track_filename,
            headers=headers or {},
            key=normalized_keys,
            cookies=cookies or {},
            download_id=None,
            site_name=None,
            max_segments=max_segments,
        )
        downloader.custom_filters = _kind_to_filters(kind, tag)

        try:
            downloader.parse_stream(show_table=False)
            result = downloader.start_download(show_progress=True)
        except Exception as exc:
            logger.error("Other track download failed (%s): %s", label, exc, exc_info=True)
            continue

        if result.get("error") == "cancelled":
            logger.info("Other track download cancelled (%s)", label)
            continue

        downloaded = _pick_status_entry(result, kind)
        if not downloaded:
            logger.error("Output not found for other track after download: %s", track_filename)
            continue

        out_path_str = str(downloaded.get("path") or "").strip()
        if not out_path_str:
            logger.error("Output path missing for other track: %s", track_filename)
            continue

        out_path = Path(out_path_str)
        if not out_path.exists():
            logger.error("Output path missing for other track: %s", out_path)
            continue

        size = out_path.stat().st_size
        probe = probe_media_file(str(out_path)) if kind == "video" else {}

        entry: Dict[str, Any] = {
            "path": str(out_path),
            "url": url,
            "type": track.get("type", kind),
            "kind": kind,
            "tag": tag,
            "language": track.get("language") or tag or "und",
            "name": track.get("name") or tag or kind,
            "size": size,
            "probe": probe,
        }
        if probe:
            entry.update(probe)

        results.append(entry)

        logger.info("Downloaded other track %s -> %s", label, out_path.name)
        if probe:
            logger.info(
                "Probe other track %s: hdr=%s dolby_vision=%s video_codec=%s base_info=%s",
                label,
                probe.get("hdr"),
                probe.get("dolby_vision"),
                probe.get("video_codec"),
                probe.get("base_info"),
            )

    return results