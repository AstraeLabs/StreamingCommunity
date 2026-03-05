# 10.01.26

import re
import json
import platform
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from contextlib import nullcontext

from rich.console import Console
from rich.progress import Progress, TextColumn

from VibraVid.utils.config import config_manager
from VibraVid.source.utils.tracker import download_tracker, context_tracker
from VibraVid.source.Manual.decrypt.decrypt import Decryptor
from ..N_m3u8.progress_bar import (CustomBarColumn, CompactTimeColumn, CompactTimeRemainingColumn, SizeColumn)

from ..utils.object import StreamInfo, KeysManager
from .parser import parse_ytdlp_json
from .pattern import pick_best_video, audio_matches_filter, _parse_lang_list
from .progress_pattern import YTDLP_PROGRESS_RE, DEST_RE
from .ui import build_table


console = Console(force_terminal=True if platform.system().lower() != "windows" else None)
YTDLP_BIN = ""
auto_select_cfg = config_manager.config.get_bool("DOWNLOAD", "auto_select", default=True)
video_filter = config_manager.config.get("DOWNLOAD", "select_video")
audio_filter = config_manager.config.get("DOWNLOAD", "select_audio")
subtitle_filter = config_manager.config.get("DOWNLOAD", "select_subtitle")
max_speed = config_manager.config.get("DOWNLOAD", "max_speed")
retry_count = config_manager.config.get_int("DOWNLOAD", "retry_count")
request_timeout = config_manager.config.get_int("REQUESTS", "timeout")
use_proxy = config_manager.config.get_bool("REQUESTS", "use_proxy")
configuration_proxy  = config_manager.config.get_dict("REQUESTS", "proxy", default={})
concurrent_fragments = config_manager.config.get_int("DOWNLOAD", "threads", default=4)


def _vtt_to_srt(vtt: str) -> str:
    """Convert WebVTT text to SRT format."""
    lines = vtt.splitlines()
    out, counter, i = [], 1, 0
    while i < len(lines):
        line = lines[i].strip()
        if " --> " in line and re.search(r"\d{2}:\d{2}[\.:]\d", line):
            ts = re.sub(r"(\d{2}:\d{2}:\d{2})\.(\d{3}).*-->\s*(\d{2}:\d{2}:\d{2})\.(\d{3}).*", r"\1,\2 --> \3,\4", line)
            # also handle MM:SS.mmm format (no hours)
            ts = re.sub(r"(\d{2}:\d{2})\.(\d{3}).*-->\s*(\d{2}:\d{2})\.(\d{3}).*", r"00:\1,\2 --> 00:\3,\4", ts)
            body = []
            i += 1
            while i < len(lines) and lines[i].strip():
                body.append(lines[i].strip())
                i += 1
            if body:
                out += [str(counter), ts] + body + [""]
                counter += 1
        else:
            i += 1
    return "\n".join(out)


