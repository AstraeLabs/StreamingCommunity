# 09.06.24

import gc
import os
import signal
import tempfile
import time
import logging
import threading
from contextlib import nullcontext
from functools import partial
from typing import Any, Optional

from rich.console import Console
from rich.prompt import Prompt
from rich.progress import Progress, TextColumn

from VibraVid.utils.http_client import create_client, get_userAgent
from VibraVid.utils import config_manager, os_manager, internet_manager
from VibraVid.cli.run import execute_hooks
from VibraVid.core.ui.progress_bar import CustomBarColumn
from VibraVid.core.ui.tracker import download_tracker, context_tracker
from VibraVid.core.ui.bar_manager import DownloadBarManager
from VibraVid.utils.vault.supa import supa_vault
from VibraVid.core.decryptor import Decryptor, KeysManager
from VibraVid.core.decryptor._mp4_inspector import parse_binary
from VibraVid.core.drm.system import KNOWN_DRM_SYSTEMS


msg = Prompt()
console = Console()
logger = logging.getLogger(__name__)

SKIP_DOWNLOAD = config_manager.config.get_bool('DOWNLOAD', 'skip_download')
DELAY_SS = config_manager.config.get_int('DOWNLOAD',  'delay_after_download')
PROBE_BYTES = 4 * 1024 * 1024  # 4 MB


class InterruptHandler:
    """
    Tracks Ctrl-C presses and decides when to abort cleanly vs. force-quit.
    """
    def __init__(self) -> None:
        self.interrupt_count:     int   = 0
        self.last_interrupt_time: float = 0.0
        self.kill_download:       bool  = False
        self.force_quit:          bool  = False

    def handle(self, signum, frame, original_handler) -> None:
        """Signal callback — attach with ``partial(self.handle, original_handler=prev)``."""
        now = time.time()
        if now - self.last_interrupt_time > 2:
            self.interrupt_count = 0

        self.interrupt_count      += 1
        self.last_interrupt_time   = now

        if self.interrupt_count == 1:
            self.kill_download = True
            console.print("\n[yellow]First interrupt received. Download will complete and save. Press Ctrl+C three times quickly to force quit.")

        elif self.interrupt_count >= 3:
            self.force_quit = True
            console.print("\n[red]Force quit activated. Saving partial download...")
            signal.signal(signum, original_handler)



class DRMProbe:
    def __init__(self, known_systems: Optional[dict] = None) -> None:
        self._systems: dict[str, str] = (known_systems if known_systems is not None else KNOWN_DRM_SYSTEMS)

    def probe(self, url: str, headers: dict, client) -> tuple:
        """
        Returns ``(encrypted: bool, scheme: str | None, drm_names: list[str])``.
        Never raises — all errors are caught and logged at DEBUG level.
        """
        try:
            raw = self._fetch_bytes(url, headers, client)
            if not raw:
                return False, None, []

            info = self._parse_bytes(raw)
            if not info.encrypted:
                logger.debug("DRMProbe: no encryption markers found in first 4 MB.")
                return False, None, []

            drm_names = self._resolve_drm_names(info.pssh_boxes)
            self._report(info.scheme, info.kid, drm_names)
            return True, info.scheme, drm_names

        except Exception as exc:
            logger.debug(f"DRMProbe failed (non-fatal): {exc}")
            return False, None, []

    def _fetch_bytes(self, url: str, headers: dict, client) -> Optional[bytes]:
        probe_headers = {**headers, "Range": f"bytes=0-{PROBE_BYTES - 1}"}
        resp = client.get(url, headers=probe_headers, timeout=15)

        if resp.status_code not in (200, 206):
            logger.debug(f"DRMProbe: unexpected status {resp.status_code} — skipping.")
            return None
        
        raw = resp.content[:PROBE_BYTES]
        return raw if raw else None

    @staticmethod
    def _parse_bytes(raw: bytes):
        """Write *raw* to a temp file, run ``parse_binary``, then delete."""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".mp4probe") as tmp:
            tmp.write(raw)
            tmp_path = tmp.name
        
        try:
            return parse_binary(tmp_path)
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    def _resolve_drm_names(self, pssh_boxes: list) -> list:
        known_names: list[str] = []
        unknown_ids: list[str] = []
        seen_known: set[str] = set()
        seen_unknown: set[str] = set()

        for box in pssh_boxes:
            sid  = box.get("system_id", "").replace(" ", "").lower()
            if not sid:
                continue

            name = self._systems.get(sid)
            if name:
                if name not in seen_known:
                    seen_known.add(name)
                    known_names.append(name)
                continue

            if sid not in seen_unknown:
                seen_unknown.add(sid)
                unknown_ids.append(sid)

        if unknown_ids:
            logger.debug(f"DRMProbe: unknown system_id(s): {', '.join(unknown_ids)}")

        if known_names and unknown_ids:
            return [*known_names, f"Unknown SID x{len(unknown_ids)}"]
        if known_names:
            return known_names
        if unknown_ids:
            return [f"Unknown ({sid[:8]}...)" for sid in unknown_ids]
        return ["Unknown"]

    @staticmethod
    def _report(scheme: Optional[str], kid: Optional[str], drm_names: list) -> None:
        label = ", ".join(drm_names) if drm_names else "unknown DRM"
        logger.info(f"DRMProbe: encryption detected — scheme={scheme or 'unknown'}, kid={kid or 'n/a'}, DRM=[{label}]")
        console.print(f"[cyan]Probe: [magenta]Encrypted [white]| [cyan]scheme: [magenta]{scheme or 'unknown'} [white]| [cyan]DRM: [magenta]{label}")


