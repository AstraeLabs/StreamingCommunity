# Direct download yt-dlp adapter
# Isolated copy - NOT shared with Generic_Downloader or providers
# Changes here do NOT affect provider downloaders

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from rich.console import Console

from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.setup.binary_paths import binary_paths
from VibraVid.tui.i18n import t
from VibraVid.utils import config_manager

if os.name == "nt":
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

console = Console(legacy_windows=False, emoji=False)


def _speed_to_bits_per_second(raw_speed: str | None) -> str:
    """Convert speed strings to bits per second for a human-readable bit-based display."""
    if not raw_speed:
        return "0 bit/s"
    text = str(raw_speed).strip()
    if text.lower() in {"done", "failed", "complete"}:
        return text
    m = re.search(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[KMGTP]?i?B|[KMGTP]?B|B|[KMGTP]?i|[KMGTP]?)", text, flags=re.IGNORECASE)
    if not m:
        return text
    value = float(m.group("value"))
    unit = (m.group("unit") or "B").upper()
    base = 1024 if "I" in unit or unit.endswith("IB") else 1000
    multiplier = {
        "B": 1,
        "KB": 1000,
        "MB": 1000 ** 2,
        "GB": 1000 ** 3,
        "TB": 1000 ** 4,
        "KIB": 1024,
        "MIB": 1024 ** 2,
        "GIB": 1024 ** 3,
        "TIB": 1024 ** 4,
        "K": 1000,
        "M": 1000 ** 2,
        "G": 1000 ** 3,
        "T": 1000 ** 4,
        "KI": 1024,
        "MI": 1024 ** 2,
        "GI": 1024 ** 3,
        "TI": 1024 ** 4,
    }.get(unit, 1)
    bits = value * multiplier * 8
    for label, limit in (("Gbit/s", 1_000_000_000), ("Mbit/s", 1_000_000), ("Kbit/s", 1_000), ("bit/s", 1)):
        if bits >= limit:
            return f"{bits / limit:.2f} {label}" if bits >= limit else f"{bits:.2f} {label}"
    return f"{bits:.2f} bit/s"


def _is_generic_filename_stem(stem: str | None) -> bool:
    """Return True for placeholder stems such as watch/download/video which should not be kept as final filenames."""
    if stem is None:
        return True
    lowered = str(stem).strip().lower()
    if not lowered:
        return True
    generic_stems = {
        "download",
        "video",
        "audio",
        "file",
        "watch",
        "watchvideo",
        "index",
        "live",
    }
    return lowered in generic_stems or lowered.startswith("watch") or lowered.isdigit()


def _build_output_template(out_dir_path: Path, filename_base: str) -> str:
    """Prefer yt-dlp's original title when the caller didn't provide a meaningful name.

    This avoids ugly generic stems like watch, video, download, or YouTube-style IDs and keeps
    the final filename close to the source title whenever yt-dlp can extract it.
    """
    stem = (filename_base or "").strip()
    if _is_generic_filename_stem(stem):
        return str(out_dir_path / "%(title)s.%(ext)s")
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", stem):
        return str(out_dir_path / "%(title)s.%(ext)s")
    safe_stem = re.sub(r'[<>:"/|?*]', "_", stem)
    return str(out_dir_path / f"{safe_stem}.%(ext)s")


def _find_existing_direct_download(out_dir_path: Path, filename_base: str, url: str) -> Path | None:
    """Return an existing file in the target folder when it matches the direct download name or source URL."""
    stems: list[str] = []
    for candidate in (filename_base, Path(str(url).split("?", 1)[0]).stem if str(url).strip() else ""):
        cleaned = re.sub(r'[<>:"/|?*]', "_", (candidate or "").strip())
        if cleaned and not _is_generic_filename_stem(cleaned):
            stems.append(cleaned)

    seen: set[str] = set()
    for stem in stems:
        if stem not in seen:
            seen.add(stem)
    if not seen:
        return None

    for stem in sorted(seen):
        for pattern in (f"{stem}.*", f"{stem}.part", f"{stem}.webm", f"{stem}.mp4", f"{stem}.mkv", f"{stem}.mp3", f"{stem}.m4a"):
            matches = list(out_dir_path.glob(pattern))
            for match in matches:
                if match.is_file():
                    return match
    return None


