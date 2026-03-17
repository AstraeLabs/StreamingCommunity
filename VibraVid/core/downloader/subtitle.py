# 12.01.25

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

from VibraVid.utils.http_client import create_async_client
from VibraVid.source.utils.language import resolve_locale


logger = logging.getLogger("SubtitleDownloader")


def _extract_lang_and_flags(lang_raw: str, track_info: Dict = None) -> Tuple[str, set]:
    """Extract standard flags from a language string and return the clean base language and a set of flags."""
    parts = re.split(r'[-_]', lang_raw)
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
        if p.lower() in ('forced', 'cc', 'sdh', 'hi', 'default'):
            flags.add(p.lower())
        else:
            clean.append(p)
    return '-'.join(clean), flags


def build_ext_track_label(track: Dict, track_type: str, ext_override: str = None) -> str:
    """
    Build a rich-formatted progress-bar label for an external subtitle or audio track.
    Shows language (BCP-47) + flags only — no name, no format suffix.
    """
    lang_raw = (track.get("language") or "und").strip()
    base_lang, parsed_flags = _extract_lang_and_flags(lang_raw, track)

    # Boolean fields take priority; fall back to language-code flag detection
    forced  = bool(track.get("forced")) or "forced" in parsed_flags
    sdh     = bool(track.get("sdh"))    or "sdh" in parsed_flags
    cc      = bool(track.get("cc"))     or "cc" in parsed_flags

    # Suppress DEFAULT when the track is only DEFAULT because it's forced
    default = (bool(track.get("default")) or "default" in parsed_flags) and not forced
    resolved  = resolve_locale(base_lang) or base_lang
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
        
    if flags:
        parts.append(f"[bold red]{' '.join(flags)}[/bold red]")

    ext = ext_override or ext_from_url(track.get("url", ""), "UNK")
    ext_tag = f"[yellow]\\[{ext}][/yellow]" if ext else ""

    pfx = "[bold cyan]Sub[/bold cyan]" if track_type == "subtitle" else "[bold cyan]Aud[/bold cyan]"
    return f"{pfx} {ext_tag} {' '.join(parts)}"


def normalize_sub_filename(lang_raw: str, track_info: Dict = None) -> Tuple[str, str]:
    """
    Return (base_lang, flag_suffix) for subtitle filename construction.

    Filename format: ``{filename}.{base_lang}{flag_suffix}.{ext}``where flag_suffix uses underscores:  ``_forced``, ``_cc``, ``_sdh``, or ``""``.
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
        logger.warning(f"resolve_url probe failed for {url!r}: {exc}")
        return url, ext_from_url(url, "UNK")

    if text.startswith("#EXTM3U"):
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                fmt = ext_from_url(line, "UNK")
                logger.debug(f"Resolved manifest → segment: {line[:80]}")
                return line, fmt
        logger.warning(f"Manifest parsed but no segment found in {url!r}")

    fmt = ext_from_url(url, "UNK")
    return url, fmt


async def download_external_tracks_with_progress(headers: Dict, external_subtitles: List[Dict], external_audios: List[Dict], output_dir: Path, filename: str, bar_manager: Any) -> Tuple[List[Dict], List[Dict]]:
    """Download external tracks with manifest resolution, proper filenames, and progress."""
    ext_subs: List[Dict] = []
    ext_auds: List[Dict] = []
    all_tasks = (
        [(sub, "subtitle") for sub in external_subtitles if sub.get("_selected", True)]
        + [(aud, "audio")  for aud in external_audios    if aud.get("_selected", True)]
    )
    for subs in external_subtitles:
        logger.info(f"Add external subtitle track: {subs}")
    for auds in external_audios:
        logger.info(f"Add external audio track: {auds}")

    if not all_tasks:
        return ext_subs, ext_auds

    progress = bar_manager.progress
    tasks = bar_manager.tasks

    async with create_async_client(headers=headers) as client:
        for track, track_type in all_tasks:
            try:
                lang_raw = (track.get("language") or "unknown").strip()
                #forced   = bool(track.get("forced"))
                #sdh      = bool(track.get("sdh"))
                #cc       = bool(track.get("cc"))

                # ── Resolve manifest → actual segment URL ───────────────
                raw_url = track["url"]
                final_url, fmt = await resolve_url(client, raw_url, track_type)

                # ── Build normalised filename ───────────────────────────
                base_lang, flag_suffix = normalize_sub_filename(lang_raw, track)
                out_path = output_dir / f"{filename}.{base_lang}{flag_suffix}.{fmt}"

                # ── Reuse pre-created task or create fallback ───────────
                task_key = track.get("_task_key", f"ext_{track_type}_{lang_raw}_{id(track)}")
                task_id  = tasks.get(task_key)
                
                new_label = build_ext_track_label(track, track_type, ext_override=fmt)
                if task_id is not None and progress:
                    progress.update(task_id, description=f"[cyan]{new_label}")
                elif task_id is None and progress:
                    task_id = progress.add_task(f"[cyan]{new_label}", total=100, segment="0/0", speed="0Bps", size="0B/0B")
                    tasks[task_key] = task_id

                logger.info(f"Downloading external {track_type}: {lang_raw} → {out_path.name}")

                # ── Stream download with live progress ──────────────────
                async with client.stream("GET", final_url) as response:
                    response.raise_for_status()
                    total_size = int(response.headers.get("content-length", 0))
                    downloaded = 0
                    t_start    = time.monotonic()

                    with open(out_path, "wb") as f:
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            f.write(chunk)
                            downloaded += len(chunk)

                            if task_id is not None and progress:
                                elapsed   = max(time.monotonic() - t_start, 0.001)
                                pct       = int((downloaded / total_size) * 100) if total_size else 0
                                dl_kb     = downloaded / 1024
                                tot_kb    = total_size / 1024 if total_size else 0
                                speed_kb  = dl_kb / elapsed
                                size_str  = (f"{dl_kb:.0f}KB/{tot_kb:.0f}KB" if tot_kb else f"{dl_kb:.0f}KB")
                                speed_str = (f"{speed_kb / 1024:.2f}MBps" if speed_kb >= 1024 else f"{speed_kb:.0f}KBps")
                                progress.update(task_id, completed=pct, size=size_str, speed=speed_str)

                size = out_path.stat().st_size if out_path.exists() else 0
                if size > 0:
                    entry = {
                        "path":     str(out_path),
                        "language": f"{base_lang}{flag_suffix}",
                        "type":     fmt,
                        "size":     size,
                    }
                    if track_type == "subtitle":
                        ext_subs.append(entry)
                    else:
                        ext_auds.append(entry)

                    if task_id is not None and progress:
                        final_kb  = size / 1024
                        size_disp = (
                            f"{size / (1024**2):.2f}MB" if final_kb >= 1024
                            else f"{final_kb:.0f}KB"
                        )
                        progress.update(task_id, completed=100, size=size_disp, speed="")

                    logger.info(f"Downloaded {track_type} {lang_raw}: {size} bytes → {out_path.name}")
                else:
                    logger.warning(f"Failed to download {track_type} {lang_raw} (empty file)")
                    if task_id is not None and progress:
                        progress.update(task_id, speed="FAILED")

            except Exception as exc:
                logger.warning(f"External {track_type} download failed ({track.get('language','?')}): {exc}")
                tid = tasks.get(track.get("_task_key", ""))
                if tid is not None and progress:
                    progress.update(tid, speed="ERR")

    return ext_subs, ext_auds