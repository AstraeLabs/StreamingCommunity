# 04.01.25

from __future__ import annotations

import asyncio
import logging
import platform
import re
import subprocess
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console
from rich.progress import Progress, TextColumn

from VibraVid.setup import get_ffmpeg_path, get_n_m3u8dl_re_path
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_async_client
from VibraVid.source.style.tracker import download_tracker, context_tracker
from VibraVid.source.utils.selector import StreamSelector
from VibraVid.source.style.ui import build_table
from VibraVid.source.style.progress_bar import CustomBarColumn, ColoredSegmentColumn, CompactTimeColumn, CompactTimeRemainingColumn, SizeColumn

from VibraVid.core.manifest.m3u8 import HLSParser
from VibraVid.core.manifest.mpd import DashParser
from VibraVid.core.manifest.stream import Stream

from .pattern import (PERCENT_RE, SPEED_RE, SIZE_RE, SEGMENT_RE, SUBTITLE_FINAL_SIZE_RE)


console = Console(force_terminal=True if platform.system().lower() != "windows" else None)
logger = logging.getLogger("Source")
_c = config_manager.config
CONCURRENT_DOWNLOAD = _c.get_bool("DOWNLOAD", "concurrent_download")
THREAD_COUNT = _c.get_int("DOWNLOAD", "thread_count")
RETRY_COUNT = _c.get_int("DOWNLOAD", "retry_count")
REQUEST_TIMEOUT = _c.get_int("REQUESTS", "timeout")
MAX_SPEED = _c.get("DOWNLOAD", "max_speed")
USE_PROXY = _c.get_bool("REQUESTS", "use_proxy")
PROXY_CFG = _c.get_dict("REQUESTS", "proxy")
_SUBFIN_RE = SUBTITLE_FINAL_SIZE_RE


