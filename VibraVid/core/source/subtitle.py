# 12.01.25

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from VibraVid.utils.http_client import create_async_client, get_proxy_url
from VibraVid.utils import config_manager
from VibraVid.core.utils.language import resolve_locale
from VibraVid.core.source.bridge import run_download_plan


logger = logging.getLogger("SubtitleDownloader")
VALID_SUBTITLE_FORMATS = {"vtt", "srt", "ass", "ssa", "ttml2", "ttml", "xml", "dfxp"}
VALID_AUDIO_FORMATS = {"m4a", "aac", "mp3", "webm", "mp4"}


def is_valid_format(fmt: str, track_type: str) -> bool:
    """Check if the detected format is valid for the given track type."""
    fmt_lower = fmt.lower()
    if track_type == "subtitle":
        return fmt_lower in VALID_SUBTITLE_FORMATS
    if track_type == "audio":
        return fmt_lower in VALID_AUDIO_FORMATS
    return False


def _extract_lang_and_flags(lang_raw: str, track_info: Dict = None) -> Tuple[str, set]:
    """Extract standard flags from a language string and return the clean base language and a set of flags."""
    parts = re.split(r"[-_]", lang_raw)
    flags = set()
    clean = []

    if track_info:
        if track_info.get("forced"):
            flags.add("forced")
        if track_info.get("sdh"):
            flags.add("sdh")
        if track_info.get("cc"):
            flags.add("cc")

    for p in parts:
        if p.lower() in ("forced", "cc", "sdh", "hi", "default"):
            flags.add(p.lower())
        else:
            clean.append(p)
    return "-".join(clean), flags


def build_ext_track_label(track: Dict, track_type: str, ext_override: str = None, plain: bool = False) -> str:
    """
    Build a rich-formatted progress-bar label for an external subtitle or audio track.
    Shows language (BCP-47) + flags only — no name, no format suffix.
    """
    lang_raw = (track.get("language") or "und").strip()
    base_lang, parsed_flags = _extract_lang_and_flags(lang_raw, track)

    forced = bool(track.get("forced")) or "forced" in parsed_flags
    sdh = bool(track.get("sdh")) or "sdh" in parsed_flags
    cc = bool(track.get("cc")) or "cc" in parsed_flags

    # Suppress DEFAULT when the track is only DEFAULT because it's forced
    default = (bool(track.get("default")) or "default" in parsed_flags) and not forced
    resolved = resolve_locale(base_lang) or base_lang
    parts: List[str] = [f"[bold white]{resolved}[/bold white]"]

    flags: List[str] = []
    if forced:
        flags.append("[FORCED]")
    if sdh:
        flags.append("[SDH]")
    if cc:
        flags.append("[CC]")
    if default:
        flags.append("[DEFAULT]")
    
    # Use track's extension as fallback if provided
    track_ext = track.get("extension", "UNK").lower().lstrip(".")
    ext = ext_override or ext_from_url(track.get("url", ""), track_ext)
    logger.debug(f"Building label for track: lang_raw={lang_raw}, resolved={resolved}, flags={flags}, ext={ext}")

    if plain:
        plain_parts: List[str] = [resolved]
        if flags:
            plain_parts.append(" ".join(flags))
        
        ext_tag = f"[{ext}]" if ext else ""
        pfx = "Sub" if track_type == "subtitle" else "Aud"
        return f"{pfx} {ext_tag} {' '.join(plain_parts)}".strip()

    if flags:
        parts.append(f"[bold red]{' '.join(flags)}[/bold red]")

    ext_tag = f"[yellow]\\[{ext}][/yellow]" if ext else ""
    pfx = "[bold cyan]Sub[/bold cyan]" if track_type == "subtitle" else "[bold cyan]Aud[/bold cyan]"
    return f"{pfx} {ext_tag} {' '.join(parts)}"


def normalize_sub_filename(lang_raw: str, track_info: Dict = None) -> Tuple[str, str]:
    """
    Return (base_lang, flag_suffix) for subtitle filename construction.

    Filename format: ``{filename}.{base_lang}{flag_suffix}.{ext}``
    where flag_suffix uses underscores: ``_forced``, ``_cc``, ``_sdh``, or ``""``.
    """
    base_lang, parsed_flags = _extract_lang_and_flags(lang_raw, track_info)

    flags: List[str] = []
    if (track_info and track_info.get("forced")) or "forced" in parsed_flags:
        flags.append("forced")
    if (track_info and track_info.get("sdh")) or "sdh" in parsed_flags:
        flags.append("sdh")
    if (track_info and track_info.get("cc")) or "cc" in parsed_flags or "hi" in parsed_flags:
        flags.append("cc")

    flag_str = ("_" + "_".join(flags)) if flags else ""
    return base_lang, flag_str


def ext_from_url(url: str, fallback: str = "UNK") -> str:
    """Detect subtitle/audio format from URL path, ignoring query string."""
    path = url.split("?")[0].lower()
    for ext in ("vtt", "srt", "ass", "ssa", "ttml2", "ttml", "xml", "dfxp", "m4a", "aac", "mp3"):
        if path.endswith(f".{ext}"):
            return ext
    
    return fallback