def _resolve_direct_download_root() -> Path:
    """Return the app-local folder used for yt-dlp direct downloads, under the configured OUTPUT root and a MyDownload subfolder."""
    try:
        root_value = config_manager.config.get("OUTPUT", "root_path", default="Video")
    except Exception:
        root_value = "Video"

    root_path = Path(str(root_value).strip() or "Video")
    if not root_path.is_absolute():
        root_path = (Path.cwd() / root_path).resolve()

    my_download_dir = root_path / "MyDownload"
    my_download_dir.mkdir(parents=True, exist_ok=True)
    return my_download_dir


def _infer_stream_kind(line: str) -> str | None:
    """Infer whether a yt-dlp line refers to the video or audio stream."""
    low = line.lower()
    if "audio" in low and "video" not in low:
        return "audio"
    if "video" in low and "audio" not in low:
        return "video"
    if "+audio" in low or "audio-" in low:
        return "audio"
    if "+video" in low or "video-" in low:
        return "video"
    return None


def _parse_yt_dlp_progress(line: str) -> tuple[float, str | None, str | None] | None:
    """Extract percentage + total size + speed from yt-dlp live progress lines.

    Keep this close to the standard yt-dlp progress output so direct downloads behave the
    same way as the regular YouTube flow. The parser accepts the canonical format and a few
    Twitch/live-stream variants that keep a percentage plus a size/speed annotation.
    """
    low = line.lower()
    if "[download]" not in low and "%" not in low:
        return None

    patterns = [
        r"\[download\]\s*(?P<pct>\d+(?:\.\d+)?)%\s+of\s+(?:~\s*)?(?P<total>[^\s]+(?:\s+[A-Za-z]+)?)\s+at\s+(?P<speed>[^\s]+(?:/s)?)",
        r"\[download\]\s*(?P<pct>\d+(?:\.\d+)?)%\s+(?:of\s+)?(?:~\s*)?(?P<total>[^\s]+(?:\s+[A-Za-z]+)?)?\s*(?:at|eta)\s+(?P<speed>[^\s]+(?:/s)?)",
        r"\[download\]\s*(?P<pct>\d+(?:\.\d+)?)%\s+at\s+(?P<speed>[^\s]+(?:/s)?)",
        r"\[download\]\s*(?P<pct>\d+(?:\.\d+)?)%\s+of\s+(?:~\s*)?(?P<total>[^\s]+(?:\s+[A-Za-z]+)?)\s*(?:,|\s)\s*(?:eta|at)\s+(?P<speed>[^\s]+(?:/s)?)",
        r"(?P<pct>\d+(?:\.\d+)?)%\s+of\s+(?:~\s*)?(?P<total>[^\s]+(?:\s+[A-Za-z]+)?)\s+at\s+(?P<speed>[^\s]+(?:/s)?)",
        r"(?P<pct>\d+(?:\.\d+)?)%\s+at\s+(?P<speed>[^\s]+(?:/s)?)",
    ]

    for pattern in patterns:
        match = re.search(pattern, line, flags=re.IGNORECASE)
        if not match:
            continue

        pct_text = match.groupdict().get("pct")
        total = match.groupdict().get("total")
        speed = (match.groupdict().get("speed") or "").strip()

        if pct_text is None:
            return None

        pct = float(pct_text)
        total = total.strip() if total else None
        if total:
            total = re.sub(r"\s+\b(?:at|eta)\b$", "", total, flags=re.IGNORECASE).strip()
        return pct, total, speed or None

    return None