def _fetch_url(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def _resolve_m3u8_subtitle(m3u8_url: str, headers: dict) -> str:
    """Download an HLS subtitle playlist and return concatenated VTT text."""
    try:
        raw = _fetch_url(m3u8_url, headers).decode("utf-8", errors="replace")
    except Exception as e:
        raise RuntimeError(f"Cannot fetch m3u8 subtitle: {e}") from e

    base = m3u8_url.rsplit("/", 1)[0]
    segments = []
    for line in raw.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            seg_url = line if line.startswith("http") else f"{base}/{line}"
            segments.append(seg_url)

    parts = []
    for seg_url in segments:
        try:
            content = _fetch_url(seg_url, headers).decode("utf-8", errors="replace")
            content = re.sub(r"^WEBVTT.*?\n\n", "", content, flags=re.DOTALL)
            parts.append(content)
        except Exception as e:
            console.print(f"[yellow]Subtitle segment failed: {e}[/yellow]")

    return "WEBVTT\n\n" + "\n".join(parts)


class MediaDownloader:
    def __init__(self, url: str, output_dir: str, filename: str, headers: Optional[Dict] = None, key: Optional[str] = None, cookies: Optional[Dict] = None, decrypt_preference: str = "shaka", download_id: str = None, site_name: str = None):
        self.url               = url
        self.output_dir        = Path(output_dir)
        self.filename          = filename
        self.headers           = headers or {}
        self.key               = key
        self.cookies           = cookies or {}
        self.decrypt_preference = decrypt_preference.strip().lower()
        self.download_id       = download_id
        self.site_name         = site_name

        self.streams: List[StreamInfo] = []
        self.status: Optional[Dict]    = None
        self.custom_filters: Optional[Dict] = None
        self.decryptor = Decryptor(preference=self.decrypt_preference)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir_type = (
            "Movie" if config_manager.config.get("OUTPUT", "movie_folder_name") in str(self.output_dir)
            else "TV" if config_manager.config.get("OUTPUT", "serie_folder_name") in str(self.output_dir)
            else "Anime" if config_manager.config.get("OUTPUT", "anime_folder_name") in str(self.output_dir)
            else "other"
        )

        if self.download_id:
            download_tracker.start_download(
                self.download_id, self.filename,
                self.site_name or "Unknown", self.output_dir_type,
            )

    def _header_args(self) -> List[str]:
        args = []
        for k, v in self.headers.items():
            args += ["--add-header", f"{k}:{v}"]
        if self.cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
            args += ["--add-header", f"Cookie:{cookie_str}"]
        return args

    def _proxy_args(self) -> List[str]:
        if use_proxy:
            proxy_url = configuration_proxy.get("https") or configuration_proxy.get("http")
            if proxy_url:
                return ["--proxy", proxy_url]
        return []

    def _rate_args(self) -> List[str]:
        args = []
        if max_speed and str(max_speed).lower() not in ("false", "none", ""):
            args += ["--limit-rate", max_speed]
        if retry_count and retry_count > 0:
            args += ["--retries", str(retry_count)]
        if request_timeout and request_timeout > 0:
            args += ["--socket-timeout", str(request_timeout)]
        return args

    def _concurrent_args(self) -> List[str]:
        """Add --concurrent-fragments for parallel HLS/DASH segment downloads."""
        n = concurrent_fragments
        if n and n > 1:
            return ["--concurrent-fragments", str(n)]
        return []

    def _keys_list(self) -> List[str]:
        """Normalise self.key into a flat list of 'kid:key' strings for Decryptor."""
        if not self.key:
            return []
        if isinstance(self.key, list):
            return [str(k) for k in self.key if k]
        if isinstance(self.key, str):
            return [self.key]
        return []

    def _active_filters(self) -> tuple:
        f = self.custom_filters or {}
        return (
            f.get("video",    video_filter),
            f.get("audio",    audio_filter),
            f.get("subtitle", subtitle_filter),
        )

    def _http_headers(self) -> dict:
        """Headers dict suitable for urllib requests."""
        h = dict(self.headers)
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        return h

    def parser_stream(self, show_table: bool = True) -> List[StreamInfo]:
        """
        Run `yt-dlp -j` to obtain full metadata JSON, save it as meta.json,
        then parse all formats (video / audio / subtitle) from that JSON.
        """
        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing...")

        cmd = [
            YTDLP_BIN,
            "-j",
            "--no-playlist",
            "--no-warnings",
            "--allow-unplayable-formats",
        ]
        cmd += self._header_args()
        cmd += self._proxy_args()
        cmd.append(self.url)

        log_path  = self.output_dir / f"{self.filename}_parsing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        meta_path = self.output_dir / f"{self.filename}_meta.json"

        raw_json = ""
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            with open(log_path, "w", encoding="utf-8", errors="replace") as lf:
                lf.write(f"Command: {' '.join(cmd)}\n{'=' * 80}\n\n")
                for line in proc.stdout:
                    raw_json += line
                    lf.write(line)
                proc.wait()
        except Exception as e:
            console.print(f"[red]yt-dlp -j failed: {e}[/red]")
            return []

        # Save raw JSON
        try:
            json_data = json.loads(raw_json)
            with open(meta_path, "w", encoding="utf-8") as jf:
                json.dump(json_data, jf, indent=2, ensure_ascii=False)
        except json.JSONDecodeError as e:
            console.print(f"[red]JSON parse error: {e}[/red]")
            return []

        self.streams = parse_ytdlp_json(json_data)

        if auto_select_cfg and self.streams:
            self._auto_select_streams()

        if show_table and self.streams:
            selected_set = {i for i, s in enumerate(self.streams) if s.selected}
            if context_tracker.should_print:
                console.print(build_table(
                    self.streams, selected_set, 0,
                    window_size=len(self.streams), highlight_cursor=False,
                ))

            if self.download_id:
                sel_v = next((s for s in self.streams if s.type == "Video" and s.selected), None)
                sel_a = next((s for s in self.streams if s.type == "Audio" and s.selected), None)
                download_tracker.update_info(
                    self.download_id,
                    quality  = sel_v.resolution if sel_v else "",
                    language = sel_a.language   if sel_a else "",
                )

        return self.streams

    def _auto_select_streams(self):
        v_filter, a_filter, s_filter = self._active_filters()

        videos    = [s for s in self.streams if s.type == "Video"]
        audios    = [s for s in self.streams if s.type == "Audio"]
        subtitles = [s for s in self.streams if s.type == "Subtitle"]

        best_v = pick_best_video(videos, v_filter)
        if best_v:
            best_v.selected = True

        for a in audios:
            if audio_matches_filter(a.language or "", a.codec or "", a_filter):
                a.selected = True

        if s_filter and s_filter.lower() not in ("none", "false", ""):
            langs = _parse_lang_list(s_filter)
            for s in subtitles:
                if not langs or any(lang in (s.language or "").lower() for lang in langs):
                    s.selected = True

    def start_download(self) -> Dict[str, Any]:
        """
        Download selected streams in order: video → each audio → subtitles. Each stream is a separate yt-dlp invocation.
        If keys are set, each downloaded file is decrypted immediately after download using the Decryptor before moving on to the next stream.
        """
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading")

        keys = self._keys_list()

        selected_video  = next((s for s in self.streams if s.type == "Video"    and s.selected), None)
        selected_audios = [s for s in self.streams if s.type == "Audio"    and s.selected]
        selected_subs   = [s for s in self.streams if s.type == "Subtitle" and s.selected]

        progress_ctx = (
            nullcontext()
            if (not auto_select_cfg or context_tracker.is_gui or context_tracker.is_parallel_cli)
            else Progress(
                TextColumn("[purple]{task.description}", justify="left"),
                CustomBarColumn(bar_width=40),
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

        log_path = self.output_dir / f"{self.filename}_download_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

        with open(log_path, "w", encoding="utf-8", errors="replace") as log_file, progress_ctx as progress:

            # ── 1. Video ────────────────────────────────────────────────────────
            if selected_video:
                res_str = selected_video.resolution or ""
                label   = f"[cyan]Vid [red]{selected_video.id}[/red][cyan]{' ' + res_str if res_str else ''}"

                # Download to a temp '_enc' path so decrypted output keeps the clean name
                enc_output = str(self.output_dir / f"{self.filename}_enc.%(ext)s")
                enc_path   = self._run_single_download(selected_video.id, enc_output, label, progress, log_file)

                if enc_path:
                    if keys:
                        dec_path = self.output_dir / f"{self.filename}{enc_path.suffix}"
                        console.print(f"[dim]Decrypting video → {dec_path.name}[/dim]")
                        log_file.write(f"\n[Decrypt video] {enc_path.name} → {dec_path.name}\n")
                        ok = self.decryptor.decrypt(str(enc_path), keys, str(dec_path), stream_type="video")
                        if ok:
                            try:
                                enc_path.unlink()
                            except Exception:
                                pass
                        else:
                            console.print("[yellow]Video decryption failed — keeping encrypted file[/yellow]")
                            dec_path = enc_path   # hand back whatever we have
                    else:
                        # No keys — rename to final name (remove _enc suffix)
                        dec_path = self.output_dir / f"{self.filename}{enc_path.suffix}"
                        try:
                            enc_path.rename(dec_path)
                        except Exception:
                            dec_path = enc_path

            # ── 2. Audio (one call per track) ───────────────────────────────────
            for aud in selected_audios:
                lang   = aud.language or aud.id or "audio"
                label  = f"[cyan]Aud [red]{aud.id}[/red][cyan]{' ' + lang if lang else ''}"

                enc_output = str(self.output_dir / f"{self.filename}.{lang}_enc.%(ext)s")
                enc_path   = self._run_single_download(aud.id, enc_output, label, progress, log_file)

                if enc_path:
                    if keys:
                        dec_path = self.output_dir / f"{self.filename}.{lang}{enc_path.suffix}"
                        console.print(f"[dim]Decrypting audio [{lang}] → {dec_path.name}[/dim]")
                        log_file.write(f"\n[Decrypt audio {lang}] {enc_path.name} → {dec_path.name}\n")
                        ok = self.decryptor.decrypt(str(enc_path), keys, str(dec_path), stream_type="audio")
                        if ok:
                            try:
                                enc_path.unlink()
                            except Exception:
                                pass
                        else:
                            console.print(f"[yellow]Audio [{lang}] decryption failed — keeping encrypted file[/yellow]")
                    else:
                        # No keys — rename to final name
                        dec_path = self.output_dir / f"{self.filename}.{lang}{enc_path.suffix}"
                        try:
                            enc_path.rename(dec_path)
                        except Exception:
                            pass

            # ── 3. Subtitles (manual download, handles m3u8 playlists) ───────────
            for sub in selected_subs:
                self._download_subtitle(sub, log_file)

        if self.download_id and download_tracker.is_stopped(self.download_id):
            return {"error": "cancelled"}

        self.status = self._get_download_status()
        return self.status

    def _run_single_download(self, fmt_id: str, output_template: str, label: str, progress, log_file) -> Optional[Path]:
        """
        Run one yt-dlp process for a single format ID.
        Returns the Path of the downloaded file (captured from yt-dlp's' [download] Destination:' line), or None on failure.
        """
        cmd = [
            YTDLP_BIN,
            "-f", fmt_id,
            "--no-playlist",
            "--allow-unplayable-formats",
            "--newline",
            "--progress",
            "--no-part",
            "--no-mtime",
            "-o", output_template,
        ]
        cmd += self._header_args()
        cmd += self._proxy_args()
        cmd += self._rate_args()
        cmd += self._concurrent_args()
        cmd.append(self.url)

        log_file.write(f"\n[Stream: {fmt_id}] Command: {' '.join(cmd)}\n{'─'*60}\n")

        # Register task in progress bar
        task_id = None
        if progress:
            task_id = progress.add_task(
                f"[yellow]yt-dlp {label}",
                total=100.0,
                speed="...",
                size="?/?",
            )

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        if self.download_id:
            download_tracker.register_process(self.download_id, proc)

        last_percent  = 0.0
        last_size     = "?/?"
        last_speed    = "?"
        downloaded_path: Optional[Path] = None

        with proc:
            for line in proc.stdout:
                log_file.write(line)

                if self.download_id and download_tracker.is_stopped(self.download_id):
                    proc.terminate()
                    break

                # Capture the actual output filename from yt-dlp
                dest_m = DEST_RE.search(line)
                if dest_m:
                    downloaded_path = Path(dest_m.group(1).strip())

                # [download]  45.2% of  931.23MiB at  5.20MiB/s ETA 02:45
                m = YTDLP_PROGRESS_RE.search(line)
                if not m:
                    continue

                try:
                    last_percent = float(m.group(1))
                except (TypeError, ValueError):
                    pass

                if m.group(2):
                    last_size  = f"?/{m.group(2).strip()}"
                if m.group(3):
                    last_speed = m.group(3).strip()

                if task_id is not None and progress:
                    progress.update(
                        task_id,
                        completed = last_percent,
                        speed     = last_speed,
                        size      = last_size,
                    )

                if self.download_id:
                    download_tracker.update_progress(
                        self.download_id, fmt_id,
                        last_percent, last_speed, last_size, None,
                    )

        # Mark complete
        if task_id is not None and progress:
            progress.update(task_id, completed=100.0, speed="done", size=last_size)

        # Verify file actually exists
        if downloaded_path and downloaded_path.exists():
            return downloaded_path

        # Fallback: search output dir for any file matching the fmt_id download pattern
        # (yt-dlp may not print Destination for already-existing files)
        stem = Path(output_template).stem.replace("%(ext)s", "")
        for f in self.output_dir.iterdir():
            if f.is_file() and f.stem.startswith(stem.rstrip(".")):
                return f

        return None

    def _download_subtitle(self, sub: StreamInfo, log_file):
        """
        Download a subtitle stream.
        Handles both direct VTT/SRT URLs and HLS m3u8 subtitle playlists.
        Always saves as .srt.
        """
        lang = (sub.language or "unknown").strip()
        urls: List[Dict] = getattr(sub, "_subtitle_urls", [])
        if not urls:
            console.print(f"[yellow]Subtitle {lang}: no URL available[/yellow]")
            return

        # Prefer srt > vtt > ttml > first available
        ext_priority = ["srt", "vtt", "ttml", "dfxp", "ass"]
        chosen = None
        for ext in ext_priority:
            chosen = next((u for u in urls if u.get("ext") == ext), None)
            if chosen:
                break
        if not chosen:
            chosen = urls[0]

        url      = chosen.get("url", "")
        src_ext  = chosen.get("ext", "vtt")
        protocol = chosen.get("protocol", "")
        out_path = self.output_dir / f"{self.filename}.{lang}.srt"

        log_file.write(f"\n[Subtitle {lang}] URL: {url}\n")

        try:
            is_m3u8 = (
                url.lower().endswith(".m3u8")
                or "m3u8" in protocol.lower()
                or src_ext == "m3u8"
            )

            if is_m3u8:
                console.print(f"[dim]Subtitle [cyan]{lang}[/cyan] → resolving m3u8 playlist…[/dim]")
                vtt_content = _resolve_m3u8_subtitle(url, self._http_headers())
                srt_content = _vtt_to_srt(vtt_content)
            else:
                raw_bytes = _fetch_url(url, self._http_headers())
                raw_text  = raw_bytes.decode("utf-8", errors="replace")

                # Detect if it's actually an m3u8 payload
                if raw_text.lstrip().startswith("#EXTM3U"):
                    console.print(f"[dim]Subtitle [cyan]{lang}[/cyan] → payload is m3u8, resolving…[/dim]")
                    vtt_content = _resolve_m3u8_subtitle(url, self._http_headers())
                    srt_content = _vtt_to_srt(vtt_content)
                elif src_ext in ("vtt", "webvtt"):
                    srt_content = _vtt_to_srt(raw_text)
                elif src_ext == "srt":
                    srt_content = raw_text
                else:
                    srt_content = _vtt_to_srt(raw_text) if " --> " in raw_text else raw_text

            out_path.write_text(srt_content, encoding="utf-8")
            log_file.write(f"[Subtitle {lang}] saved → {out_path.name}\n")
            console.print(f"[dim cyan]Subtitle → {out_path.name}[/dim cyan]")

            if self.download_id:
                download_tracker.update_progress(
                    self.download_id, f"subtitle_{lang}",
                    100.0, "N/A", f"{out_path.stat().st_size}B/...", None,
                )

        except Exception as e:
            console.print(f"[red]Subtitle {lang} failed: {e}[/red]")
            log_file.write(f"[Subtitle {lang}] ERROR: {e}\n")

    def _get_download_status(self) -> Dict[str, Any]:
        """
        Scan output_dir for downloaded files and return a status dict
        identical in structure to the N_m3u8DL-RE wrapper.
        """
        status: Dict[str, Any] = {
            "video": None,
            "audios": [],
            "subtitles": [],
            "external_subtitles": [],
        }

        video_exts = {".mp4", ".mkv", ".webm", ".mov", ".ts", ".m4v"}
        audio_exts = {".m4a", ".aac", ".mp3", ".opus", ".ogg", ".wav"}
        subtitle_exts = {".srt", ".vtt", ".ass", ".ttml", ".xml"}

        for f in sorted(self.output_dir.iterdir()):
            if not f.is_file():
                continue
            if not f.stem.lower().startswith(self.filename.lower()):
                continue

            suf  = f.suffix.lower()
            stem = f.stem

            if suf in video_exts:
                # Only the first video file (bare name = merged or video-only)
                extra = stem[len(self.filename):].lstrip(".-")
                if status["video"] is None and not extra:
                    status["video"] = {"path": str(f), "size": f.stat().st_size}

            elif suf in audio_exts:
                # Skip if it's the video file somehow
                if status["video"] and Path(status["video"]["path"]).name == f.name:
                    continue
                lang = self._lang_from_stem(stem)
                status["audios"].append({
                    "path": str(f), "name": lang, "size": f.stat().st_size,
                })

            elif suf in subtitle_exts:
                lang = self._lang_from_stem(stem)
                status["subtitles"].append({
                    "path": str(f), "language": lang, "name": lang, "size": f.stat().st_size,
                })

        return status

    def _lang_from_stem(self, stem: str) -> str:
        """Extract language/track tag inserted after the base filename."""
        base  = self.filename
        extra = stem[len(base):].lstrip(".-") if stem.lower().startswith(base.lower()) else stem
        parts = extra.split(".")
        return parts[0] if parts and parts[0] else extra

    def set_key(self, key):
        if isinstance(key, KeysManager):
            self.key = key.get_keys_list()
        else:
            self.key = key
        self.decryptor = Decryptor(preference=self.decrypt_preference)

    def get_status(self) -> Dict[str, Any]:
        return self.status or self._get_download_status()

    def get_metadata(self):
        """N_m3u8DL-RE compat stub."""
        return None, None, None, None, None