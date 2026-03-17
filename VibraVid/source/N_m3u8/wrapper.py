# 04.01.25

import asyncio
import logging
import platform
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from VibraVid.setup import get_ffmpeg_path, get_n_m3u8dl_re_path
from VibraVid.utils import config_manager
from VibraVid.core.manifest.m3u8 import HLSParser
from VibraVid.core.manifest.mpd import DashParser

from VibraVid.source.style.tracker import download_tracker
from VibraVid.source.style.bar_manager import DownloadBarManager
from VibraVid.source.utils.selector import StreamSelector
from VibraVid.source.style.ui import build_table
from VibraVid.core.downloader.subtitle import download_external_tracks_with_progress, build_ext_track_label
from VibraVid.core.manifest.stream import Stream
from VibraVid.source.backend import BaseDownloaderBackend, N3u8dlBackend
from VibraVid.source.utils.codec import VIDEO_EXTENSIONS, AUDIO_EXTENSIONS
from VibraVid.source.utils.decrypt import Decryptor, KeysManager


console = Console(force_terminal=True if platform.system().lower() != "windows" else None)
logger = logging.getLogger("Source")
CONCURRENT_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "concurrent_download")
THREAD_COUNT = config_manager.config.get_int("DOWNLOAD", "thread_count")
RETRY_COUNT = config_manager.config.get_int("DOWNLOAD", "retry_count")
REQUEST_TIMEOUT = config_manager.config.get_int("REQUESTS", "timeout")
MAX_SPEED = config_manager.config.get("DOWNLOAD", "max_speed")
USE_PROXY = config_manager.config.get_bool("REQUESTS", "use_proxy")
PROXY_CFG = config_manager.config.get_dict("REQUESTS", "proxy")


def _default_backend() -> N3u8dlBackend:
    return N3u8dlBackend(binary_path=get_n_m3u8dl_re_path(), ffmpeg_path=get_ffmpeg_path())


