# 18.03.26

from __future__ import annotations

import asyncio
import logging
import platform
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from rich.console import Console

from VibraVid.setup import get_ffmpeg_path, get_n_m3u8dl_re_path
from VibraVid.utils import config_manager
from VibraVid.core.manifest.m3u8 import HLSParser
from VibraVid.core.manifest.mpd import DashParser
from VibraVid.core.manifest.stream import Stream
from VibraVid.utils.tmdb_client import tmdb_client
from VibraVid.utils.http_client import create_client, get_headers

from VibraVid.source.style.tracker import download_tracker
from VibraVid.source.style.bar_manager import DownloadBarManager
from VibraVid.source.utils.selector import StreamSelector, N3u8dlFormatter
from VibraVid.source.style.ui import build_table
from VibraVid.source.utils.language import resolve_locale, LANGUAGE_MAP
from VibraVid.source.utils.stream_selector_ui import InteractiveStreamSelector
from VibraVid.core.downloader.subtitle import download_external_tracks_with_progress, build_ext_track_label, is_valid_format, ext_from_url
from VibraVid.source.utils.codec import VIDEO_EXTENSIONS, AUDIO_EXTENSIONS
from VibraVid.source.utils.decrypt import Decryptor, KeysManager


console = Console(force_terminal=True if platform.system().lower() != "windows" else None)
logger  = logging.getLogger("n3u8dl_re")
auto_select = config_manager.config.get_bool("DOWNLOAD", "auto_select")
CONCURRENT_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "concurrent_download")
THREAD_COUNT = config_manager.config.get_int("DOWNLOAD", "thread_count")
RETRY_COUNT = config_manager.config.get_int("REQUESTS", "max_retry")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")
MAX_SPEED = config_manager.config.get("DOWNLOAD", "max_speed")
USE_PROXY = config_manager.config.get_bool("REQUESTS", "use_proxy")
PROXY_CFG = config_manager.config.get_dict("REQUESTS", "proxy")

_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
_SPEED_RE = re.compile(r"(\d+(?:\.\d+)?(?:MB|KB|GB|B)ps)")
_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?(?:MB|GB|KB|B))/(\d+(?:\.\d+)?(?:MB|GB|KB|B))")
_SEG_RE = re.compile(r"(\d+)/(\d+)")
_VID_RES_RE = re.compile(r"Vid\s+(\d+x\d+)")
_AUD_PROG_RE = re.compile(r"Aud\s+(.+?)\s*\|\s*([\w-]+)(?:\s{3,}|\s*-{5,}|$)")
_SUB_PROG_RE = re.compile(r"Sub\s+([\w-]+)\s*\|\s*(.+?)(?:\s{3,}|\s*-{5,}|$)")
_SUBFIN_RE = re.compile(r"(\d+\.?\d*(?:B|KB|MB|GB))\s+-\s+00:00:00")