async def resolve_url(client: Any, url: str, track_type: str) -> Tuple[str, str]:
    """If *url* points to an HLS manifest (#EXTM3U), resolve and return the first media segment URL and its detected format."""
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text.strip()
    except Exception as exc:
        logger.error(f"resolve_url probe failed for {url!r}: {exc}")
        return url, ext_from_url(url, "UNK")

    if text.startswith("#EXTM3U"):
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                fmt = ext_from_url(line, "UNK")
                logger.info(f"Resolved manifest → segment: {line} (fmt={fmt})")
                return line, fmt

        logger.error(f"Manifest parsed but no segment found in {url!r}")
        return url, ext_from_url(url, "UNK")

    fmt = ext_from_url(url, "UNK")
    return url, fmt


async def download_external_tracks_with_progress(headers: Dict, external_subtitles: List[Dict], external_audios: List[Dict], output_dir: Path, filename: str, bar_manager: Any, stop_check: Any = None) -> Tuple[List[Dict], List[Dict]]:
    """Download external tracks with manifest resolution, proper filenames, and progress.
    
    Args:
        stop_check: Optional callable that returns True when download should stop.
    """
    ext_subs: List[Dict] = []
    ext_auds: List[Dict] = []
    all_tasks = (
        [(sub, "subtitle") for sub in external_subtitles if sub.get("_selected", True)]
        + [(aud, "audio") for aud in external_audios if aud.get("_selected", True)]
    )
    for subs in external_subtitles:
        logger.info(f"Add external subtitle track: {subs}")
    for auds in external_audios:
        logger.info(f"Add external audio track: {auds}")

    if not all_tasks:
        return ext_subs, ext_auds

    async with create_async_client(headers=headers) as client:
        for track, track_type in all_tasks:
            lang_raw = (track.get("language") or "unknown").strip()

            # Use track's extension as fallback if provided
            track_ext = track.get("extension", "UNK").lower().lstrip(".")
            fmt: str = ext_from_url(track.get("url", ""), track_ext)
            base_lang: str
            flag_suffix: str
            base_lang, flag_suffix = normalize_sub_filename(lang_raw, track)
            logger.debug(f"Prepared to download track: lang_raw={lang_raw}, base_lang={base_lang}, flag_suffix={flag_suffix}, ext={fmt}, url={track.get('url')}")

            try:
                raw_url = track["url"]
                final_url, fmt = await resolve_url(client, raw_url, track_type)

                # If format is still UNK and track provides extension, use it
                if fmt == "UNK" and track.get("extension"):
                    fmt = track.get("extension").lower().lstrip(".")
                    logger.debug(f"Using track extension for {track_type}: {fmt}")

                if not is_valid_format(fmt, track_type):
                    logger.error(f"Skipping {track_type} with invalid format '{fmt}' for {lang_raw}: {raw_url}")
                    continue

                # ── Build normalised filename ─────────────────────────────
                base_lang, flag_suffix = normalize_sub_filename(lang_raw, track)
                out_path = output_dir / f"{base_lang}{flag_suffix}.{fmt}"
                task_key = track.get("_task_key", f"ext_{track_type}_{base_lang}{flag_suffix}")
                new_label = build_ext_track_label(track, track_type, ext_override=fmt)
                display_label = build_ext_track_label(track, track_type, ext_override=fmt, plain=True)

                logger.info(f"Downloading external {track_type}: {lang_raw} → {out_path.name}")
                plan = {
                    "project": "Velora",
                    "task_key": task_key,
                    "label": new_label,
                    "display_label": display_label,
                    "concurrency": 1,
                    "retry_count": config_manager.config.get_int("REQUESTS", "max_retry"),
                    "timeout_seconds": config_manager.config.get_int("REQUESTS", "timeout"),
                    "proxy_url": get_proxy_url(),
                    "headers": headers,
                    "tasks": [
                        {
                            "task_key": task_key,
                            "label": new_label,
                            "display_label": display_label,
                            "url": final_url,
                            "path": str(out_path),
                            "headers": {},
                        }
                    ],
                }

                results = run_download_plan(plan, event_cb=bar_manager.handle_progress_line, stop_check=stop_check)
                result = results[0] if results else {}
                size = int(result.get("bytes") or 0)
                if result.get("path") and Path(result["path"]).exists() and size > 0:
                    entry = {
                        "path": str(out_path),
                        "language": f"{base_lang}{flag_suffix}",
                        "type": fmt,
                        "size": size,
                    }
                    if track_type == "subtitle":
                        ext_subs.append(entry)
                    else:
                        ext_auds.append(entry)

                    logger.info(f"Downloaded {track_type} {lang_raw}: {size} bytes → {out_path.name}")
                else:
                    logger.error(f"Failed to download {track_type} {lang_raw} (empty file)")
                    bar_manager.handle_progress_line(
                        {
                            "task_key": task_key,
                            "label": new_label,
                            "display_label": display_label,
                            "segments": "0/1",
                            "speed": "FAILED",
                        }
                    )

            except Exception as exc:
                logger.error(f"External {track_type} download failed ({track.get('language', '?')}): {exc}")
                bar_manager.handle_progress_line(
                    {
                        "task_key": track.get("_task_key", f"ext_{track_type}_{base_lang}{flag_suffix}"),
                        "label": build_ext_track_label(track, track_type, ext_override=fmt),
                        "display_label": build_ext_track_label(track, track_type, ext_override=fmt, plain=True),
                        "segments": "0/1",
                        "speed": "ERR",
                    }
                )

    return ext_subs, ext_auds