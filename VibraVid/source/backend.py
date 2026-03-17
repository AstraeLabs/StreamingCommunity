# 16.03.26

"""
Pluggable downloader backend system.

Adding a new downloader
───────────────────────
1. Subclass BaseDownloaderBackend.
2. Override name, get_formatter(), build_command(), parse_progress_line().
3. Optionally override prepare_stream_labels() for rich labels.
4. Pass an instance to MediaDownloader(backend=YourBackend()).
"""

from __future__ import annotations

import re
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Set

from VibraVid.source.utils.selector import BaseFormatter
from VibraVid.core.manifest.stream import Stream
from VibraVid.source.utils.language import resolve_locale, LANGUAGE_MAP

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Abstract backend
# ─────────────────────────────────────────────────────────────────────────────

class BaseDownloaderBackend(ABC):
    """Abstract base — each subclass encapsulates one external download tool."""
    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable downloader name, e.g. 'N_m3u8DL-RE'."""

    @abstractmethod
    def get_formatter(self) -> "BaseFormatter":
        """Return the BaseFormatter for this backend's filter syntax."""

    @abstractmethod
    def build_command(self, url: str, sv: str, sa: str, ss: str,
        filename: str, output_dir: Path, tmp_dir: Path,
        headers: Dict[str, str], cookies: Dict[str, str], key: Any, *,
        concurrent: bool = False, thread_count: int = 0, timeout: int = 30, retry_count: int = 3,
        max_speed: str = "", use_proxy: bool = False, proxy: str = "", extra_args: Optional[List[str]] = None,
    ) -> List[str]:
        """Return the fully-assembled subprocess command list."""

    @abstractmethod
    def parse_progress_line(self, line: str, manifest_type: str) -> Optional[Dict[str, Any]]:
        """
        Parse a single stdout line.

        Returns a dict with any subset of:
          { track, label, _task_key, _lang_code, pct, speed, size,
            segments, codec, final_size }

        Returns None for non-progress lines (listings, info, warnings).
        Only return a dict when pct/speed/size/segments/final_size is present.
        """

    def prepare_stream_labels(self, streams: List["Stream"]) -> None:
        """
        Pre-compute progress-bar labels from the already-selected Stream objects.

        Called once before the download subprocess starts.
        Default: no-op.  N3u8dlBackend overrides this.
        """

    def get_prebuilt_tasks(self) -> List[Tuple[str, str]]:
        """
        Return (task_key, rich_label) pairs for every track this backend will download.

        Called by the wrapper to pre-create Rich progress tasks in the correct
        order (video → audio → subtitles) before the subprocess emits any output.
        Default: empty list (backends that don't support this return nothing).
        """
        return []