def _resolve_subtitle_url_sync(url: str, headers: Dict) -> Tuple[str, str]:
    """Synchronously probe *url* to determine the real subtitle format.

    If the response is an HLS manifest (``#EXTM3U``), the first media segment
    URL is extracted and its extension is used.  Returns ``(final_url, ext)``
    where *ext* may be an empty string if nothing recognisable was found.
    """
    try:
        hdrs = dict(headers)
        hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))

        logger.info(f"Resolving subtitle URL synchronously: {url!r}")
        with create_client(headers=hdrs, timeout=15, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            text = resp.text.strip()
    except Exception as exc:
        logger.info(f"_resolve_subtitle_url_sync: request failed for {url!r}: {exc}")
        return url, ext_from_url(url, "")

    if text.startswith("#EXTM3U"):
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                resolved_ext = ext_from_url(line, "")
                logger.info(f"Resolved HLS subtitle manifest → segment {line!r} (ext={resolved_ext!r})")
                return line, resolved_ext
        logger.info(f"_resolve_subtitle_url_sync: manifest at {url!r} had no segments")
        return url, ""

    content_type = resp.headers.get("content-type", "").lower()
    for mime, ext in (("vtt", "vtt"), ("webvtt", "vtt"), ("srt", "srt"), ("ttml", "ttml"), ("xml", "xml"), ("dfxp", "dfxp")):
        if mime in content_type:
            return url, ext
    return url, ext_from_url(url, "")


def _lang_variants(normalized_lang: str) -> Set[str]:
    """Return every LANGUAGE_MAP key that resolves to *normalized_lang*."""
    if not normalized_lang:
        return set()
    variants: Set[str] = {normalized_lang, normalized_lang.lower()}
    variants.add(normalized_lang.split("-")[0].lower())
    for key, value in LANGUAGE_MAP.items():
        if value == normalized_lang or value == normalized_lang.lower():
            variants.add(key)
            variants.add(key.lower())
    return variants


class MediaDownloader:
    def __init__(self, url: str, output_dir: str, filename: str, headers: Optional[Dict] = None, key: Optional[Any] = None, cookies: Optional[Dict] = None, decrypt_preference: str = "shaka", download_id: Optional[str]  = None, site_name: Optional[str] = None):
        self.url = url
        self.output_dir = Path(output_dir)
        self.filename = filename
        self.headers = headers or {}
        self.key = key
        self.cookies = cookies or {}
        self.decrypt_preference = decrypt_preference.strip().lower()
        self.download_id = download_id
        self.site_name = site_name

        self.streams: List[Stream]   = []
        self.manifest_type: str            = "Unknown"
        self.raw_m3u8: Optional[Path] = None
        self.raw_mpd: Optional[Path] = None
        self.status: Optional[dict] = None

        self._sv: str = "best"
        self._sa: str = "best"
        self._ss: str = "all"

        self.external_subtitles: list          = []
        self.external_audios: list          = []
        self.custom_filters: Optional[Dict] = None
        self.license_url: Optional[str]  = None
        self.drm_type: Optional[str]  = None

        # Progress-bar label tables — built in _prepare_labels()
        self._video_label: str = ""
        self._video_task_key:  str = "vid_main"
        self._has_video: bool = True
        self._audio_labels: Dict[str, str] = {}
        self._audio_task_keys: List[Tuple[str, str]] = []
        self._sub_labels: Dict[str, str] = {}

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_dir = self.output_dir / f"{self.filename}_tmp"
        self._tmp_dir.mkdir(exist_ok=True)

        if self.download_id:
            _output_type = (
                "Movie" if config_manager.config.get("OUTPUT", "movie_folder_name") in str(self.output_dir)
                else "TV"    if config_manager.config.get("OUTPUT", "serie_folder_name")  in str(self.output_dir)
                else "Anime" if config_manager.config.get("OUTPUT", "anime_folder_name")  in str(self.output_dir)
                else "other"
            )
            download_tracker.start_download(self.download_id, self.filename, self.site_name or "Unknown", _output_type)

    def set_key(self, key: Any) -> None:
        self.key = key.get_keys_list() if isinstance(key, KeysManager) else key

    def parse_stream(self, show_table: bool = True) -> List[Stream]:
        """Fetch manifest → parse streams → apply selection → print table."""
        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing …")

        url_lower = self.url.lower().split("?")[0]
        parser = DashParser(self.url, self.headers) if url_lower.endswith(".mpd") else HLSParser(self.url, self.headers)

        if not parser.fetch_manifest():
            logger.error("MediaDownloader: manifest fetch failed")
            return []

        if isinstance(parser, DashParser):
            self.raw_mpd = parser.save_raw(self._tmp_dir)
            self.manifest_type = "DASH"
        else:
            self.raw_m3u8 = parser.save_raw(self._tmp_dir)
            self.manifest_type = "HLS"

        self.streams = [s for s in parser.parse_streams() if s.type != "image"]
        
        # Check if auto_select is enabled
        if auto_select:
            self._apply_selection()
        else:
            selector = InteractiveStreamSelector(self.streams, window_size=15)
            selector.run()

        for ext in self.external_subtitles:
            lang = ext.get("language", "")
            selected = self._ext_track_matches(ext, "subtitle")
            ext["_selected"] = selected
            fake = Stream(type="subtitle", language=lang, name=ext.get("name", ""), selected=selected, is_external=True)
            fake.id = "EXT"
            self.streams.append(fake)

        for ext in self.external_audios:
            lang = ext.get("language", "")
            selected = self._ext_lang_matches(lang, "audio")
            ext["_selected"] = selected
            fake = Stream(type="audio", language=lang, name=ext.get("name", ""), selected=selected, is_external=True)
            fake.id = "EXT"
            self.streams.append(fake)

        if show_table and self.streams:
            console.print(build_table(self.streams))

        return self.streams

    parser_stream = parse_stream
    def get_metadata(self) -> Tuple[str, str, str]:
        return (str(self.raw_m3u8), str(self.raw_mpd), "")

    def start_download(self) -> Dict[str, Any]:
        """Build command → run N_m3u8DL-RE → download externals → return status dict."""
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading …")

        # Move HLS subtitle streams to external download list
        new_ext_subs: List[Dict] = []
        for s in self.streams:
            if s.type == "subtitle" and s.selected and not s.is_external:
                sub_url = s.playlist_url or (s.segments[0].url if s.segments else None)
                if not sub_url:
                    continue

                ext = ext_from_url(sub_url, "")
                if not ext or not is_valid_format(ext, "subtitle"):
                    resolved_url, ext = _resolve_subtitle_url_sync(sub_url, self.headers)
                    if not ext or not is_valid_format(ext, "subtitle"):
                        logger.info(f"Skipping external subtitle (unsupported format): {s.language} {sub_url}")
                        s.selected = False
                        continue
                    sub_url = resolved_url

                new_ext_subs.append({
                    "url":       sub_url,
                    "language":  s.language or "und",
                    "name":      s.name or "",
                    "forced":    s.forced,
                    "sdh":       s.is_sdh,
                    "cc":        s.is_cc,
                    "default":   s.default,
                    "type":      ext,
                    "_selected": True,
                })
                logger.info(f"Subtitle to download: {s.language} from {sub_url[:80]}")
                s.selected = False

        self.external_subtitles.extend(new_ext_subs)
        if new_ext_subs:
            logger.info(f"Moved {len(new_ext_subs)} subtitle(s) to external download")
        sv = self._sv or "best"
        sa = self._sa or "best"
        ss = "false"
        proxy = (PROXY_CFG.get("https") or PROXY_CFG.get("http", "")) if USE_PROXY else ""
        self._prepare_labels()

        cmd = self._build_command(sv=sv, sa=sa, ss=ss, proxy=proxy)
        logger.info(f"N_m3u8DL-RE command: {' '.join(cmd)}")

        with DownloadBarManager(self.download_id) as bar_manager:
            bar_manager.add_prebuilt_tasks(self._get_prebuilt_tasks())

            for _track, _ttype in ([(s, "subtitle") for s in self.external_subtitles if s.get("_selected", True)] + [(a, "audio")   for a in self.external_audios    if a.get("_selected", True)]):
                _label    = build_ext_track_label(_track, _ttype)
                _lang     = _track.get("language", "und")
                _task_key = f"ext_{_ttype}_{_lang}_{id(_track)}"
                _track["_task_key"] = _task_key
                _track["_label"]    = _label
                bar_manager.add_external_track_task(_label, _task_key)

            loop = asyncio.new_event_loop()
            download_result: Dict[str, Any] = {"ext_subs": [], "ext_auds": []}

            def _run_externals() -> None:
                asyncio.set_event_loop(loop)
                try:
                    ext_subs, ext_auds = loop.run_until_complete(download_external_tracks_with_progress(self.headers, self.external_subtitles, self.external_audios, self.output_dir, self.filename, bar_manager))
                    download_result["ext_subs"] = ext_subs
                    download_result["ext_auds"] = ext_auds
                except Exception as exc:
                    logger.error(f"External downloads failed: {exc}")
                finally:
                    loop.close()

            download_thread = threading.Thread(target=_run_externals, daemon=False)
            download_thread.start()

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, universal_newlines=True,
            )
            if self.download_id:
                download_tracker.register_process(self.download_id, proc)

            with proc:
                for line in proc.stdout:
                    if " : " in line:
                        logger.info(line.rstrip())
                    if self.download_id and download_tracker.is_stopped(self.download_id):
                        proc.terminate()
                        break
                    bar_manager.handle_progress_line(self._parse_progress_line(line))
                bar_manager.finish_all_tasks()

            download_thread.join(timeout=300)
            if download_thread.is_alive():
                logger.error("External download thread timed out — proceeding anyway")

            ext_subs = download_result["ext_subs"]
            ext_auds = download_result["ext_auds"]

        if self.download_id and download_tracker.is_stopped(self.download_id):
            return {"error": "cancelled"}

        self.status = self._build_status(ext_subs, ext_auds)

        if self.key:
            self._decrypt_check(self.status)

        return self.status

    def get_status(self) -> Dict:
        return self.status or self._build_status([], [])

    def _build_command(self, sv: str, sa: str, ss: str, proxy: str = "") -> List[str]:
        cmd: List[str] = [
            get_n_m3u8dl_re_path(),
            "--save-name",                self.filename,
            "--save-dir",                 str(self.output_dir),
            "--tmp-dir",                  str(self._tmp_dir),
            "--ffmpeg-binary-path",       get_ffmpeg_path(),
            "--write-meta-json",          "false",
            "--binary-merge",
            "--del-after-done",
            "--auto-subtitle-fix",        "false",
            "--check-segments-count",     "false",
            "--mp4-real-time-decryption", "false",
            "--no-log",
        ]

        cmd.extend(["--drop-video", "all"] if sv == "false" else ["--select-video", sv])
        cmd.extend(["--drop-audio", "all"] if sa == "false" else ["--select-audio", sa])
        cmd.extend(["--drop-subtitle", "all"])  # subtitles are always handled externally

        for k, v in self.headers.items():
            cmd.extend(["--header", f"{k}: {v}"])
        if self.cookies:
            cmd.extend(["--header", "Cookie: " + "; ".join(f"{k}={v}" for k, v in self.cookies.items())])

        if USE_PROXY and proxy:
            cmd.extend(["--use-system-proxy", "false", "--custom-proxy", proxy])
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
        return cmd

    def _prepare_labels(self) -> None:
        """Build Rich label strings from selected Stream objects before starting the subprocess."""
        sel_video = [s for s in self.streams if s.type == "video"    and s.selected and not s.is_external]
        sel_audio = [s for s in self.streams if s.type == "audio"    and s.selected and not s.is_external]
        sel_subs  = [s for s in self.streams if s.type == "subtitle" and s.selected and not s.is_external]

        # Video
        self._has_video = bool(sel_video)
        if sel_video:
            v = sel_video[0]
            codec = v.get_short_codec() or v.codecs or ""
            res = v.resolution or "main"
            parts = []
            if codec:
                parts.append(f"[yellow]\\[{codec}][/yellow]")
            if res:
                parts.append(f"[green]{res}[/green]")
            if v.bitrate:
                parts.append(f"[blue]{v.bitrate_display}[/blue]")
            self._video_label    = " ".join(parts)
            self._video_task_key = f"vid_{res}"
        else:
            self._video_label    = ""
            self._video_task_key = "vid_main"

        # Audio
        self._audio_labels    = {}
        self._audio_task_keys = []
        seen_normalized: Set[str] = set()

        for s in sel_audio:
            lang  = s.resolved_language or s.language or "und"
            codec = s.get_short_codec() or s.codecs or ""
            parts = []
            if codec:
                parts.append(f"[yellow]\\[{codec}][/yellow]")
            parts.append(f"[bold white]{lang}[/bold white]")
            if s.bitrate:
                parts.append(f"[blue]{s.bitrate_display}[/blue]")
            if s.default:
                parts.append("[bold red][DEFAULT][/bold red]")
            label = " ".join(parts)

            raw = (s.language or "und").lower()
            normalized = resolve_locale(raw) if raw else ""
            task_lang = normalized.split("-")[0].lower() if normalized else raw

            if task_lang in seen_normalized:
                logger.info(f"Audio {raw!r} already mapped as {task_lang!r}, skipping duplicate")
                continue
            seen_normalized.add(task_lang)

            self._audio_labels[raw] = label
            for variant in _lang_variants(normalized):
                self._audio_labels[variant] = label
            if s.id and ":" in s.id:
                self._audio_labels.setdefault(s.id.split(":")[0].lower(), label)

            self._audio_task_keys.append((task_lang, label))

        # Subtitles
        self._sub_labels = {}
        for s in sel_subs:
            label = self._sub_stream_label(s)
            raw   = (s.language or "und").lower()
            name  = (s.name or "").strip()
            if name:
                self._sub_labels[f"{raw}:{tmdb_client._slugify(name)}"] = label
            self._sub_labels.setdefault(raw, label)

        logger.info(f"Labels ready — video={self._video_label!r} audio={self._audio_labels} subs={self._sub_labels}")

    @staticmethod
    def _sub_stream_label(s: Stream) -> str:
        """Rich label for a subtitle stream (without the 'Sub' prefix)."""
        try:
            lang_raw = s.language or "und"
            sfx = re.search(r"[-_](forced|cc|sdh|hi)$", lang_raw, re.I)
            lang_sfx = sfx.group(1).lower() if sfx else ""
            forced = s.forced  or lang_sfx == "forced"
            cc = s.is_cc   or lang_sfx == "cc"
            sdh = s.is_sdh  or lang_sfx == "sdh"
            default = s.default and not forced

            lang  = s.resolved_language or lang_raw or "und"
            parts = [f"[bold white]{lang}[/bold white]"]
            flags = []
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

            ext = "vtt" if s.type == "subtitle" else "m4a"
            return f"[yellow]\\[{ext}][/yellow] {' '.join(parts)}"
        except Exception:
            return s.language or "und"

    def _get_prebuilt_tasks(self) -> List[Tuple[str, str]]:
        """Return (task_key, rich_label) in display order: video → audio."""
        tasks: List[Tuple[str, str]] = []
        if self._has_video:
            tasks.append((self._video_task_key, f"[bold cyan]Vid[/bold cyan] {self._video_label}"))
        seen: Set[str] = set()
        for lang_code, label in self._audio_task_keys:
            key = f"aud_{lang_code}"
            if key not in seen:
                seen.add(key)
                tasks.append((key, f"[bold cyan]Aud[/bold cyan] {label}"))
        return tasks

    def _parse_progress_line(self, line: str) -> Optional[Dict[str, Any]]:
        """
        Parse one N_m3u8DL-RE stdout line.
        Returns a dict with progress data, or None for listing/irrelevant lines.
        """
        line_s = line.strip()
        if not line_s:
            return None

        result: Dict[str, Any] = {}

        if line_s.startswith("Vid"):
            if not (_VID_RES_RE.search(line_s) or re.search(r"Vid\s+[\d.]+\s*[KMGT]?bps", line_s) or "Vid main" in line_s or "Vid " in line_s):
                return None
            result["_task_key"]  = self._video_task_key
            result["label"]      = f"[bold cyan]Vid[/bold cyan] {self._video_label}"
            result["_lang_code"] = ""

        elif line_s.startswith("Aud"):
            result["track"] = "audio"
            content = re.split(r'\s{3,}|\s*-{5,}', line_s[3:])[0].strip()
            parts = [p.strip().lower() for p in content.split("|")]

            lang_code = ""
            label = ""

            for p in parts:
                if p in self._audio_labels:
                    lang_code, label = p, self._audio_labels[p]
                    break

            if not label:
                m = re.search(r'\b([a-z]{2}(?:-[a-z]{2})?)\b', content, re.I)
                if m:
                    extracted = m.group(1).lower()
                    if extracted in self._audio_labels:
                        lang_code, label = extracted, self._audio_labels[extracted]

            if not label:
                m = re.search(r'(\d+(?:\.\d+)?)\s*([KMG]?bps)', content, re.I)
                if m:
                    br_str = m.group(0).lower()
                    for k, v in self._audio_labels.items():
                        if br_str in v.lower():
                            lang_code, label = k, v
                            break

            if not label:
                m = _AUD_PROG_RE.search(line_s)
                lang_code = m.group(2).strip().lower() if m else ""

            normalized = resolve_locale(lang_code) if lang_code else ""
            task_lang  = normalized.split("-")[0].lower() if normalized else lang_code
            result["label"]      = f"[bold cyan]Aud[/bold cyan] {label or f'[bold white]{content or chr(65)}[/bold white]'}"
            result["_task_key"]  = f"aud_{task_lang or 'main'}"
            result["_lang_code"] = lang_code

        elif line_s.startswith("Sub"):
            result["track"] = "subtitle"
            m = _SUB_PROG_RE.search(line_s)
            lang_code = m.group(1).strip().lower() if m else ""
            display_name = m.group(2).strip()         if m else ""

            name_slug = tmdb_client._slugify(display_name) if display_name else lang_code
            compound_key = f"{lang_code}:{name_slug}"
            base = lang_code.split("-")[0]
            label = (self._sub_labels.get(compound_key) or self._sub_labels.get(lang_code, "") or self._sub_labels.get(f"{base}:{name_slug}", "") or self._sub_labels.get(base, ""))
            result["label"]      = f"[bold cyan]Sub[/bold cyan] {label or f'[bold white]{display_name or lang_code}[/bold white]'}"
            result["_task_key"]  = f"sub_{lang_code}_{name_slug}" if name_slug else f"sub_{lang_code}"
            result["_lang_code"] = lang_code

            fm = _SUBFIN_RE.search(line_s)
            if fm:
                result["final_size"] = fm.group(1)
                result["pct"]        = 100.0
        else:
            return None

        # Numeric progress fields
        m = _PERCENT_RE.search(line_s)
        if m:
            result["pct"] = float(m.group(1))

        m = _SEG_RE.search(line_s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b > 0:
                result["segments"] = f"{a}/{b}"
                if "pct" not in result:
                    result["pct"] = (a / b) * 100.0

        m = _SIZE_RE.search(line_s)
        if m:
            result["size"] = f"{m.group(1)}/{m.group(2)}"

        m = _SPEED_RE.search(line_s)
        if m:
            result["speed"] = m.group(1)

        has_data = any(k in result for k in ("pct", "segments", "size", "speed", "final_size"))
        
        # If it's a progress line (Vid/Aud/Sub) but no numeric data was found,
        if has_data or any(k in result for k in ("_task_key", "track")):
            if not has_data:
                result["pct"] = 0.0
            return result
        
        return None

    def _apply_selection(self) -> None:
        f = self.custom_filters or {}
        v_cfg = f.get("video")    or config_manager.config.get("DOWNLOAD", "select_video")
        a_cfg = f.get("audio")    or config_manager.config.get("DOWNLOAD", "select_audio")
        s_cfg = f.get("subtitle") or config_manager.config.get("DOWNLOAD", "select_subtitle")
        selector = StreamSelector(v_cfg, a_cfg, s_cfg, formatter=N3u8dlFormatter())
        self._sv, self._sa, self._ss = selector.apply(self.streams)
        logger.info(f"Selection → video={self._sv!r}  audio={self._sa!r}  subtitle={self._ss!r}")

    def _ext_lang_matches(self, lang: str, track_type: str) -> bool:
        """Return True if the external track with the given *lang* tag should be downloaded."""
        cfg_key = "select_subtitle" if track_type == "subtitle" else "select_audio"
        cfg = config_manager.config.get("DOWNLOAD", cfg_key)
        if not cfg or cfg.lower() == "all":
            return True
        if cfg.lower() == "false":
            return False

        # Split on pipe/comma into individual tokens (e.g. "ita_forced", "eng", "it_cc")
        tokens = [t.strip().lower() for t in re.split(r"[|,]", cfg) if t.strip()]
        lang_l = lang.strip().lower()

        for token in tokens:

            # Strip flag suffixes (forced/cc/sdh/hi) to get the bare language token
            parts = token.split("_")
            base_token = parts[0]

            # Match the base language token against the track language
            # Support: "ita" matching "it-it", "it" matching "it-it", exact "it-it"
            if base_token in lang_l or lang_l.startswith(base_token):
                return True
            
            # ISO-639-2 three-letter → two-letter prefix match ("ita" → "it")
            if len(base_token) == 3 and base_token.isalpha() and lang_l.startswith(base_token[:2]):
                return True
        return False

    def _ext_track_matches(self, track: Dict, track_type: str) -> bool:
        """Return True if *track* (a full external track dict with flag fields)
        matches the configured selection filter, including flag requirements.
        """
        cfg_key = "select_subtitle" if track_type == "subtitle" else "select_audio"
        cfg = config_manager.config.get("DOWNLOAD", cfg_key)
        if not cfg or cfg.lower() == "all":
            return True
        if cfg.lower() == "false":
            return False

        lang = (track.get("language") or "").strip().lower()
        tokens = [t.strip().lower() for t in re.split(r"[|,]", cfg) if t.strip()]

        for token in tokens:
            parts = token.split("_")
            base_token = parts[0]
            req_flags  = {p for p in parts[1:] if p in {"forced", "cc", "sdh", "hi"}}
            if "hi" in req_flags:
                req_flags.discard("hi")
                req_flags.add("cc")

            # Language match
            lang_ok = (base_token in lang or lang.startswith(base_token) or (len(base_token) == 3 and base_token.isalpha() and lang.startswith(base_token[:2])))
            if not lang_ok:
                continue

            # Flag match: every requested flag must be present
            if req_flags:
                track_flags: set = set()
                if track.get("forced"):
                    track_flags.add("forced")
                if track.get("cc"):
                    track_flags.add("cc")
                if track.get("sdh"):
                    track_flags.add("sdh")
                if not req_flags.issubset(track_flags):
                    continue

            return True
        return False

    def _decrypt_check(self, status: Dict[str, Any]) -> None:
        if self.download_id:
            download_tracker.update_status(self.download_id, "Decrypting …")

        decryptor = Decryptor(preference=self.decrypt_preference, license_url=self.license_url, drm_type=self.drm_type)
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
                    try: 
                        out.unlink()
                    except Exception: 
                        pass

    def _build_status(self, ext_subs: List, ext_auds: List = None) -> Dict:
        """Scan output_dir and build the status dict."""
        status: Dict[str, Any] = {
            "video": None,
            "audios": [],
            "subtitles": ext_subs or [],
            "external_subtitles": [],
            "external_audios": ext_auds or [],
        }
        for f in sorted(self.output_dir.iterdir()):
            if not f.is_file():
                continue
            ext = f.suffix.lower()
            fname_l = self.filename.lower()

            if ext in VIDEO_EXTENSIONS and f.stem.lower() == fname_l:
                if status["video"] is None:
                    status["video"] = {"path": str(f), "size": f.stat().st_size}
                continue

            if ext in AUDIO_EXTENSIONS and f.name.lower().startswith(fname_l):
                if status["video"] and Path(status["video"]["path"]).name == f.name:
                    continue
                track_name = f.stem[len(self.filename):].lstrip(".")
                status["audios"].append({"path": str(f), "name": track_name, "size": f.stat().st_size})

        return status