class MediaDownloader:
    """
    Thin wrapper around N-m3u8DL-RE (n3u8dl).

    Responsibilities
    ----------------
    * Fetch and parse the manifest (HLS or DASH) via the core parsers.
    * Apply StreamSelector → mark ``stream.selected`` → build n3u8dl args.
    * Run n3u8dl subprocess with a live Rich progress bar.
    * Post-process the output directory → build a ``status`` dict.
    * Optionally decrypt with Bento4 / Shaka Packager.
    """
    def __init__(self, url: str, output_dir: str, filename: str, headers: Optional[Dict] = None, key: Optional[Any] = None, cookies: Optional[Dict] = None, decrypt_preference: str = "shaka", download_id: Optional[str] = None, site_name: Optional[str] = None,):
        self.url = url
        self.output_dir = Path(output_dir)
        self.filename = filename
        self.headers = headers or {}
        self.key = key
        self.cookies = cookies or {}
        self.decrypt_preference = decrypt_preference.strip().lower()
        self.download_id = download_id
        self.site_name = site_name

        # Populated after parse_stream
        self.streams: List[Stream] = []
        self.manifest_type: str = "Unknown"
        self.raw_m3u8: Optional[Path] = None
        self.raw_mpd: Optional[Path] = None
        self.status: Optional[dict] = None

        # n3u8dl selection args (set by _apply_selection)
        self._sv: str = "best"
        self._sa: str = "best"
        self._ss: str = "all"

        # External tracks injected before parse_stream
        self.external_subtitles: list = []
        self.external_audios: list = []

        # Per-call filter overrides (override config.json)
        self.custom_filters: Optional[Dict[str, str]] = None

        # Passthrough fields for DASH decryptor
        self.license_url: Optional[str] = None
        self.drm_type: Optional[str] = None

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_dir = self.output_dir / f"{self.filename}_tmp"
        self._tmp_dir.mkdir(exist_ok=True)

        if self.download_id:
            _output_type = (
                "Movie"
                if _c.get("OUTPUT", "movie_folder_name") in str(self.output_dir)
                else "TV"
                if _c.get("OUTPUT", "serie_folder_name") in str(self.output_dir)
                else "Anime"
                if _c.get("OUTPUT", "anime_folder_name") in str(self.output_dir)
                else "other"
            )
            download_tracker.start_download(self.download_id, self.filename, self.site_name or "Unknown", _output_type)

    def set_key(self, key: Any) -> None:
        """Accept str, list[str], or KeysManager."""
        from VibraVid.source.utils.object import KeysManager

        if isinstance(key, KeysManager):
            self.key = key.get_keys_list()
        else:
            self.key = key

    def parse_stream(self, show_table: bool = True) -> List[Stream]:
        """
        Fetch the manifest, parse all streams, apply StreamSelector
        (marks ``stream.selected`` and generates n3u8dl args), then
        optionally print the selection table.
        """
        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing …")

        # ── Detect format ─────────────────────────────────────────────────────
        url_lower = self.url.lower().split("?")[0]
        if url_lower.endswith(".mpd") or "mpd" in url_lower:
            parser = DashParser(self.url, self.headers)
        else:
            parser = HLSParser(self.url, self.headers)

        if not parser.fetch_manifest():
            logger.error("MediaDownloader: manifest fetch failed")
            return []

        # ── Save raw manifest ─────────────────────────────────────────────────
        if isinstance(parser, DashParser):
            self.raw_mpd = parser.save_raw(self._tmp_dir)
            self.manifest_type = "DASH"
        else:
            self.raw_m3u8 = parser.save_raw(self._tmp_dir)
            self.manifest_type = "HLS"

        # ── Parse streams ─────────────────────────────────────────────────────
        self.streams = [s for s in parser.parse_streams() if s.type != "image"]

        # ── Apply selection ───────────────────────────────────────────────────
        self._apply_selection()

        # ── Attach external subtitles ─────────────────────────────────────────
        for ext in self.external_subtitles:
            lang = ext.get("language", "")
            selected = self._ext_lang_matches(lang, "subtitle")
            ext["_selected"] = selected
            fake = Stream(
                type="subtitle",
                language=lang,
                name=ext.get("name", ""),
                selected=selected,
                is_external=True,
            )
            fake.id = "EXT"
            self.streams.append(fake)

        # ── Attach external audios ────────────────────────────────────────────
        for ext in self.external_audios:
            lang = ext.get("language", "")
            selected = self._ext_lang_matches(lang, "audio")
            ext["_selected"] = selected
            fake = Stream(
                type="audio",
                language=lang,
                name=ext.get("name", ""),
                selected=selected,
                is_external=True,
            )
            fake.id = "EXT"
            self.streams.append(fake)

        if show_table and self.streams:
            console.print(build_table(self.streams))

        return self.streams

    # Alias for backward compat with any callers using the v0 name
    parser_stream = parse_stream

    def get_metadata(self) -> Tuple[str, str, str]:
        """Return (raw_m3u8_path, raw_mpd_path, '') — strings, not Path objects."""
        return (str(self.raw_m3u8), str(self.raw_mpd), "")

    def _apply_selection(self) -> None:
        f = self.custom_filters or {}
        v_cfg = f.get("video") or _c.get("DOWNLOAD", "select_video")
        a_cfg = f.get("audio") or _c.get("DOWNLOAD", "select_audio")
        s_cfg = f.get("subtitle") or _c.get("DOWNLOAD", "select_subtitle")

        selector = StreamSelector(v_cfg, a_cfg, s_cfg)
        self._sv, self._sa, self._ss = selector.apply(self.streams)
        logger.info(f"Selection → video={self._sv!r}  audio={self._sa!r}  subtitle={self._ss!r}")

    def _ext_lang_matches(self, lang: str, track_type: str) -> bool:
        cfg_key = "select_subtitle" if track_type == "subtitle" else "select_audio"
        cfg = _c.get("DOWNLOAD", cfg_key, default="all")
        if not cfg or cfg.lower() == "all":
            return True
        if cfg.lower() == "false":
            return False
        tokens = [t.strip() for t in re.split(r"[|,]", cfg) if t.strip()]
        return any(t.lower() in lang.lower() for t in tokens)


    def start_download(self) -> Dict[str, Any]:
        """Build the n3u8dl command and run it.  Returns the status dict."""
        sv = self._sv or "worst"
        sa = self._sa or "worst"
        ss = self._ss or "all"

        cmd = [
            get_n_m3u8dl_re_path(),
            "--save-name", self.filename,
            "--save-dir", str(self.output_dir),
            "--tmp-dir", str(self._tmp_dir),
            "--ffmpeg-binary-path", get_ffmpeg_path(),
            "--write-meta-json", "false",
            "--binary-merge",
            "--del-after-done",
            "--auto-subtitle-fix", "false",
            "--check-segments-count", "false",
            "--mp4-real-time-decryption", "false",
            "--no-log",
        ]

        if sv == "false":
            cmd.extend(["--drop-video", "all"])
        else:
            cmd.extend(["--select-video", sv])

        if sa == "false":
            cmd.extend(["--drop-audio", "all"])
        else:
            cmd.extend(["--select-audio", sa])

        if ss == "false":
            cmd.extend(["--drop-subtitle", "all"])
        else:
            cmd.extend(["--select-subtitle", ss])

        cmd.extend(self._common_args())

        if CONCURRENT_DOWNLOAD:
            cmd.append("--concurrent-download")
        if THREAD_COUNT > 0:
            cmd.extend(["--thread-count", str(THREAD_COUNT)])
        if REQUEST_TIMEOUT > 0:
            cmd.extend(["--http-request-timeout", str(REQUEST_TIMEOUT)])
        if RETRY_COUNT > 0:
            cmd.extend(["--download-retry-count", str(RETRY_COUNT)])
        if MAX_SPEED and str(MAX_SPEED).lower() not in ("", "false"):
            cmd.extend(["--max-speed", str(MAX_SPEED)])

        cmd.append(self.url)
        logger.info(f"N_m3u8DL-RE command: {' '.join(cmd)}")

        # Download external tracks concurrently
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            ext_subs, ext_auds = loop.run_until_complete(self._download_external_tracks())
        finally:
            loop.close()

        subtitle_sizes: Dict[str, str] = {}

        progress_ctx = (
            nullcontext()
            if context_tracker.is_gui
            else Progress(
                TextColumn("[purple]{task.description}", justify="left"),
                CustomBarColumn(bar_width=40),
                ColoredSegmentColumn(),
                TextColumn("[dim][[/dim]"),
                CompactTimeColumn(),
                TextColumn("[dim]<[/dim]"),
                CompactTimeRemainingColumn(),
                TextColumn("[dim]][/dim]"),
                SizeColumn(),
                TextColumn("[dim]@[/dim]"),
                TextColumn("[red]{task.fields[speed]}[/red]", justify="right"),
                console=console,
                refresh_per_second=10.0,
            )
        )

        with progress_ctx as progress:
            tasks: Dict[str, Any] = {}
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1, universal_newlines=True)
            if self.download_id:
                download_tracker.register_process(self.download_id, proc)

            with proc:
                for line in proc.stdout:
                    if " : " in str(line):
                        logger.info(f"{line.rstrip()}")
                    if self.download_id and download_tracker.is_stopped(self.download_id):
                        proc.terminate()
                        break
                    self._parse_progress_line(line, progress, tasks, subtitle_sizes)

                if progress:
                    for tid in tasks.values():
                        progress.update(tid, completed=100)

        if self.download_id and download_tracker.is_stopped(self.download_id):
            return {"error": "cancelled"}

        self.status = self._build_status(subtitle_sizes, ext_subs, ext_auds)

        if self.key:
            self._decrypt_check(self.status)

        return self.status

    def _common_args(self) -> List[str]:
        cmd: List[str] = []
        for k, v in self.headers.items():
            cmd.extend(["--header", f"{k}: {v}"])
        if self.cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            cmd.extend(["--header", f"Cookie: {cookie_str}"])
        if USE_PROXY:
            proxy = PROXY_CFG.get("https") or PROXY_CFG.get("http", "")
            if proxy:
                cmd.extend(["--use-system-proxy", "false", "--custom-proxy", proxy])
        return cmd

    def _decrypt_check(self, status: Dict[str, Any]) -> None:
        if self.download_id:
            download_tracker.update_status(self.download_id, "Decrypting …")

        from VibraVid.source.utils.decrypt import Decryptor
        from VibraVid.source.utils.object import KeysManager

        decryptor = Decryptor(preference=self.decrypt_preference, license_url=self.license_url, drm_type=self.drm_type,)
        keys = (self.key.get_keys_list() if isinstance(self.key, KeysManager) else ([self.key] if isinstance(self.key, str) else self.key))

        targets = []
        if status.get("video"):
            targets.append((status["video"], "video"))
        for aud in status.get("audios", []):
            targets.append((aud, "audio"))

        for target, stype in targets:
            fp = Path(target["path"])
            if not fp.exists():
                continue
            scheme, *_ = decryptor.detect_encryption(str(fp))
            if scheme is None:
                continue
            out = fp.with_suffix(fp.suffix + ".dec")
            if decryptor.decrypt(str(fp), keys, str(out), stream_type=stype):
                try:
                    fp.unlink()
                    out.rename(fp)
                    target["size"] = fp.stat().st_size
                except Exception as exc:
                    logger.error(f"Failed to replace encrypted file: {exc}")
                    if out.exists():
                        out.unlink()
            else:
                if out.exists():
                    out.unlink()

    async def _download_external_tracks(self) -> Tuple[List[Dict], List[Dict]]:
        ext_subs: List[Dict] = []
        ext_auds: List[Dict] = []

        all_tasks = [(sub, "subtitle") for sub in self.external_subtitles if sub.get("_selected", True) ] + [(aud, "audio") for aud in self.external_audios if aud.get("_selected", True)]
        if not all_tasks:
            return ext_subs, ext_auds

        async with create_async_client(headers=self.headers) as client:
            for track, track_type in all_tasks:
                try:
                    logger.info(f"Downloading external {track_type}: {track.get('name') or track.get('language') or track.get('type') or 'unknown'}")
                    lang = track.get("language", "unknown")
                    flag = ""
                    if track.get("forced"):
                        flag = ".forced"
                    elif track.get("sdh"):
                        flag = ".sdh"

                    fmt = (track.get("type") or track.get("format") or ("srt" if track_type == "subtitle" else "m4a"))
                    if fmt == "captions":
                        fmt = "vtt"

                    logger.info(f"URL: {track['url']}")
                    out_path = self.output_dir / f"{self.filename}.{lang}{flag}.{fmt}"
                    r = await client.get(track["url"])
                    r.raise_for_status()
                    out_path.write_bytes(r.content)

                    entry = {
                        "path": str(out_path),
                        "language": f"{lang}{flag}",   # ← include il flag nel language
                        "type": fmt,
                        "size": len(r.content),
                    }

                    if track_type == "subtitle":
                        ext_subs.append(entry)
                    else:
                        ext_auds.append(entry)
                except Exception as exc:
                    logger.warning(f"External {track_type} download failed: {exc}")

        return ext_subs, ext_auds

    def _build_status(self, subtitle_sizes: Dict, ext_subs: List, ext_auds: List = None) -> Dict:
        """
        Scan output_dir and build the status dict:
          { video, audios, subtitles, external_subtitles, external_audios }

        Subtitle naming logic (3-pass):
          Pass 1 — detect forced/CC from filename tag + match n3u8dl progress metadata
          Pass 2 — size-based CC disambiguation within same-language groups
          Pass 3 — assign final names, handle duplicates
        """
        status: Dict[str, Any] = {
            "video": None,
            "audios": [],
            "subtitles": [],
            "external_subtitles": ext_subs or [],
            "external_audios": ext_auds or [],
        }

        VIDEO_EXTS = {".mp4", ".mkv", ".m4v", ".ts", ".mov", ".webm"}
        AUDIO_EXTS = {".m4a", ".aac", ".mp3", ".ts", ".mp4", ".wav", ".webm"}
        SUB_EXTS = {".srt", ".vtt", ".ass", ".sub", ".ssa", ".m4s", ".ttml", ".xml"}

        # ── Build progress-metadata lookup ────────────────────────────────────
        downloaded_subs: List[Dict] = []
        for key_str, size_str in subtitle_sizes.items():
            parts = key_str.split(":", 1)
            raw_lang = parts[0].strip()
            size_b = _parse_size_str(size_str)

            is_forced = bool(re.search(r"(?:^|[-_.])forced(?:$|[-_.])", raw_lang, re.IGNORECASE))
            is_cc = bool(
                re.search(r"(?:^|[-_.])(?:cc|sdh|captions?)(?:$|[-_.])", raw_lang, re.IGNORECASE)
            )
            base_lang = re.sub(r"(?:^|[-_.])(?:forced|cc|sdh|captions?)(?:$|[-_.])", "", raw_lang, flags=re.IGNORECASE,).strip("-_.")

            downloaded_subs.append(
                {
                    "raw_lang": raw_lang,
                    "base_lang": base_lang or raw_lang,
                    "is_forced": is_forced,
                    "is_cc": is_cc,
                    "size": size_b,
                    "used": False,
                }
            )

        def _norm_tokens(lang: str) -> set:
            return set(lang.lower().replace("-", ".").split("."))

        # ── Pass 1: collect subtitle candidates ───────────────────────────────
        sub_candidates: List[Dict] = []

        for f in sorted(self.output_dir.iterdir()):
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            f_name_l = f.name.lower()
            fname_l = self.filename.lower()

            if ext in VIDEO_EXTS and f.stem.lower() == fname_l:
                if status["video"] is None:
                    status["video"] = {"path": str(f), "size": f.stat().st_size}
                continue

            if ext in AUDIO_EXTS and f_name_l.startswith(fname_l):
                if status["video"] and Path(status["video"]["path"]).name == f.name:
                    continue
                track_name = f.stem[len(self.filename) :].lstrip(".")
                status["audios"].append(
                    {"path": str(f), "name": track_name, "size": f.stat().st_size}
                )
                continue

            if ext not in SUB_EXTS or not f_name_l.startswith(fname_l):
                continue

            f_size = f.stat().st_size
            raw_tag = f.stem[len(self.filename) :].lstrip(".")

            tag_forced = bool(re.search(r"(?:^|[-_.])forced(?:$|[-_.])", raw_tag, re.IGNORECASE))
            tag_cc = bool(
                re.search(r"(?:^|[-_.])(?:cc|sdh|captions?)(?:$|[-_.])", raw_tag, re.IGNORECASE)
            )
            tag_base = re.sub(r"(?:^|[-_.])(?:forced|cc|sdh|captions?)(?:$|[-_.])", "", raw_tag, flags=re.IGNORECASE).strip("-_.")
            tag_base = tag_base or raw_tag

            # N-m3u8DL-RE CC convention: "lang.lang" (e.g. "fre.fre")
            if not tag_cc and not tag_forced and "." in tag_base:
                _parts = tag_base.split(".")
                if len(_parts) == 2 and _parts[0].lower() == _parts[1].lower():
                    tag_base = _parts[0]
                    tag_cc = True

            best_meta = None
            min_diff = float("inf")
            f_tokens = _norm_tokens(tag_base)
            for meta in downloaded_subs:
                if meta["used"]:
                    continue

                m_tokens = _norm_tokens(meta["base_lang"])
                overlap = f_tokens & m_tokens
                diff = abs(meta["size"] - f_size)
                if ((not f_tokens or not m_tokens or overlap) and diff < min_diff and diff <= 2048):
                    min_diff = diff
                    best_meta = meta

            if best_meta:
                best_meta["used"] = True
                is_forced = tag_forced or best_meta["is_forced"]
                is_cc = tag_cc or best_meta["is_cc"]
                base_lang = tag_base or best_meta["base_lang"]
            else:
                is_forced = tag_forced
                is_cc = tag_cc
                base_lang = tag_base

            sub_candidates.append(
                {
                    "path": str(f),
                    "size": f_size,
                    "base_lang": base_lang,
                    "is_forced": is_forced,
                    "is_cc": is_cc,
                    "tag_explicit": tag_forced or tag_cc,
                }
            )

        # ── Pass 2: size-based CC disambiguation ──────────────────────────────
        from collections import defaultdict

        _lang_groups: dict = defaultdict(list)
        for cand in sub_candidates:
            if not cand["is_forced"]:
                _lang_groups[cand["base_lang"]].append(cand)

        for base_lang, group in _lang_groups.items():
            untagged = [c for c in group if not c["is_cc"] and not c["tag_explicit"]]
            if len(group) >= 2 and untagged:
                sorted_group = sorted(group, key=lambda c: c["size"], reverse=True)
                for i, cand in enumerate(sorted_group):
                    if cand["tag_explicit"]:
                        continue
                    cand["is_cc"] = i == 0

        # ── Pass 3: assign final names ────────────────────────────────────────
        seen_normal: Dict[str, int] = {}
        for cand in sub_candidates:
            base_lang = cand["base_lang"]
            if cand["is_forced"]:
                final_name = f"{base_lang}_forced"
            elif cand["is_cc"]:
                final_name = f"{base_lang}_cc"
            else:
                count = seen_normal.get(base_lang, 0)
                final_name = base_lang if count == 0 else f"{base_lang} ({count + 1})"
                seen_normal[base_lang] = count + 1

            status["subtitles"].append(
                {
                    "path": cand["path"],
                    "language": final_name,
                    "name": final_name,
                    "size": cand["size"],
                }
            )

        return status

    def get_status(self) -> Dict:
        return self.status or self._build_status({}, [], [])

    def _update_task(self, progress, tasks: dict, key: str, label: str, line: str) -> Any:
        if key not in tasks:
            tasks[key] = (
                progress.add_task(
                    f"[yellow]{self.manifest_type} {label}",
                    total=100,
                    segment="0/0",
                    speed="0Bps",
                    size="0B/0B",
                )
                if progress
                else "gui"
            )

        task = tasks[key]

        # Always update the tracker (needed for GUI mode where progress is None)
        if self.download_id:
            pct = (float(PERCENT_RE.search(line).group(1)) if PERCENT_RE.search(line) else None)
            spd = SPEED_RE.search(line).group(1) if SPEED_RE.search(line) else None
            sz = (f"{SIZE_RE.search(line).group(1)}/{SIZE_RE.search(line).group(2)}" if SIZE_RE.search(line) else None)
            seg = SEGMENT_RE.search(line).group(0) if SEGMENT_RE.search(line) else None
            download_tracker.update_progress(self.download_id, key, pct, spd, sz, seg)

        if not progress or task == "gui":
            return task

        if m := SEGMENT_RE.search(line):
            progress.update(task, segment=m.group(0))
        if m := PERCENT_RE.search(line):
            try:
                progress.update(task, completed=float(m.group(1)))
            except Exception:
                pass
        if m := SPEED_RE.search(line):
            progress.update(task, speed=m.group(1))
        if m := SIZE_RE.search(line):
            progress.update(task, size=f"{m.group(1)}/{m.group(2)}")

        return task

    def _parse_progress_line(self, line: str, progress, tasks: dict, subtitle_sizes: dict) -> None:
        if line.startswith("Vid"):
            res = next(
                (
                    s.resolution
                    for s in self.streams
                    if s.type == "video" and s.selected
                ),
                "main",
            )
            self._update_task(progress, tasks, f"vid_{res}", f"[cyan]Vid [red]{res}", line)

        elif line.startswith("Aud"):
            m = re.search(r"Aud\s+(\S+)", line)
            tag = m.group(1) if m else "aud"
            self._update_task(progress, tasks, f"aud_{tag}", f"[cyan]Aud [red]{tag}", line)

        elif line.startswith("Sub"):
            m = re.search(r"Sub\s+(\S+)\s*\|\s*(\S+)", line)
            if m:
                lang, codec = m.group(1), m.group(2)
                from VibraVid.source.utils.codec import get_subtitle_codec_name

                display = get_subtitle_codec_name(lang)
                task = self._update_task(progress, tasks, f"sub_{lang}_{codec}", f"[cyan]Sub [red]{display}", line)
                if fm := _SUBFIN_RE.search(line):
                    if progress and task not in (None, "gui"):
                        progress.update(task, size=fm.group(1), completed=100)
                    subtitle_sizes[f"{lang}:{codec}"] = fm.group(1)

_SIZE_UNITS = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}


def _parse_size_str(s: str) -> int:
    """Convert '123.4KB' → bytes (int)."""
    try:
        m = re.match(r"([\d.]+)\s*(B|KB|MB|GB)", s, re.IGNORECASE)
        if m:
            return int(float(m.group(1)) * _SIZE_UNITS[m.group(2).upper()])
    except Exception:
        pass
    return 0