class N3u8dlBackend(BaseDownloaderBackend):
    """
    Backend for N_m3u8DL-RE.

    Progress line formats
    ─────────────────────
    N_m3u8DL-RE emits two kinds of lines per track:

    LISTING (during parsing, no progress data):
        Vid 1920x1080 | 4500 Kbps | avc1.640028
        Aud audio | Italian | ita              ← 3 parts: group, display, lang
        Sub subs | eng | English [CC]          ← 3 parts: group, lang, display

    PROGRESS (during download, has %, size, speed):
        Vid 1920x1080 | 4500 Kbps         ----- 33/435  99.49MB/1.51GB  24.99MBps
        Aud Italian | ita                  ----- 38/435   6.36MB/83.78MB  1.43MBps
        Sub eng | English [CC]             ----- 0/1   0.00%  0.00Bps
        Sub ita-forced | Italian [Forced]  ----- 1/1  100.00%  144.00B - 00:00:00

    Key difference: progress lines DROP the GROUP-ID prefix for Aud/Sub.
    Audio progress: "{DisplayName} | {langcode}"
    Subtitle progress: "{langcode} | {DisplayName}"

    Speed format: "24.99MBps" (no space, no "/s")  ← matched by _SPEED_RE

    Stream labels
    ─────────────
    prepare_stream_labels() is called before the subprocess.  It builds
    label strings from Stream objects:
        Video:    "1920x1080 | 4.5 Mbps | H.264"
        Audio:    "it-IT | [Default]"
        Subtitle: "en-US | [CC]"   or  "it-IT | [Forced]"
    """
    _PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
    _SPEED_RE   = re.compile(r"(\d+(?:\.\d+)?(?:MB|KB|GB|B)ps)")   # "24.99MBps"
    _SIZE_RE    = re.compile(r"(\d+(?:\.\d+)?(?:MB|GB|KB|B))/(\d+(?:\.\d+)?(?:MB|GB|KB|B))")
    _SEG_RE     = re.compile(r"(\d+)/(\d+)")
    _VID_RES_RE = re.compile(r"Vid\s+(\d+x\d+)")
    _AUD_PROG_RE = re.compile(r"Aud\s+(.+?)\s*\|\s*([\w-]+)(?:\s{3,}|\s*-{5,}|$)")
    _SUB_PROG_RE = re.compile(r"Sub\s+([\w-]+)\s*\|\s*(.+?)(?:\s{3,}|\s*-{5,}|$)")
    _SUBFIN_RE  = re.compile(r"(\d+\.?\d*(?:B|KB|MB|GB))\s+-\s+00:00:00")

    def __init__(self, binary_path: str, ffmpeg_path: str):
        self._binary   = binary_path
        self._ffmpeg   = ffmpeg_path
        self._video_label: str = "Video"
        self._video_task_key: str = "vid_main"
        self._audio_labels: Dict[str, str] = {}
        self._audio_task_keys: List[Tuple[str, str]] = []
        self._sub_labels: Dict[str, str] = {}

    @property
    def name(self) -> str:
        return "N_m3u8DL-RE"

    def get_formatter(self) -> "BaseFormatter":
        from VibraVid.source.utils.selector import N3u8dlFormatter
        return N3u8dlFormatter()

    @staticmethod
    def _name_slug(text: str) -> str:
        """Normalise a display name to a simple key slug, e.g. 'Italian [CC]' → 'italian_cc'."""
        return re.sub(r"[^\w]+", "_", text.lower()).strip("_")

    @staticmethod
    def _stream_label(s: "Stream") -> str:
        """
        Build a compact display label for a subtitle stream.
        Shows: extension [yellow][ext][/yellow] + language code (BCP-47) + flags.
        PREFIX is NOT included here — added by caller.
        """
        try:
            lang_raw = s.language or "und"
            
            # Detect flags from language code suffix if not already set
            _sfx     = re.search(r"[-_](forced|cc|sdh|hi)$", lang_raw, re.I)
            lang_sfx = _sfx.group(1).lower() if _sfx else ""

            forced = s.forced or lang_sfx == "forced"
            cc     = s.is_cc  or lang_sfx == "cc"
            sdh    = s.is_sdh or lang_sfx == "sdh"
            default = s.default and not forced

            lang = s.resolved_language or lang_raw or "und"
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
            ext_tag = f"[yellow]\\[{ext}][/yellow]"

            return f"{ext_tag} {' '.join(parts)}"
        except Exception:
            return s.language or "und"

    def prepare_stream_labels(self, streams: List["Stream"], manifest_type: str = "Unknown") -> None:
        """
        Build rich progress-bar labels from the already-selected Stream objects.

        Called once in wrapper.start_download() before the subprocess starts.
        """
        self._manifest_type = manifest_type
        logger.info(f"Preparing stream labels for {len(streams)} streams (manifest type: {manifest_type})")

        # Helper: find all alternative language codes for a given normalized form
        def get_lang_code_variants(normalized_lang: str) -> Set[str]:
            """
            Given a BCP-47 locale (e.g. "it-IT"), find all alternative codes from LANGUAGE_MAP.
            Returns: {"ita", "it", "italian", "it-it", "it-IT"} for Italian
            """
            if not normalized_lang:
                return set()
            
            variants: Set[str] = {normalized_lang, normalized_lang.lower()}
            base = normalized_lang.split("-")[0].lower()
            variants.add(base)
            
            # Find all keys in LANGUAGE_MAP that map to this normalized form
            for key, value in LANGUAGE_MAP.items():
                if value == normalized_lang or value == normalized_lang.lower():
                    variants.add(key)
                    variants.add(key.lower())
            
            return variants
        
        sel_video = [s for s in streams if s.type == "video"    and s.selected and not s.is_external]
        sel_audio = [s for s in streams if s.type == "audio"    and s.selected and not s.is_external]
        sel_subs  = [s for s in streams if s.type == "subtitle" and s.selected and not s.is_external]

        # ── Video ─────────────────────────────────────────────────────────────
        self._has_video = bool(sel_video)
        if sel_video:
            v = sel_video[0]
            try:
                codec = v.get_short_codec() or v.codecs or ""
            except Exception:
                codec = v.codecs or ""
            
            # Construct colored video label (WITHOUT the prefix — added in get_prebuilt_tasks)
            res = v.resolution or "main"
            bw  = v.bitrate_display
            
            parts = []
            if codec:
                parts.append(f"[yellow]\\[{codec}][/yellow]")
            if res:
                parts.append(f"[green]{res}[/green]")
            if bw:
                parts.append(f"[blue]{bw}[/blue]")
            
            self._video_label    = " ".join(parts) if parts else ""
            self._video_task_key = f"vid_{res}"
        else:
            self._video_label    = ""
            self._video_task_key = "vid_main"

        # ── Audio ─────────────────────────────────────────────────────────────
        self._audio_labels    = {}
        self._audio_task_keys = []
        seen_normalized = set()  # Track normalized language codes to avoid duplicates
        
        for s in sel_audio:
            lang = s.resolved_language or s.language or "und"
            
            try:
                codec = s.get_short_codec() or s.codecs or ""
            except Exception:
                codec = s.codecs or ""

            aparts = []
            if codec:
                aparts.append(f"[yellow]\\[{codec}][/yellow]")
            
            aparts.append(f"[bold white]{lang}[/bold white]")
            
            # Only show bitrate when it is actually known (bitrate=0 → omit, never "N/A")
            if s.bitrate:
                aparts.append(f"[blue]{s.bitrate_display}[/blue]")
            if s.default:
                aparts.append("[bold red][DEFAULT][/bold red]")

            label = " ".join(aparts)

            raw = (s.language or "und").lower()
            
            # Normalize language code to avoid duplicates (e.g., "ita" and "it-IT" both → "it")
            normalized = resolve_locale(raw) if raw else ""
            task_lang = normalized.split("-")[0].lower() if normalized else raw
            
            # Skip if we already have this normalized language (avoid duplicate audio tasks)
            if task_lang in seen_normalized:
                logger.debug(f"Audio {raw} (normalized: {task_lang}) already added, skipping duplicate")
                continue
            seen_normalized.add(task_lang)
            
            self._audio_labels[raw] = label

            # Also store by normalized form so n3u8dl's output (which may use either form) finds it
            if normalized and normalized != raw:
                self._audio_labels[normalized] = label

                # Also store lowercase variant (parser does .lower() on language codes from n3u8dl)
                self._audio_labels[normalized.lower()] = label

                # Store ALL alternative language codes (e.g., "ita", "it", "italian" for "it-IT")
                for variant in get_lang_code_variants(normalized):
                    self._audio_labels[variant] = label

            # _audio_task_keys: only real language codes (used for pre-built progress tasks)
            # Store both raw and the normalized task language
            self._audio_task_keys.append((task_lang, label))

            # Group-alias index for parse_progress_line partial matching — NOT used for tasks
            if s.id and ":" in s.id:
                group = s.id.split(":")[0].lower()
                self._audio_labels.setdefault(group, label)

        # ── Subtitles ──────────────────────────────────────────────────────────
        # Use compound keys to disambiguate streams with the same language code.
        # s.name is the original HLS NAME attribute; N_m3u8DL-RE uses this as
        # its display name in progress lines, so slugging it gives a match.
        self._sub_labels = {}
        for s in sel_subs:
            label = self._stream_label(s)
            raw   = (s.language or "und").lower()
            name  = (s.name or "").strip()

            if name:
                slug         = self._name_slug(name)
                compound_key = f"{raw}:{slug}"
                self._sub_labels[compound_key] = label
            
            # Simple lang-code fallback (first found wins)
            self._sub_labels.setdefault(raw, label)

        logger.debug(f"N3u8dlBackend labels: video={self._video_label!r} audio={self._audio_labels} subs={self._sub_labels}")

    def get_prebuilt_tasks(self) -> List[Tuple[str, str]]:
        """
        Return (task_key, rich_label) pairs in display order: video → audio.

        Uses _audio_task_keys (real language codes only, insertion-ordered) so
        group-ID aliases like 'audio' never produce phantom frozen progress bars.
        Subtitles are external-only and pre-created separately by the wrapper.
        """
        tasks: List[Tuple[str, str]] = []

        # Video
        if getattr(self, "_has_video", True):
            tasks.append((self._video_task_key, f"[bold cyan]Vid[/bold cyan] {self._video_label}"))

        # Audio — one task per selected stream, in selection order
        seen: set = set()
        for lang_code, label in self._audio_task_keys:
            task_key = f"aud_{lang_code}"
            if task_key not in seen:
                seen.add(task_key)
                tasks.append((task_key, f"[bold cyan]Aud[/bold cyan] {label}"))

        return tasks

    # ─────────────────────────────────────────────────────────────────────────
    # Command builder
    # ─────────────────────────────────────────────────────────────────────────

    def build_command(
        self, url: str, sv: str, sa: str, ss: str,
        filename: str, output_dir: Path, tmp_dir: Path,
        headers: Dict[str, str], cookies: Dict[str, str], key: Any = None, *,
        concurrent: bool = False, thread_count: int = 0, timeout: int = 30, retry_count: int = 3, max_speed: str = "",
        use_proxy: bool = False, proxy: str = "", extra_args: Optional[List[str]] = None,
    ) -> List[str]:
        cmd: List[str] = [
            self._binary,
            "--save-name",                filename,
            "--save-dir",                 str(output_dir),
            "--tmp-dir",                  str(tmp_dir),
            "--ffmpeg-binary-path",       self._ffmpeg,
            "--write-meta-json",          "false",
            "--binary-merge",
            "--del-after-done",
            "--auto-subtitle-fix",        "false",
            "--check-segments-count",     "false",
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

        # Always drop all subtitles from the manifest, since we handle them separately via
        cmd.extend(["--drop-subtitle", "all"])

        for k, v in headers.items():
            cmd.extend(["--header", f"{k}: {v}"])
        if cookies:
            cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
            cmd.extend(["--header", f"Cookie: {cookie_str}"])
        if use_proxy and proxy:
            cmd.extend(["--use-system-proxy", "false", "--custom-proxy", proxy])

        if concurrent:
            cmd.append("--concurrent-download")
        if thread_count > 0:
            cmd.extend(["--thread-count", str(thread_count)])
        if timeout > 0:
            cmd.extend(["--http-request-timeout", str(timeout)])
        if retry_count > 0:
            cmd.extend(["--download-retry-count", str(retry_count)])
        if max_speed and str(max_speed).lower() not in ("", "false"):
            cmd.extend(["--max-speed", str(max_speed)])

        if extra_args:
            cmd.extend(extra_args)

        cmd.append(url)
        return cmd

    def parse_progress_line(self, line: str, manifest_type: str) -> Optional[Dict[str, Any]]:
        """
        Parse one N_m3u8DL-RE stdout line.

        Returns None for listing lines and any line with no progress data.
        Returns a dict only when at least one of pct/speed/size/segments/
        final_size is present.

        Audio progress format:   "Aud {DisplayName} | {langcode}  ---"
        Subtitle progress format: "Sub {langcode} | {DisplayName}  ---"
        (Both drop the GROUP-ID prefix that appears in listing lines.)

        dict keys:
            track       "video" | "audio" | "subtitle"
            label       Rich-formatted description for the progress bar task
            _task_key   Unique stable key for the tasks dict
            _lang_code  Raw language code (used by wrapper for subtitle_sizes)
            pct         float 0..100
            speed       e.g. "24.99MBps"
            size        e.g. "99.49MB/1.51GB"
            segments    e.g. "33/435"
            final_size  subtitle completion size, e.g. "52.15KB"
        """
        line_s = line.strip()
        if not line_s:
            return None

        result: Dict[str, Any] = {}

        # ── Identify track type and resolve label ──────────────────────────
        if line_s.startswith("Vid"):

            # Video line: try resolution first (DASH), then bitrate fallback (HLS)
            m_res = self._VID_RES_RE.search(line_s)
            if m_res:

                # DASH format: "Vid 1920x1080 | 4500 Kbps | avc1"
                result["_task_key"]  = self._video_task_key
                result["label"]      = f"[bold cyan]Vid[/bold cyan] {self._video_label}"
                result["_lang_code"] = ""

            elif re.search(r"Vid\s+[\d.]+\s*[KMGT]?bps", line_s):
                # HLS format: "Vid 1885 Kbps" or "Vid 1.5 MBps" (bitrate without resolution)
                result["_task_key"]  = self._video_task_key
                result["label"]      = f"[bold cyan]Vid[/bold cyan] {self._video_label}"
                result["_lang_code"] = ""
            else:
                return None

        elif line_s.startswith("Aud"):
            result["track"] = "audio"
            content = re.split(r'\s{3,}|\s*-{5,}', line_s[3:])[0].strip()
            parts = [p.strip().lower() for p in content.split('|')]
            
            lang_code = ""
            label = ""
            
            # 1. Direct lookup by part in _audio_labels (e.g., "it", "en", "128 kbps")
            for p in parts:
                if p in self._audio_labels:
                    lang_code = p
                    label = self._audio_labels[p]
                    break
            
            # 2. Regex fallback: extract language and bitrate from content
            if not label:
                # Extract language: "it", "it-IT", "en-US", etc.
                lang_match = re.search(r'\b([a-z]{2}(?:-[a-z]{2})?)\b', content, re.I)
                if lang_match:
                    extracted_lang = lang_match.group(1).lower()
                    if extracted_lang in self._audio_labels:
                        lang_code = extracted_lang
                        label = self._audio_labels[extracted_lang]
            
            # 3. Bitrate-based fallback for HLS: match "128 Kbps" against configured labels
            if not label:
                br_match = re.search(r'(\d+(?:\.\d+)?)\s*([KMG]?bps)', content, re.I)
                if br_match:
                    bitrate_str = br_match.group(0).lower()
                    # Find a label containing this bitrate string
                    for k, v in self._audio_labels.items():
                        if bitrate_str in v.lower():
                            label = v
                            lang_code = k
                            break

            # 4. Last fallback: regex parsing with more detailed extraction
            if not label:
                m = self._AUD_PROG_RE.search(line_s)
                if m:
                    display_name = m.group(1).strip()
                    lang_code    = m.group(2).strip().lower()
                else:
                    display_name = content
            else:
                display_name = content

            # Normalize language code to resolve the correct task key
            normalized_lang = resolve_locale(lang_code) if lang_code else ""
            if normalized_lang:
                task_lang = normalized_lang.split("-")[0].lower()
            else:
                task_lang = lang_code
            
            final_label = label or f"[bold white]{display_name or 'Audio'}[/bold white]"
            result["label"] = f"[bold cyan]Aud[/bold cyan] {final_label}"
            result["_task_key"] = f"aud_{task_lang or 'main'}"
            result["_lang_code"] = lang_code

        elif line_s.startswith("Sub"):
            result["track"] = "subtitle"
            m = self._SUB_PROG_RE.search(line_s)
            if m:
                # Progress line: "Sub langcode | DisplayName  ---"
                lang_code = m.group(1).strip().lower()
                display_name = m.group(2).strip()
            else:
                lang_code    = ""
                display_name = ""

            # Compound key: "ita:italian_cc" for disambiguation
            name_slug    = self._name_slug(display_name) if display_name else lang_code
            compound_key = f"{lang_code}:{name_slug}"
            label = (self._sub_labels.get(compound_key) or self._sub_labels.get(lang_code, ""))
            if not label:
                # Partial match fallback for compound codes like "ita-forced"
                base = lang_code.split("-")[0]
                label = self._sub_labels.get(f"{base}:{name_slug}") or self._sub_labels.get(base, "")
            
            final_label = label or f"[bold white]{display_name or lang_code}[/bold white]"
            result["label"] = f"[bold cyan]Sub[/bold cyan] {final_label}"

            # Unique task key per subtitle stream (lang + display slug)
            result["_task_key"]  = f"sub_{lang_code}_{name_slug}" if name_slug else f"sub_{lang_code}"
            result["_lang_code"] = lang_code

            # Subtitle completion: "52.15KB     -      00:00:00"
            fm = self._SUBFIN_RE.search(line_s)
            if fm:
                result["final_size"] = fm.group(1)
                result["pct"]        = 100.0

        else:
            return None

        # ── Extract numeric progress data ──────────────────────────────────
        m = self._PERCENT_RE.search(line_s)
        if m:
            result["pct"] = float(m.group(1))

        m = self._SEG_RE.search(line_s)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b > 0:
                result["segments"] = f"{a}/{b}"

                # If no percentage yet, calculate from segments for better progress visibility
                if "pct" not in result:
                    result["pct"] = (a / b) * 100.0

        m = self._SIZE_RE.search(line_s)
        if m:
            result["size"] = f"{m.group(1)}/{m.group(2)}"

        m = self._SPEED_RE.search(line_s)
        if m:
            result["speed"] = m.group(1)

        # ── Only return when meaningful progress data is present ───────────
        # Listing lines ("Vid 1920x1080 | 4500 Kbps | avc1") have no
        # pct/size/speed — returning None lets the wrapper ignore them.
        has_data = any(k in result for k in ("pct", "segments", "size", "speed", "final_size"))
        return result if has_data else None

def create_backend(name: str, **kwargs) -> BaseDownloaderBackend:
    """
    Create a backend by name string.

    Usage::

        backend = create_backend("n3u8dl", binary_path="/usr/bin/N_m3u8DL-RE", ffmpeg_path="/usr/bin/ffmpeg")
        backend = create_backend("ytdlp")
        backend = create_backend("aria2c")
    """
    name_l = name.strip().lower().replace("-", "").replace("_", "")
    if name_l in ("n3u8dl", "n3u8dlre", "nm3u8dlre"):
        return N3u8dlBackend(
            binary_path=kwargs.get("binary_path", "N_m3u8DL-RE"),
            ffmpeg_path=kwargs.get("ffmpeg_path", "ffmpeg"),
        )
    raise ValueError(f"Unknown downloader backend: {name!r}. Known: n3u8dl, ytdlp, aria2c")