class MediaDownloader:
    """
    Orchestrates manifest parsing, stream selection, and delegated download.
    """
    def __init__(self, url: str, output_dir: str, filename: str, headers: Optional[Dict] = None, key: Optional[Any] = None, cookies: Optional[Dict] = None, decrypt_preference: str = "shaka", download_id: Optional[str] = None, site_name: Optional[str] = None,backend: Optional[BaseDownloaderBackend] = None):
        self.url = url
        self.output_dir = Path(output_dir)
        self.filename = filename
        self.headers = headers or {}
        self.key = key
        self.cookies = cookies or {}
        self.decrypt_preference = decrypt_preference.strip().lower()
        self.download_id = download_id
        self.site_name = site_name

        self._backend: BaseDownloaderBackend = backend or _default_backend()
        logger.info(f"MediaDownloader: backend={self._backend.name!r}")

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
        self.custom_filters: Optional[Dict[str, str]] = None
        self.license_url: Optional[str] = None
        self.drm_type: Optional[str] = None

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._tmp_dir = self.output_dir / f"{self.filename}_tmp"
        self._tmp_dir.mkdir(exist_ok=True)

        if self.download_id:
            _output_type = (
                "Movie" if config_manager.config.get("OUTPUT", "movie_folder_name") in str(self.output_dir)
                else "TV" if config_manager.config.get("OUTPUT", "serie_folder_name") in str(self.output_dir)
                else "Anime" if config_manager.config.get("OUTPUT", "anime_folder_name") in str(self.output_dir)
                else "other"
            )
            download_tracker.start_download(
                self.download_id, self.filename, self.site_name or "Unknown", _output_type
            )

    def set_backend(self, backend: BaseDownloaderBackend) -> None:
        """Swap the downloader backend after construction."""
        self._backend = backend
        logger.info(f"MediaDownloader: backend swapped to {backend.name!r}")

    def set_key(self, key: Any) -> None:
        
        if isinstance(key, KeysManager):
            self.key = key.get_keys_list()
        else:
            self.key = key

    def parse_stream(self, show_table: bool = True) -> List[Stream]:
        """
        Fetch the manifest, parse all streams, apply StreamSelector, then optionally print the selection table.
        """
        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing …")

        url_lower = self.url.lower().split("?")[0]
        if url_lower.endswith(".mpd"):
            parser = DashParser(self.url, self.headers)
        else:
            parser = HLSParser(self.url, self.headers)

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
        self._apply_selection()

        for ext in self.external_subtitles:
            lang = ext.get("language", "")
            selected = self._ext_lang_matches(lang, "subtitle")
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

    parser_stream = parse_stream  # backward compat alias

    def get_metadata(self) -> Tuple[str, str, str]:
        return (str(self.raw_m3u8), str(self.raw_mpd), "")

    def start_download(self) -> Dict[str, Any]:
        """Build and run the downloader command.  Returns the status dict."""
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading …")
        
        has_internal_subs_moved = False
        if self.streams:
            new_ext_subs = []
            for s in self.streams:
                if s.type == "subtitle" and s.selected and not s.is_external:
                    sub_url = s.playlist_url
                    if not sub_url and s.segments and len(s.segments) > 0:
                        sub_url = s.segments[0].url
                    
                    if sub_url:
                        new_ext_subs.append({
                            "url":      sub_url,
                            "language": s.language or "und",
                            "name":     s.name or "",
                            "forced":   s.forced,
                            "sdh":      s.is_sdh,
                            "cc":       s.is_cc,
                            "default":  s.default,
                            "type":     "vtt" if "vtt" in (s.codecs or "").lower() else "srt",
                            "_selected": True,
                        })
                        logger.info(f"Subtitle to download: {s.language} from {sub_url[:80]}")
                        s.selected = False
                        has_internal_subs_moved = True

            self.external_subtitles.extend(new_ext_subs)
            if new_ext_subs:
                logger.info(f"Moved {len(new_ext_subs)} subtitle(s) to external download")


        sv = self._sv or "best"
        sa = self._sa or "best"
        ss = "false" if has_internal_subs_moved else (self._ss or "all")

        proxy = ""
        if USE_PROXY:
            proxy = PROXY_CFG.get("https") or PROXY_CFG.get("http", "")

        # ── Let the backend learn the selected stream metadata ─────────────
        logger.info(f"Preparing backend with stream: len({self.streams}) manifest_type={self.manifest_type}")
        self._backend.prepare_stream_labels(self.streams, manifest_type=self.manifest_type)

        cmd = self._backend.build_command(
            url=self.url,
            sv=sv, sa=sa, ss=ss,
            filename=self.filename,
            output_dir=self.output_dir,
            tmp_dir=self._tmp_dir,
            headers=self.headers,
            cookies=self.cookies,
            key=self.key,
            concurrent=CONCURRENT_DOWNLOAD,
            thread_count=THREAD_COUNT,
            timeout=REQUEST_TIMEOUT,
            retry_count=RETRY_COUNT,
            max_speed=str(MAX_SPEED) if MAX_SPEED else "",
            use_proxy=USE_PROXY,
            proxy=proxy,
        )
        logger.info(f"{self._backend.name} command: {' '.join(cmd)}")

        with DownloadBarManager(self.download_id) as bar_manager:
            bar_manager.add_prebuilt_tasks(self._backend.get_prebuilt_tasks())
            _all_ext = (
                [(s, "subtitle") for s in self.external_subtitles if s.get("_selected", True)]
                + [(a, "audio")   for a in self.external_audios    if a.get("_selected", True)]
            )
            for _track, _ttype in _all_ext:
                _label    = build_ext_track_label(_track, _ttype)
                _lang     = _track.get("language", "und")
                _task_key = f"ext_{_ttype}_{_lang}_{id(_track)}"
                _track["_task_key"] = _task_key   # store so download func reuses it
                _track["_label"]    = _label
                bar_manager.add_external_track_task(_label, _task_key)

            # Start external subtitle/audio downloads in a background thread.
            loop = asyncio.new_event_loop()
            download_result: Dict[str, Any] = {"ext_subs": [], "ext_auds": []}

            def run_downloads() -> None:
                asyncio.set_event_loop(loop)
                try:
                    ext_subs, ext_auds = loop.run_until_complete(
                        download_external_tracks_with_progress(self.headers, self.external_subtitles, self.external_audios, self.output_dir, self.filename, bar_manager)
                    )
                    download_result["ext_subs"] = ext_subs
                    download_result["ext_auds"] = ext_auds
                except Exception as e:
                    logger.warning(f"External downloads failed: {e}")
                finally:
                    loop.close()

            download_thread = threading.Thread(target=run_downloads, daemon=False)
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
                        logger.info(f"{line.rstrip()}")
                    if self.download_id and download_tracker.is_stopped(self.download_id):
                        proc.terminate()
                        break
                    
                    parsed = self._backend.parse_progress_line(line, self.manifest_type)
                    bar_manager.handle_progress_line(parsed)

                bar_manager.finish_all_tasks()
            
            # Wait for subtitle/audio downloads to complete
            download_thread.join(timeout=300)  # Max 5 minutes wait
            if download_thread.is_alive():
                logger.warning("Download thread timeout - proceeding anyway")
            
            ext_subs = download_result["ext_subs"]
            ext_auds = download_result["ext_auds"]
            subtitle_sizes = bar_manager.subtitle_sizes

        if self.download_id and download_tracker.is_stopped(self.download_id):
            return {"error": "cancelled"}

        self.status = self._build_status(subtitle_sizes, ext_subs, ext_auds)

        if self.key:
            self._decrypt_check(self.status)

        return self.status

    # ─────────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────────────────────────────────
    def _apply_selection(self) -> None:
        f = self.custom_filters or {}
        v_cfg = f.get("video") or config_manager.config.get("DOWNLOAD", "select_video")
        a_cfg = f.get("audio") or config_manager.config.get("DOWNLOAD", "select_audio")
        s_cfg = f.get("subtitle") or config_manager.config.get("DOWNLOAD", "select_subtitle")

        formatter = self._backend.get_formatter()
        selector = StreamSelector(v_cfg, a_cfg, s_cfg, formatter=formatter)
        self._sv, self._sa, self._ss = selector.apply(self.streams)
        logger.info(f"Selection → video={self._sv!r}  audio={self._sa!r}  subtitle={self._ss!r}")

    def _ext_lang_matches(self, lang: str, track_type: str) -> bool:
        cfg_key = "select_subtitle" if track_type == "subtitle" else "select_audio"
        cfg = config_manager.config.get("DOWNLOAD", cfg_key)
        if not cfg or cfg.lower() == "all":
            return True
        if cfg.lower() == "false":
            return False
        tokens = [t.strip() for t in re.split(r"[|,]", cfg) if t.strip()]
        return any(t.lower() in lang.lower() for t in tokens)

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
            
            # Directly attempt decryption; let the decryptor handle detection/skipping
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

    def _build_status(self, subtitle_sizes: Dict, ext_subs: List, ext_auds: List = None) -> Dict:
        """
        Scan output_dir and build the status dict.
        """
        status: Dict[str, Any] = {
            "video": None,
            "audios": [],
            "subtitles": ext_subs or [],
            "external_subtitles": [],
            "external_audios": ext_auds or [],
        }

        VIDEO_EXTS = VIDEO_EXTENSIONS
        AUDIO_EXTS = AUDIO_EXTENSIONS

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
                track_name = f.stem[len(self.filename):].lstrip(".")
                status["audios"].append({"path": str(f), "name": track_name, "size": f.stat().st_size})
                continue

        return status

    def get_status(self) -> Dict:
        return self.status or self._build_status({}, [], [])