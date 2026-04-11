# 10.04.26

import re
import logging
import platform
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from rich.console import Console


from VibraVid.utils import config_manager
from VibraVid.core.manifest.m3u8 import HLSParser
from VibraVid.core.manifest.mpd import DashParser
from VibraVid.core.manifest.stream import Stream
from VibraVid.utils.http_client import create_client, get_headers
from VibraVid.utils.tmdb_client import tmdb_client

from VibraVid.core.ui.tracker import download_tracker
from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.core.ui.ui import build_table
from VibraVid.core.utils.selector import StreamSelector, N3u8dlFormatter
from VibraVid.core.utils.language import resolve_locale, LANGUAGE_MAP
from VibraVid.core.utils.stream_selector_ui import InteractiveStreamSelector
from VibraVid.core.downloader.subtitle import build_ext_track_label, is_valid_format, ext_from_url
from VibraVid.core.utils.codec import VIDEO_EXTENSIONS, AUDIO_EXTENSIONS
from VibraVid.core.utils.decrypt_engine import KeysManager


logger = logging.getLogger(__name__)
auto_select = config_manager.config.get_bool("DOWNLOAD", "auto_select")


def resolve_subtitle_url_sync(url: str, headers: Dict) -> Tuple[str, str]:
    """Synchronously probe *url* to determine the real subtitle format.

    If the response is an HLS manifest (``#EXTM3U``), the first media segment
    URL is extracted and its extension is used.  Returns ``(final_url, ext)``
    where *ext* may be an empty string if nothing recognisable was found.
    """
    try:
        hdrs = dict(headers)
        hdrs.setdefault("User-Agent", get_headers().get("User-Agent", ""))
        logger.info(f"resolve_subtitle_url_sync: probing {url!r}")
        with create_client(headers=hdrs, timeout=15, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            text = resp.text.strip()
    except Exception as exc:
        logger.info(f"resolve_subtitle_url_sync: request failed for {url!r}: {exc}")
        return url, ext_from_url(url, "")

    if text.startswith("#EXTM3U"):
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                resolved_ext = ext_from_url(line, "")
                logger.info(f"Resolved HLS subtitle manifest -> segment {line!r} (ext={resolved_ext!r})")
                return line, resolved_ext
        logger.info(f"resolve_subtitle_url_sync: manifest at {url!r} had no segments")
        return url, ""

    content_type = resp.headers.get("content-type", "").lower()
    for mime, ext in (
        ("vtt", "vtt"), ("webvtt", "vtt"), ("srt", "srt"),
        ("ttml", "ttml"), ("xml", "xml"), ("dfxp", "dfxp"),
    ):
        if mime in content_type:
            return url, ext
    return url, ext_from_url(url, "")


def lang_variants(normalized_lang: str) -> Set[str]:
    """Return every LANGUAGE_MAP key that resolves to *normalized_lang*."""
    if not normalized_lang:
        return set()
    variants: Set[str] = {normalized_lang, normalized_lang.lower()}
    variants.add(normalized_lang.split("-")[0].lower())
    for key, value in LANGUAGE_MAP.items():
        if value == normalized_lang or value.lower() == normalized_lang.lower():
            variants.add(key)
            variants.add(key.lower())
    return variants


class BaseMediaDownloader:
    def __init__(self, url: str, output_dir: str, filename: str, headers: Optional[Dict] = None, key: Optional[Any] = None, cookies: Optional[Dict] = None, download_id: Optional[str] = None, site_name: Optional[str] = None) -> None:
        self.url = url
        self.output_dir = Path(output_dir)
        self.filename = filename
        self.headers = headers or {}
        self.key = key
        self.cookies = cookies or {}
        self.download_id = download_id
        self.site_name = site_name

        self.streams: List[Stream] = []
        self.manifest_type: str = "Unknown"
        self.raw_m3u8: Optional[Path] = None
        self.raw_mpd: Optional[Path] = None
        self.status: Optional[dict] = None

        self._sv: str = "best"
        self._sa: str = "best"
        self._ss: str = "all"

        self.external_subtitles: list = []
        self.external_audios: list = []
        self.custom_filters: Optional[Dict] = None
        self.license_url: Optional[str] = None
        self.drm_type: Optional[str] = None

        # Progress-bar label tables — built in _prepare_labels()
        self._video_label: str = ""
        self._video_task_key: str = "vid_main"
        self._has_video: bool = True
        self._audio_labels: Dict[str, str] = {}
        self._audio_task_keys: List[Tuple[str, str]] = []
        self._sub_labels: Dict[str, str] = {}

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_dir = self.output_dir / f"{self.filename}_tmp"
        self._tmp_dir.mkdir(exist_ok=True)

        if self.download_id:
            _type = (
                "Movie" if config_manager.config.get("OUTPUT", "movie_folder_name") in str(self.output_dir)
                else "TV"    if config_manager.config.get("OUTPUT", "serie_folder_name") in str(self.output_dir)
                else "Anime" if config_manager.config.get("OUTPUT", "anime_folder_name") in str(self.output_dir)
                else "other"
            )
            download_tracker.start_download(
                self.download_id, self.filename, self.site_name or "Unknown", _type
            )

    def set_key(self, key: Any) -> None:
        self.key = key.get_keys_list() if isinstance(key, KeysManager) else key

    def parse_stream(self, show_table: bool = True) -> List[Stream]:
        """Fetch manifest → parse streams → apply selection → print table."""
        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing ...")

        url_lower = self.url.lower().split("?")[0]
        parser = (
            DashParser(self.url, self.headers)
            if url_lower.endswith(".mpd")
            else HLSParser(self.url, self.headers)
        )
        if not parser.fetch_manifest():
            logger.error("BaseMediaDownloader: manifest fetch failed")
            return []

        if isinstance(parser, DashParser):
            self.raw_mpd = parser.save_raw(self._tmp_dir)
            self.manifest_type = "DASH"
        else:
            self.raw_m3u8 = parser.save_raw(self._tmp_dir)
            self.manifest_type = "HLS"

        self.streams = [s for s in parser.parse_streams() if s.type != "image"]

        if auto_select:
            self._apply_selection()
        else:
            selector = InteractiveStreamSelector(self.streams, window_size=15)
            selector.run()

        for ext in self.external_subtitles:
            lang = ext.get("language", "")
            selected = self._ext_track_matches(ext, "subtitle")
            ext["_selected"] = selected
            fake = Stream(
                type="subtitle", language=lang, name=ext.get("name", ""),
                selected=selected, is_external=True,
            )
            fake.id = "EXT"
            self.streams.append(fake)

        for ext in self.external_audios:
            lang = ext.get("language", "")
            selected = self._ext_lang_matches(lang, "audio")
            ext["_selected"] = selected
            fake = Stream(
                type="audio", language=lang, name=ext.get("name", ""),
                selected=selected, is_external=True,
            )
            fake.id = "EXT"
            self.streams.append(fake)

        if show_table and self.streams:
            console = Console(force_terminal=True if platform.system().lower() != "windows" else None)
            console.print(build_table(self.streams))

        return self.streams

    def get_metadata(self) -> Tuple[str, str, str]:
        return (str(self.raw_m3u8), str(self.raw_mpd), "")

    def get_status(self) -> Dict:
        return self.status or self._build_status([], [])

    def _apply_selection(self) -> None:
        f = self.custom_filters or {}
        v_cfg = f.get("video")    or config_manager.config.get("DOWNLOAD", "select_video")
        a_cfg = f.get("audio")    or config_manager.config.get("DOWNLOAD", "select_audio")
        s_cfg = f.get("subtitle") or config_manager.config.get("DOWNLOAD", "select_subtitle")
        selector = StreamSelector(v_cfg, a_cfg, s_cfg, formatter=N3u8dlFormatter())
        self._sv, self._sa, self._ss = selector.apply(self.streams)
        logger.info(f"Selection -> video={self._sv!r}  audio={self._sa!r}  subtitle={self._ss!r}")

    def _ext_lang_matches(self, lang: str, track_type: str) -> bool:
        """Return True if the external track with the given *lang* tag should be downloaded."""
        cfg_key = "select_subtitle" if track_type == "subtitle" else "select_audio"
        cfg = config_manager.config.get("DOWNLOAD", cfg_key)
        if not cfg or cfg.lower() == "all":
            return True
        if cfg.lower() == "false":
            return False

        tokens = [t.strip().lower() for t in re.split(r"[|,]", cfg) if t.strip()]
        lang_l = lang.strip().lower()

        for token in tokens:
            base_token = token.split("_")[0]
            if base_token in lang_l or lang_l.startswith(base_token):
                return True
            # ISO-639-2 three-letter -> two-letter prefix match ("ita" -> "it")
            if len(base_token) == 3 and base_token.isalpha() and lang_l.startswith(base_token[:2]):
                return True
        return False

    def _ext_track_matches(self, track: Dict, track_type: str) -> bool:
        """Return True if *track* (a full external track dict with flag fields) matches the configured selection filter, including flag requirements.
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
            req_flags = {p for p in parts[1:] if p in {"forced", "cc", "sdh", "hi"}}
            if "hi" in req_flags:
                req_flags.discard("hi")
                req_flags.add("cc")

            lang_ok = (
                base_token in lang
                or lang.startswith(base_token)
                or (len(base_token) == 3 and base_token.isalpha() and lang.startswith(base_token[:2]))
            )
            if not lang_ok:
                continue

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

    def _build_status(self, ext_subs: List, ext_auds: List = None) -> Dict:
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
                status["audios"].append(
                    {"path": str(f), "name": track_name, "size": f.stat().st_size}
                )

        return status

    def _prepare_labels(self) -> None:
        sel_video = [s for s in self.streams if s.type == "video"    and s.selected and not s.is_external]
        sel_audio = [s for s in self.streams if s.type == "audio"    and s.selected and not s.is_external]
        sel_subs  = [s for s in self.streams if s.type == "subtitle" and s.selected and not s.is_external]
        logger.info(f"Preparing labels -- {len(self.streams)} streams  type={self.manifest_type}")

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
            self._video_label = " ".join(parts)
            self._video_task_key = f"vid_{res}"
        else:
            self._video_label = ""
            self._video_task_key = "vid_main"

        self._audio_labels = {}
        self._audio_task_keys = []
        seen_normalized: Set[str] = set()

        for s in sel_audio:
            lang = s.resolved_language or s.language or "und"
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
                logger.info(f"Audio {raw!r} already mapped as {task_lang!r} -- skip dup")
                continue
            seen_normalized.add(task_lang)

            self._audio_labels[raw] = label
            for variant in lang_variants(normalized):
                self._audio_labels[variant] = label
            if s.id and ":" in s.id:
                self._audio_labels.setdefault(s.id.split(":")[0].lower(), label)

            self._audio_task_keys.append((task_lang, label))

        self._sub_labels = {}
        for s in sel_subs:
            label = self._sub_stream_label(s)
            raw = (s.language or "und").lower()
            name = (s.name or "").strip()
            if name:
                self._sub_labels[f"{raw}:{tmdb_client._slugify(name)}"] = label
            self._sub_labels.setdefault(raw, label)

        logger.info(
            f"Labels ready -- video={self._video_label!r} "
            f"audio={list(self._audio_labels)[:4]}  subs={list(self._sub_labels)[:4]}"
        )

    @staticmethod
    def _sub_stream_label(s: Stream) -> str:
        try:
            lang_raw = s.language or "und"
            sfx = re.search(r"[-_](forced|cc|sdh|hi)$", lang_raw, re.I)
            lang_sfx = sfx.group(1).lower() if sfx else ""
            forced = s.forced   or lang_sfx == "forced"
            cc     = s.is_cc    or lang_sfx == "cc"
            sdh    = s.is_sdh   or lang_sfx == "sdh"
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
            return f"[yellow]\\[{ext}][/yellow] {' '.join(parts)}"
        except Exception:
            return s.language or "und"

    def _get_prebuilt_tasks(self) -> List[Tuple[str, str]]:
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

    def _promote_hls_subtitles_to_external(self) -> None:
        """Move selected HLS subtitle streams into self.external_subtitles."""
        new_ext_subs: List[Dict] = []
        for s in self.streams:
            if s.type == "subtitle" and s.selected and not s.is_external:
                sub_url = s.playlist_url or (s.segments[0].url if s.segments else None)
                if not sub_url:
                    continue

                ext = ext_from_url(sub_url, "")
                if not ext or not is_valid_format(ext, "subtitle"):
                    resolved_url, ext = resolve_subtitle_url_sync(sub_url, self.headers)
                    if not ext or not is_valid_format(ext, "subtitle"):
                        logger.info(
                            f"Skipping external subtitle (unsupported format): "
                            f"{s.language} url={sub_url}"
                        )
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
                logger.info(f"Subtitle -> external: {s.language}  url={sub_url[:80]}")
                s.selected = False

        self.external_subtitles.extend(new_ext_subs)
        if new_ext_subs:
            logger.info(f"Moved {len(new_ext_subs)} HLS subtitle(s) to external download")

    def _register_external_track_tasks(self, bar_manager: DownloadBarManager) -> None:
        """Add progress-bar tasks for every selected external subtitle/audio track."""
        for _track, _ttype in (
            [(s, "subtitle") for s in self.external_subtitles if s.get("_selected", True)]
            + [(a, "audio")  for a in self.external_audios    if a.get("_selected", True)]
        ):
            _label    = build_ext_track_label(_track, _ttype)
            _lang     = _track.get("language", "und")
            _task_key = f"ext_{_ttype}_{_lang}_{id(_track)}"
            _track["_task_key"] = _task_key
            _track["_label"]    = _label
            bar_manager.add_external_track_task(_label, _task_key)

    def start_download(self) -> Dict[str, Any]:  # pragma: no cover
        raise NotImplementedError("Subclasses must implement start_download()")