def _normalize_format_selector(value: str | None) -> str | None:
    """Normalize user-friendly selectors like '1080p', '720p', 'mp4', or 'webm' into valid yt-dlp format strings."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lower = text.lower()

    if lower in {"best", "worst", "bestaudio", "bestvideo"}:
        if lower == "best":
            return "bestvideo+bestaudio/best"
        if lower == "worst":
            return "worstvideo+worstaudio/worst"
        return text
    if re.fullmatch(r"(?:best|worst)(?:\[ext=(?:mp4|webm|mkv|m4a|mp3|flv|avi)\])?", lower):
        return text

    tokens = [t.strip().lower() for t in re.split(r"[\s+/]+", text) if t.strip()]
    if not tokens:
        return None

    quality = None
    ext = None
    for token in tokens:
        if quality is None:
            m = re.fullmatch(r"(?:(\d{1,4})p|(?:(\d{1,4})k)|((?:u?hd)|(?:fhd)|(?:qhd)|(?:sd)))", token)
            if m:
                quality = token
                continue
        if ext is None and re.fullmatch(r"(?:mp4|webm|mkv|m4a|mp3|flv|avi)", token):
            ext = token

    def quality_to_height(value: str) -> int | None:
        if not value:
            return None
        value_lower = value.lower().strip()
        quality_aliases = {
            "144p": 144,
            "240p": 240,
            "360p": 360,
            "480p": 480,
            "720p": 720,
            "1080p": 1080,
            "1440p": 1440,
            "2160p": 2160,
            "4320p": 4320,
            "8k": 4320,
            "4k": 2160,
            "2k": 1080,
            "sd": 480,
            "hd": 720,
            "fhd": 1080,
            "qhd": 1440,
            "uhd": 2160,
        }
        if value_lower in quality_aliases:
            return quality_aliases[value_lower]
        if value_lower.endswith("p"):
            match = re.search(r"(\d{1,4})", value_lower)
            if match:
                return int(match.group(1))
        if value_lower.endswith("k"):
            match = re.search(r"(\d{1,4})", value_lower)
            if match:
                k_value = int(match.group(1))
                if k_value <= 2:
                    return 1080
                if k_value <= 4:
                    return 2160
                if k_value <= 8:
                    return 4320
                return 2160
        match = re.search(r"(\d{1,4})", value_lower)
        if match:
            return int(match.group(1))
        return None

    if quality is not None and ext is not None:
        height = quality_to_height(quality)
        if height is not None:
            return f"best[height={height}][ext={ext}]"

    if quality is not None:
        height = quality_to_height(quality)
        if height is not None:
            return f"best[height={height}]"

    if ext is not None:
        return f"best[ext={ext}]"

    if re.fullmatch(r"(?:mp4|webm|mkv|m4a|mp3|flv|avi)", lower):
        return f"best[ext={lower}]"

    return text


def try_download_with_ytdlp_direct(
    url: str,
    out_dir: str,
    filename_base: str,
    source: Dict[str, Any] | None = None,
    download_id: str | None = None,
    site_name: str | None = None,
    timeout: int = 300,
    stream_output: bool = False,
    browser_cookies: str | None = None,
) -> Dict[str, Any]:
    """Attempt to download `url` using the system `yt-dlp` CLI.

    Isolated version for direct downloads - does not share state with providers.

    Returns:
        { 'used': bool, 'path': str|None, 'error': str|None }
    """
    try:
        use_flag = config_manager.config.get_bool("DOWNLOAD", "use_ytdlp")
    except Exception:
        use_flag = True
    if not use_flag:
        return {"used": False, "path": None, "error": "yt-dlp disabled in config"}

    out_dir_path = Path(out_dir) if str(out_dir).strip() else _resolve_direct_download_root()
    if out_dir_path.name.lower() != "mydownload":
        out_dir_path = _resolve_direct_download_root()
    out_dir_path.mkdir(parents=True, exist_ok=True)
    existing_file = _find_existing_direct_download(out_dir_path, str(filename_base or ""), str(url))
    if existing_file is not None:
        console.print(f"[yellow]{t('file_already_exists', default='file già esistente.')}[/yellow]")
        return {"used": True, "path": str(existing_file), "error": "file già esistente"}

    out_template = _build_output_template(out_dir_path, filename_base)

    try:
        ytdlp_config_path = config_manager.config.get("DOWNLOAD", "ytdlp_path", str, default=None)
    except Exception:
        ytdlp_config_path = None

    ytdlp_exec = None
    if ytdlp_config_path:
        cand = ytdlp_config_path if os.path.isabs(ytdlp_config_path) else os.path.join(os.getcwd(), ytdlp_config_path)
        if os.path.isfile(cand):
            ytdlp_exec = cand

    if not ytdlp_exec:
        try:
            cand = binary_paths.get_binary_path("yt-dlp", "yt-dlp.exe" if os.name == "nt" else "yt-dlp")
        except Exception:
            cand = None
        if cand and os.path.isfile(cand):
            ytdlp_exec = cand

    if not ytdlp_exec:
        ytdlp_exec = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe") or shutil.which("yt_dlp") or "yt-dlp"

    direct_download_quality = config_manager.config.get("DOWNLOAD", "direct_download_quality", default="best")
    normalized_format = _normalize_format_selector(direct_download_quality)
    cmd = [ytdlp_exec, "--no-warnings"]
    is_youtube = "youtube.com" in url.lower() or "youtu.be" in url.lower()
    lower_url = url.lower()
    has_direct_media_extension = any(
        lower_url.endswith(ext)
        for ext in (
            ".mp4",
            ".mkv",
            ".webm",
            ".m4a",
            ".mp3",
            ".ts",
            ".m3u8",
            ".mpd",
            ".ism",
            ".m3u",
        )
    )
    if normalized_format and (is_youtube or not has_direct_media_extension):
        cmd.extend(["-f", normalized_format])
    elif not is_youtube and has_direct_media_extension:
        pass
    else:
        cmd.extend(["-f", "best"])
    cmd.extend(["-o", out_template, url])
    if stream_output:
        cmd.insert(1, "--newline")

    env = os.environ.copy()
    binary_dir = Path(binary_paths.get_binary_directory())
    tools_dir = binary_dir / "tools"
    if tools_dir.exists():
        env["PATH"] = str(tools_dir) + os.pathsep + env.get("PATH", "")
        cmd.extend(["--ffmpeg-location", str(tools_dir)])

    js_runtime_dir = None
    js_runtime_name = "deno"
    for js_runtime_candidate in (
        os.path.join(binary_dir, "tools", "deno.exe"),
        os.path.join(binary_dir, "deno.exe"),
        os.path.join(binary_dir, "tools", "deno"),
        shutil.which("deno") or "",
    ):
        if js_runtime_candidate and os.path.exists(js_runtime_candidate):
            js_runtime_dir = str(Path(js_runtime_candidate).parent)
            break
    if js_runtime_dir:
        env["PATH"] = js_runtime_dir + os.pathsep + env.get("PATH", "")
        cmd.insert(1, "--js-runtimes")
        cmd.insert(2, js_runtime_name)

    if is_youtube:
        if browser_cookies:
            cmd.extend(["--cookies-from-browser", browser_cookies])

    headers = source.get("headers") if isinstance(source, dict) else None
    if headers:
        for k, v in headers.items():
            if not k:
                continue
            cmd.extend(["--add-header", f"{k}: {v}"])

    cookies = source.get("cookies") if isinstance(source, dict) else None
    if not is_youtube:
        if cookies:
            parts = [f"{k}={v}" for k, v in cookies.items()]
            cookie_header = "; ".join(parts)
            if cookie_header:
                cmd.extend(["--add-header", f"Cookie: {cookie_header}"])

    if source and source.get("no_playlist"):
        cmd.insert(1, "--no-playlist")

    if not is_youtube:
        cookie_paths = [
            Path(r"C:\binary\cookies.txt"),
            Path("C:/binary/cookies.txt"),
            Path("cookies.txt"),
        ]
        cookie_path = next((p for p in cookie_paths if p.is_file()), None)
        if cookie_path:
            cmd.extend(["--cookies", str(cookie_path)])

    if stream_output:
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            captured: list[str] = []
            destination_printed = False
            console.print()
            try:
                from VibraVid.tui.i18n import t as i18n_t
                console.print(f"[yellow]{i18n_t('destination_label', default='Destination:')}[/yellow] {out_dir_path}")
            except Exception:
                console.print(f"[yellow]Destination:[/yellow] {out_dir_path}")
            console.print()
            progress_mgr = DownloadBarManager()
            progress_mgr.__enter__()
            try:
                task_id = None

                def ensure_task():
                    nonlocal task_id
                    if task_id is None:
                        task_id = progress_mgr.progress.add_task("download", total=100, size="", speed="0 bit/s")
                    return task_id

                for raw in proc.stdout or []:
                    line = raw.replace("\r", "\n").rstrip("\n")
                    captured.append(line)
                    low = line.lower()
                    if not line.strip():
                        continue

                    if low.startswith("destination:") and not destination_printed:
                        destination = line.split(":", 1)[1].strip()
                        try:
                            from VibraVid.tui.i18n import t as i18n_t
                            console.print(f"[yellow]{i18n_t('destination_label', default='Destination:')}[/yellow] {destination}")
                        except Exception:
                            console.print(f"[yellow]Destination:[/yellow] {destination}")
                        destination_printed = True
                        continue

                    parsed = _parse_yt_dlp_progress(line)
                    if parsed is None:
                        if any(
                            x in low
                            for x in (
                                "unsupported url",
                                "unable to download",
                                "http error",
                                "error:",
                                "error ",
                            )
                        ):
                            console.print(f"[red]{line}[/red]")
                        continue

                    target_task_id = ensure_task()
                    pct, total, speed = parsed
                    progress_mgr.progress.update(
                        target_task_id,
                        completed=min(max(float(pct), 0.0), 100.0),
                        size=total,
                        speed=_speed_to_bits_per_second(speed) if speed else "0 bit/s",
                    )

                proc.wait(timeout)
                if proc.returncode == 0:
                    progress_mgr.progress.stop()
                else:
                    try:
                        from VibraVid.tui.i18n import t as i18n_t
                        final_status = i18n_t("download_status_complete", default="complete") if proc.returncode == 0 else i18n_t("download_status_error", default="error")
                        final_speed = i18n_t("download_status_done", default="done") if proc.returncode == 0 else i18n_t("download_status_failed", default="failed")
                    except Exception:
                        final_status = "complete" if proc.returncode == 0 else "error"
                        final_speed = "done" if proc.returncode == 0 else "failed"
                    if task_id is not None:
                        progress_mgr.progress.update(
                            task_id,
                            description="download",
                            completed=100.0,
                            size=final_status,
                            speed=final_speed,
                        )
                console.print()
            finally:
                try:
                    progress_mgr.progress.stop()
                except Exception:
                    pass

            if proc.returncode != 0:
                return {"used": True, "path": None, "error": "\n".join(captured) if captured else f"yt-dlp exit {proc.returncode}"}

            try:
                files = [p for p in out_dir_path.iterdir() if p.is_file()]
                filtered_files = [p for p in files if not _is_generic_filename_stem(p.stem)]
                exact_candidates = list(out_dir_path.glob(filename_base + ".*")) if not _is_generic_filename_stem(filename_base) else []
                candidates = exact_candidates or filtered_files or files
                if not candidates:
                    return {"used": True, "path": None, "error": "no output file found after yt-dlp"}

                for ext in (".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".ts"):
                    for candidate in candidates:
                        if candidate.suffix.lower() == ext:
                            return {"used": True, "path": str(candidate), "error": None}

                newest = max(candidates, key=lambda p: p.stat().st_mtime)
                return {"used": True, "path": str(newest), "error": None}
            except Exception as exc:
                return {"used": True, "path": None, "error": f"post-processing error: {exc}"}
        except FileNotFoundError:
            return {"used": False, "path": None, "error": "yt-dlp not found"}
        except subprocess.TimeoutExpired:
            return {"used": True, "path": None, "error": "yt-dlp timeout"}
        except Exception as exc:
            return {"used": True, "path": None, "error": f"yt-dlp error: {exc}"}

    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return {"used": False, "path": None, "error": "yt-dlp not found"}
    except subprocess.TimeoutExpired:
        return {"used": True, "path": None, "error": "yt-dlp timeout"}
    except Exception as exc:
        return {"used": True, "path": None, "error": f"yt-dlp error: {exc}"}

    if proc.returncode != 0:
        return {"used": True, "path": None, "error": proc.stdout or f"yt-dlp exit {proc.returncode}"}

    try:
        files = [p for p in out_dir_path.iterdir() if p.is_file()]
        filtered_files = [p for p in files if not _is_generic_filename_stem(p.stem)]
        exact_candidates = list(out_dir_path.glob(filename_base + ".*")) if not _is_generic_filename_stem(filename_base) else []
        candidates = exact_candidates or filtered_files or files
        if not candidates:
            return {"used": True, "path": None, "error": "no output file found after yt-dlp"}

        for ext in (".mp4", ".mkv", ".webm", ".mp3", ".m4a", ".ts"):
            for candidate in candidates:
                if candidate.suffix.lower() == ext:
                    return {"used": True, "path": str(candidate), "error": None}

        newest = max(candidates, key=lambda p: p.stat().st_mtime)
        return {"used": True, "path": str(newest), "error": None}
    except Exception as exc:
        return {"used": True, "path": None, "error": f"post-processing error: {exc}"}