class PostDownloadDecryptor:

    @staticmethod
    def has_keys(key: Any) -> bool:
        """Return ``True`` when *key* contains at least one usable pair."""
        if key is None:
            return False
        if isinstance(key, KeysManager):
            return bool(key.get_keys_list())
        if isinstance(key, str):
            return bool(key.strip())
        if isinstance(key, (list, tuple)):
            return bool(key)
        return False

    def run(self, path: str, key: Any, download_id: Optional[str] = None) -> None:
        """
        Decrypt *path* in-place.  Logs and prints errors but never raises.
        """
        logger.info(f"PostDownloadDecryptor: probing {os.path.basename(path)} …")
        if download_id:
            download_tracker.update_status(download_id, "Decrypting ...")

        dec_path = path + ".dec"
        try:
            decryptor = Decryptor()
            mode, kid, *_rest = decryptor.detect_encryption(path)

            if mode is None:
                logger.info("PostDownloadDecryptor: file is not encrypted — skipping.")
                console.print("[dim]Keys provided but file is not encrypted — skipping decryption.")
                return

            logger.info(f"PostDownloadDecryptor: encryption found (mode={mode}, kid={kid}) — starting decryption.")

            if kid:
                self._warn_if_kid_missing(kid, key)

            ok = decryptor.decrypt(
                encrypted_path=path,
                keys=key,
                output_path=dec_path,
                stream_type="video",
                progress_cb=None,
            )

            if ok and os.path.exists(dec_path) and os.path.getsize(dec_path) > 0:
                try:
                    os.replace(dec_path, path)
                    logger.info(f"PostDownloadDecryptor: success → {os.path.basename(path)}")
                except Exception as exc:
                    logger.error(f"PostDownloadDecryptor: rename failed — {exc}")
                    console.print(f"[red]Decryption rename failed: {exc}")
                    self._remove(dec_path)
            
            else:
                logger.error(f"PostDownloadDecryptor: decryption failed for {os.path.basename(path)}")
                console.print(f"[red]Decryption failed for {os.path.basename(path)}")
                self._remove(dec_path)

        except Exception as exc:
            logger.error(f"PostDownloadDecryptor: unexpected error — {exc}")
            console.print(f"[red]Decryption error: {exc}")
            self._remove(dec_path)

    @staticmethod
    def _remove(path: str) -> None:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    @staticmethod
    def _warn_if_kid_missing(kid: str, key: Any) -> None:
        """Emit a warning when the probed KID is not among the provided keys."""
        kid_norm = kid.replace("-", "").lower()
        found    = False

        if isinstance(key, KeysManager):
            found = any(
                k.kid.replace("-", "").lower() == kid_norm
                for k in (key.get_keys_list() or [])
                if hasattr(k, "kid")
            )

        elif isinstance(key, str):
            # Accepts "kid:key" or "kid:key|kid:key|..." formats
            found = any(
                pair.split(":")[0].replace("-", "").lower() == kid_norm
                for pair in key.strip().split("|")
                if ":" in pair
            )

        elif isinstance(key, (list, tuple)):
            for pair in key:
                raw = pair if isinstance(pair, str) else str(pair)
                if raw.split(":")[0].replace("-", "").lower() == kid_norm:
                    found = True
                    break

        if not found:
            logger.warning(f"PostDownloadDecryptor: KID [{kid}] from probe not found among provided keys — decryption may fail.")
            console.print(f"[yellow]Warning:[/yellow] KID [cyan]{kid}[/cyan] missing — decryption may fail.")


class SupaTracker:
    def fire(self, title: str, media_type: str, site: str) -> None:
        try:
            threading.Thread(target=self._run, args=(title, media_type, site), daemon=False).start()
        except Exception:
            logger.debug("SupaTracker: failed to start background thread (ignored)")

    @staticmethod
    def _run(title: str, media_type: str, site: str) -> None:
        try:
            if not supa_vault:
                logger.warning("SupaTracker: supa_vault not initialized")
                return
            
            title_str = "://generic"
            media_type_str = (media_type or "").strip()
            site_str = (site or "").strip().lower()
            logger.info(f"SupaTracker: title={title_str} type={media_type_str} service={site_str}")
            result = supa_vault.track_download(title_str, media_type_str, site_str)
            logger.info(f"SupaTracker result: {result}")
        except Exception as exc:
            logger.error(f"SupaTracker error: {exc}", exc_info=True)


class MP4FileDownloader:
    _probe = DRMProbe()
    _decryptor = PostDownloadDecryptor()
    _tracker = SupaTracker()

    def __init__(self,url: str, path: str, referer: Optional[str] = None, headers_: Optional[dict] = None, download_id: Optional[str] = None, site_name: Optional[str] = None, label: str = "MP4", key: Any = None, max_percentage: Optional[float] = None) -> None:
        self.url = str(url).strip()
        self.path = os_manager.get_sanitize_path(path)
        self.referer = referer
        self.headers_ = headers_
        self.label = label
        self.key = key
        self.max_percentage = self._normalize_max_percentage(max_percentage)

        # Merge explicit args with context-level defaults
        self.download_id = download_id or context_tracker.download_id
        self.site_name   = site_name   or context_tracker.site_name
        self.media_type  = context_tracker.media_type or "Film"

        # Internal state (reset per download() call)
        self._temp_path: str = f"{self.path}.temp"
        self._interrupt: InterruptHandler = InterruptHandler()
        self._total: Optional[int] = None
        self._downloaded: int = 0
        self._incomplete_err: Any = False

    @staticmethod
    def _normalize_max_percentage(value: Optional[float]) -> float:
        try:
            value_f = float(value)
        except (TypeError, ValueError):
            return 100.0

        if value_f <= 0:
            return 100.0
        if value_f > 100:
            return 100.0
        return value_f

    def download(self) -> tuple:
        """
        Execute the full pipeline.  Returns ``(path | None, interrupted: bool, error: Optional[str])``.
        """
        if not self._preflight():
            return None, False, None

        self._start_gui_tracking()
        headers = self._build_headers()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._install_signal_handler()

        client = create_client(headers=headers)
        try:
            if not self._check_content_type(client, headers):
                return None, False, None
            
            self._run_drm_probe(client, headers)
            self._stream_to_disk(client, headers)
        
        finally:
            client.close()

        return self._finalise()

    def _preflight(self) -> bool:
        if SKIP_DOWNLOAD:
            console.print("[yellow]Download skipped due to configuration.")
            return False
        
        if os.path.exists(self.path):
            console.print("[yellow]File already exists.")
            return False
        
        if not (self.url.lower().startswith("http://") or self.url.lower().startswith("https://")):
            logger.error(f"Invalid URL: {self.url}")
            console.print(f"[red]Invalid URL: {self.url}")
            return False

        return True

    def _start_gui_tracking(self) -> None:
        if not self.download_id:
            return
        
        download_tracker.start_download(
            self.download_id,
            os.path.basename(self.path),
            self.site_name or "Unknown",
            self.media_type,
            path=os.path.abspath(self.path),
        )
        download_tracker.update_status(self.download_id, "Downloading ...")

    def _build_headers(self) -> dict:
        headers: dict = {}
        if self.referer:
            headers["Referer"] = self.referer
        if self.headers_:
            headers.update(self.headers_)
        else:
            headers["User-Agent"] = get_userAgent()
        return headers

    def _install_signal_handler(self) -> None:
        try:
            if threading.current_thread() is threading.main_thread():
                prev = signal.getsignal(signal.SIGINT)
                signal.signal(signal.SIGINT, partial(self._interrupt.handle, original_handler=prev))
        except Exception:
            pass

    def _check_content_type(self, client, headers: dict) -> bool:
        try:
            head = client.head(self.url)
            head.raise_for_status()
            content_type = (head.headers.get("content-type") or "").lower()
        except Exception:
            content_type = ""

        if "text/html" not in content_type and "application/json" not in content_type:
            return True  # looks like a binary/media response → proceed

        logger.error("HEAD indicates non-video content type; inspecting body …")
        try:
            resp = client.get(self.url)
            resp.raise_for_status()
            preview_text = resp.content[:2000].decode("utf-8", errors="replace")
            logger.info(f"Body preview: {preview_text}")
        except Exception as exc:
            logger.error(f"Fallback GET failed: {exc}")
        
        return False

    def _run_drm_probe(self, client, headers: dict) -> None:
        logger.info(f"Probing first {PROBE_BYTES // (1024 * 1024)} MB for DRM/encryption markers")
        if self.download_id:
            download_tracker.update_status(self.download_id, "Probing ...")

        encrypted, scheme, drm_names = self._probe.probe(self.url, headers, client)

        if encrypted:
            if not PostDownloadDecryptor.has_keys(self.key):
                console.print(f"[red]Warning:[/red] stream appears [red]encrypted[/red] ([cyan]{', '.join(drm_names) or 'unknown DRM'}[/cyan]) but [red]no decryption keys[/red] were provided — the downloaded file will remain encrypted.")
                logger.warning(f"Probe: encrypted ({scheme or 'unknown'}, DRM=[{', '.join(drm_names)}]) but no keys provided.")
            else:
                logger.info(f"Probe: encrypted ({scheme or 'unknown'}, DRM=[{', '.join(drm_names)}]) — keys present, will decrypt after download.")
        else:
            logger.info("Probe: no encryption markers found — clear stream.")

        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")

    def _stream_to_disk(self, client, headers: dict) -> None:
        response = client.get(self.url, stream=True)
        try:
            response.raise_for_status()
            self._total = self._parse_content_length(response)
            self._downloaded = 0
            self._incomplete_err = False

            if self._total is None:
                logger.error("No Content-Length — streaming until connection closes.")

            bar_mgr = DownloadBarManager(self.download_id)
            with bar_mgr as progress_bars:
                try:
                    progress_bars.add_prebuilt_tasks([("video", self.label)])
                except Exception:
                    pass

                with open(self._temp_path, "wb") as fh:
                    self._write_chunks(fh, response, progress_bars, time.time(), bar_mgr)
        finally:
            response.close()

    @staticmethod
    def _parse_content_length(response) -> Optional[int]:
        raw = response.headers.get("content-length")
        try:
            return int(raw) if raw is not None else None
        except Exception:
            return None

    def _build_progress_ctx(self):
        if context_tracker.is_gui:
            return nullcontext()
        
        return Progress(
            TextColumn(f"[yellow]{self.label}[/yellow] [cyan]Downloading[/cyan]: "),
            CustomBarColumn(),
            TextColumn(
                "[bright_green]{task.fields[downloaded]}[/bright_green] "
                "[bright_magenta]{task.fields[downloaded_unit]}[/bright_magenta]"
                "[dim]/[/dim]"
                "[bright_cyan]{task.fields[total_size]}[/bright_cyan] "
                "[bright_magenta]{task.fields[total_unit]}[/bright_magenta]"
            ),
            TextColumn(
                "[dim]\\[[/dim][bright_yellow]{task.fields[elapsed]}[/bright_yellow]"
                "[dim] < [/dim][bright_cyan]{task.fields[eta]}[/bright_cyan][dim]][/dim]"
            ),
            TextColumn("[bright_magenta]@[/bright_magenta]"),
            TextColumn("[bright_cyan]{task.fields[speed]}[/bright_cyan]"),
            console=console,
            refresh_per_second=10.0,
        )

    def _add_progress_task(self, progress_bars) -> Any:
        if self._total:
            size_val, size_unit = internet_manager.format_file_size(self._total).split(" ")
            task_total = self._total
        else:
            size_val, size_unit = "--", ""
            task_total = None
        
        try:
            return progress_bars.add_task(
                "download",
                total=task_total,
                downloaded="0.00",
                downloaded_unit="B",
                total_size=size_val,
                total_unit=size_unit,
                elapsed="0s",
                eta="--",
                speed="-- B/s",
            )
        except Exception:
            return None

    def _write_chunks(self, fh, response, progress_bars, start_time: float, bar_mgr: DownloadBarManager) -> None:
        try:
            for chunk in response.iter_content(chunk_size=65536):
                if self._interrupt.force_quit or (self.download_id and download_tracker.is_stopped(self.download_id)):
                    console.print("\n[red]Force quitting... Saving partial download.")
                    if self.download_id and download_tracker.is_stopped(self.download_id):
                        self._incomplete_err = "cancelled"
                    
                    break

                if chunk:
                    self._downloaded += fh.write(chunk)
                    self._tick_progress(progress_bars, start_time, bar_mgr)

                    if self._should_stop_at_max_percentage():
                        self._incomplete_err = f"max_percentage_reached:{self.max_percentage:.2f}"
                        self._interrupt.kill_download = True
                        break

        except KeyboardInterrupt:
            if not self._interrupt.force_quit:
                self._interrupt.kill_download = True

        except Exception as exc:
            self._incomplete_err = True
            self._interrupt.kill_download = True
            console.print(f"\n[red]Download error: {exc}. Saving partial download.")

        finally:
            try:
                fh.flush()
                os.fsync(fh.fileno())
            except Exception:
                pass

    def _should_stop_at_max_percentage(self) -> bool:
        if self.max_percentage >= 100.0 or not self._total:
            return False
        return (self._downloaded / self._total * 100.0) >= self.max_percentage

    def _tick_progress(self, progress_bars, start_time: float, bar_mgr: DownloadBarManager) -> None:
        elapsed = time.time() - start_time
        speed = self._downloaded / elapsed if elapsed > 0 else 0
        speed_str = internet_manager.format_transfer_speed(speed) if speed > 0 else "-- B/s"
        dl_val, dl_unit = internet_manager.format_file_size(self._downloaded).split(" ")
        percent = (self._downloaded / self._total * 100) if self._total else 0
        total_size_str = internet_manager.format_file_size(self._total) if self._total else "Unknown"
        pct_int = max(0, min(100, int(percent)))

        parsed = {
            "task_key": "video",
            "pct": percent,
            "speed": speed_str,
            "size": f"{dl_val} {dl_unit}/{total_size_str}",
            "segments": f"{pct_int}/100",
            "label": self.label,
            "display_label": self.label,
        }

        try:
            if bar_mgr:
                bar_mgr.handle_progress_line(parsed)
        except Exception:
            try:
                download_tracker.update_progress(
                    self.download_id,
                    "video",
                    progress=parsed.get("pct"),
                    speed=parsed.get("speed"),
                    size=parsed.get("size"),
                    segments=parsed.get("segments"),
                )
            except Exception:
                pass


    def _finalise(self) -> tuple:

        # Temp file missing entirely
        if not os.path.exists(self._temp_path):
            console.print("[red]Download failed or file is empty.")
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="File missing or empty")
            return None, self._interrupt.kill_download, "File missing or empty"

        # Explicitly cancelled
        if self._incomplete_err == "cancelled":
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="cancelled")
            return None, True, "cancelled"

        # Explicit threshold stop requested by user/config
        if isinstance(self._incomplete_err, str) and self._incomplete_err.startswith("max_percentage_reached:"):
            if not self._rename_temp():
                return None, True, self._incomplete_err

            # Try decryption even on partial files when keys are available.
            if PostDownloadDecryptor.has_keys(self.key):
                self._decryptor.run(self.path, self.key, self.download_id)

            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error=self._incomplete_err)
            return self.path, True, None

        # Atomic rename temp → final
        if not self._rename_temp():
            return None, self._interrupt.kill_download, None

        # Final file must exist now
        if not os.path.exists(self.path):
            console.print("[red]Download failed or file is empty.")
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="File missing or empty")
            return None, self._interrupt.kill_download, "File missing or empty"

        if self._incomplete_err or (self._total and os.path.getsize(self.path) < self._total):
            console.print("[yellow]Warning: download was incomplete (partial file saved).")

        # Post-download decryption
        if PostDownloadDecryptor.has_keys(self.key):
            self._decryptor.run(self.path, self.key, self.download_id)

        # GUI completion
        if self.download_id:
            download_tracker.complete_download(
                self.download_id,
                success=True,
                path=os.path.abspath(self.path),
            )

        # Analytics (fire-and-forget)
        self._tracker.fire(
            title = context_tracker.title or os.path.basename(self.path),
            media_type = self.media_type or "Film",
            site = self.site_name or "",
        )

        execute_hooks("post_run")
        if DELAY_SS > 0:
            console.print(f"\n[green]Sleeping {DELAY_SS} seconds before finishing...")
            time.sleep(DELAY_SS)

        return self.path, self._interrupt.kill_download, None

    def _rename_temp(self) -> bool:
        last_exc = None
        for attempt in range(10):
            try:
                os.replace(self._temp_path, self.path)
                return True
            except PermissionError as exc:
                last_exc = exc
                console.log(f"[yellow]Rename attempt {attempt + 1}/10 failed: {exc}")
                time.sleep(0.5)
                gc.collect()
        
        console.print(f"[red]Could not rename temp file after 10 retries: {last_exc}")
        return False

def MP4_Downloader(url: str, path: str, referer: Optional[str] = None, headers_: Optional[dict] = None, download_id: Optional[str] = None, site_name: Optional[str] = None, label: str = "MP4", key: Any = None, max_percentage: Optional[float] = None) -> tuple:
    """
    Backward-compatible entry point — wraps ``MP4FileDownloader.download()``.

    Parameters
    ----------
    url         : URL of the file to download.
    path        : Local file path to save to.
    referer     : Optional ``Referer`` header.
    headers_    : Optional extra headers dict.
    download_id : Optional GUI tracking ID.
    site_name   : Optional site name for GUI/analytics.
    label       : Progress-bar label (default ``"MP4"``; use ``"Audio"`` for music).
    key         : Decryption key(s) — ``KeysManager``, ``"kid:key"`` string,
    """
    return MP4FileDownloader(
        url=url,
        path=path,
        referer=referer,
        headers_=headers_,
        download_id=download_id,
        site_name=site_name,
        label=label,
        key=key,
        max_percentage=max_percentage,
    ).download()
