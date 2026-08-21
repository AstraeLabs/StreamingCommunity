# 01.04.26

import json
import locale
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from VibraVid.core.ui.bar_manager import console
from VibraVid.setup import get_bento4_decrypt_path, get_ffmpeg_path, get_flux_path, get_shaka_packager_path
from VibraVid.utils import config_manager
from VibraVid.utils._mp4dump import parse_file as _mp4dump_parse_file

from ._models import SCHEME_TO_MODE, detect_encryption_info
from ._subprocess_runner import _ENGINE_LOG_LEVELS, _log_engine_output_enabled, _strip_profile_lines, run_with_progress
from .keys_manager import KeysManager

logger = logging.getLogger(__name__)
_TRANSIENT_OPEN_ERROR_MARKERS = (
    "cannot open input file",           # mp4decrypt (Bento4)
    "cannot open file for reading",     # shaka-packager
)
_OPEN_RETRY_ATTEMPTS = 6
_OPEN_RETRY_BASE_DELAY = 0.4
_OPEN_RETRY_MAX_DELAY = 3.0
_AV_HANDLER_TYPES = {"vide", "soun"}
_DEFAULT_ENGINE_PRIORITY: tuple[str, ...] = ("flux", "shaka", "bento4")
_SCHEME_ENGINE_PRIORITY: dict[str, tuple[str, ...]] = {
    "fps": ("shaka", "bento4"),
}


def _trak_handler_type(trak) -> str | None:
    """Find `trak/mdia/hdlr`'s `handler_type`, or `None` if the atom tree is missing it."""
    stack = list(trak.children)
    while stack:
        atom = stack.pop()
        if atom.type == "hdlr":
            return atom.data.get("handler_type")
        stack.extend(atom.children)
    return None


def _count_traks(atoms) -> int:
    """Count every audio/video `trak` atom, recursively, across `atoms` (as returned by `VibraVid.utils._mp4dump.parse_file`)."""
    count = 0
    stack = list(atoms)
    while stack:
        atom = stack.pop()
        if atom.type == "trak" and _trak_handler_type(atom) in _AV_HANDLER_TYPES:
            count += 1
        stack.extend(atom.children)
    return count


def _is_transient_open_error(stderr_text: str | None) -> bool:
    """Check if the stderr text contains any known transient open error markers."""
    text = (stderr_text or "").lower()
    return any(marker in text for marker in _TRANSIENT_OPEN_ERROR_MARKERS)


def _open_retry_delay(attempt: int) -> float:
    """Calculate the delay before retrying to open a file, using exponential backoff."""
    return min(_OPEN_RETRY_BASE_DELAY * (2**attempt), _OPEN_RETRY_MAX_DELAY)


def _ansi_encodable(path: str) -> bool:
    """Check if the given path can be encoded in the system's preferred ANSI encoding."""
    if os.name != "nt":
        return True
    try:
        path.encode(locale.getpreferredencoding(False))
        return True
    except (UnicodeEncodeError, LookupError):
        return False


class _AnsiSafePathGuard:
    def __init__(self, encrypted_path: str, output_path: str, forbid_comma: bool = False):
        self.encrypted_path = encrypted_path
        self.output_path = output_path
        self.safe_encrypted_path = encrypted_path
        self.safe_output_path = output_path
        self.forbid_comma = forbid_comma
        self._linked_input = False

    def _needs_alias(self, path: str) -> bool:
        return not _ansi_encodable(path) or (self.forbid_comma and "," in path)

    def __enter__(self) -> "_AnsiSafePathGuard":
        if self._needs_alias(self.encrypted_path):
            ext = os.path.splitext(self.encrypted_path)[1]
            alias = os.path.join(tempfile.gettempdir(), f"vv_dec_in_{uuid.uuid4().hex}{ext}")
            try:
                os.link(self.encrypted_path, alias)
            except OSError:
                shutil.copy2(self.encrypted_path, alias)
            self.safe_encrypted_path = alias
            self._linked_input = True
            logger.debug(f"Input path unsafe for this tool (ANSI/comma), aliased via {alias}")

        if self._needs_alias(self.output_path):
            ext = os.path.splitext(self.output_path)[1]
            self.safe_output_path = os.path.join(tempfile.gettempdir(), f"vv_dec_out_{uuid.uuid4().hex}{ext}")
            logger.debug(f"Output path unsafe for this tool (ANSI/comma), aliased via {self.safe_output_path}")

        return self

    def finalize(self) -> None:
        """Move the aliased output (if any) back to the real output path."""
        if self.safe_output_path != self.output_path and os.path.exists(self.safe_output_path):
            try:
                os.replace(self.safe_output_path, self.output_path)
            except OSError:
                shutil.copy2(self.safe_output_path, self.output_path)
                os.remove(self.safe_output_path)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._linked_input and self.safe_encrypted_path != self.encrypted_path:
            try:
                os.remove(self.safe_encrypted_path)
            except OSError:
                pass
        if self.safe_output_path != self.output_path:
            try:
                if os.path.exists(self.safe_output_path):
                    os.remove(self.safe_output_path)
            except OSError:
                pass


class _FluxDaemon:
    def __init__(self, flux_path: str):
        self._proc = subprocess.Popen(
            [flux_path, "--daemon"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            encoding="utf-8",
            errors="replace",
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
        self._lock = threading.Lock()
        self._dead = False
        self._stderr_lines: list[str] = []
        self._stderr_lock = threading.Lock()
        self._job_count = 0
        self._last_job_lines: list[str] = []
        self._last_job_had_fragments_info = False
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        try:
            for line in self._proc.stderr:
                with self._stderr_lock:
                    self._stderr_lines.append(line)
        except Exception:
            pass

    def _take_job_stderr(self) -> list[str]:
        with self._stderr_lock:
            lines, self._stderr_lines = self._stderr_lines, []
        return lines

    def _log_job_output(self, lines: list[str], tag: str) -> None:
        if not lines:
            return
        level = _ENGINE_LOG_LEVELS.get("FLUX", logging.INFO)
        for line in lines:
            stripped = line.rstrip()
            if stripped:
                logger.log(level, f"[flux daemon, {tag}] {stripped}")

    def decrypt(self, input_path: str, output_path: str, keys: list[str], fragments_info: str | None) -> tuple[bool, str | None]:
        """Run one job through the daemon. Returns (ok, error_message)."""
        if self._dead or self._proc.poll() is not None:
            self._dead = True
            return False, "daemon not running"

        job = json.dumps(
            {"input": input_path, "output": output_path, "keys": keys, "fragments_info": fragments_info}
        )

        with self._lock:
            try:
                self._proc.stdin.write(job + "\n")
                self._proc.stdin.flush()
                line = self._proc.stdout.readline()
            except (BrokenPipeError, OSError) as exc:
                self._dead = True
                return False, f"daemon pipe error: {exc}"

        if not line:
            self._dead = True
            stderr_tail = ""
            try:
                stderr_tail = (self._proc.stderr.read() or "")[-300:]
            except Exception:
                pass
            return False, f"daemon exited unexpectedly ({stderr_tail or 'no stderr'})"

        self._job_count += 1
        self._last_job_had_fragments_info = fragments_info is not None
        if fragments_info is not None:
            self._take_job_stderr()
        else:
            if self._job_count == 1:
                time.sleep(0.05)
            job_lines = self._take_job_stderr()
            self._last_job_lines = job_lines  # overwritten every call -- close() logs whatever's here as "last"
            if _log_engine_output_enabled() and self._job_count == 1:
                self._log_job_output(job_lines, "first job")

        try:
            resp = json.loads(line)
        except json.JSONDecodeError as exc:
            return False, f"bad daemon response line {line.strip()!r}: {exc}"

        if resp.get("ok"):
            return True, None
        return False, resp.get("error") or "unknown daemon error"

    def close(self) -> None:
        if _log_engine_output_enabled() and self._job_count > 1 and not self._last_job_had_fragments_info:
            self._log_job_output(self._last_job_lines, f"last job, #{self._job_count}")

        if self._dead or self._proc.poll() is not None:
            return
        try:
            with self._lock:
                self._proc.stdin.write("--quit\n")
                self._proc.stdin.flush()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass


class Decryptor:
    def __init__(self, license_url: str = None, drm_type: str = None, **_kwargs) -> None:
        logger.debug(f"Initializing Decryptor license_url={license_url!r} drm_type={drm_type!r}")
        self.flux_path = get_flux_path()
        self.mp4decrypt_path = get_bento4_decrypt_path()
        self.shaka_packager_path = get_shaka_packager_path()
        self.ffmpeg_path = get_ffmpeg_path()
        self.license_url = license_url
        self.drm_type = drm_type
        self._flux_daemon: _FluxDaemon | None = None
        self._flux_daemon_failed = False

    @staticmethod
    def _redacted_cmd(cmd: list[str]) -> str:
        """Redact sensitive information (like keys) from the command for logging purposes."""
        redacted = []
        hide_next = False
        for token in cmd:
            if hide_next:
                redacted.append("<redacted>")
                hide_next = False
                continue
            if token in {"--key", "--keys"}:
                redacted.append(token)
                hide_next = True
                continue
            redacted.append(token)
        return " ".join(redacted)

    def _engine_order(self, enc_method: str | None, scheme: str | None) -> list[str]:
        """Full priority order for this track's scheme, filtered to available binaries: an
        explicit per-scheme override wins for first place, the rest follow the scheme's
        default priority. Used to try one engine, then fall through to the next on failure."""
        key = "fps" if (enc_method and "sample" in enc_method.lower()) else (scheme or "").lower()

        priority = _SCHEME_ENGINE_PRIORITY.get(key, _DEFAULT_ENGINE_PRIORITY)

        engine_map = config_manager.config.get("DRM", "decrypt_engine_map", default={}) or {}
        if isinstance(engine_map, dict):
            override = str(engine_map.get(key, "")).strip().lower()
            if override in ("bento4", "shaka", "flux"):
                priority = (override, *[e for e in priority if e != override])

        availability = {"flux": bool(self.flux_path), "shaka": bool(self.shaka_packager_path), "bento4": bool(self.mp4decrypt_path)}
        ordered = [engine for engine in priority if availability[engine]]
        return ordered or [priority[0]]

    def _engine_for(self, enc_method: str | None, scheme: str | None) -> str:
        """Pick which decrypt engine to use for this track: an explicit per-scheme override wins,
        otherwise walk the scheme's priority order and take the first engine whose binary is available."""
        order = self._engine_order(enc_method, scheme)
        key = "fps" if (enc_method and "sample" in enc_method.lower()) else (scheme or "").lower()
        preferred = _SCHEME_ENGINE_PRIORITY.get(key, _DEFAULT_ENGINE_PRIORITY)[0]
        if order[0] != preferred:
            logger.warning(f"scheme calls for {preferred} but it's not available — falling back to {order[0]}")
        return order[0]

    def detect_encryption(self, file_path: str) -> tuple:
        """Detect the encryption scheme and related information for the given file."""
        logger.debug(f"Detecting encryption: {os.path.basename(file_path)}")
        info = detect_encryption_info(file_path)

        if not info.encrypted:
            logger.info("No encryption indicators found")
            return None, None, None, None, None, None, False

        mode = SCHEME_TO_MODE.get(info.scheme or "")
        if mode is None:
            mode = "ctr"
            console.print("[dim]Encryption detected (no explicit scheme). Defaulting to CTR mode.")

        logger.debug(f"Encryption finalized: scheme={info.scheme}, mode={mode}, kid={info.kid}, codec={info.video_codec}, enc_method={info.encryption_method}, piff={info.is_piff}")
        return mode, info.kid, info.pssh_b64, info.video_codec, info.encryption_method, info.scheme, info.is_piff

    def _decrypt_flux_nonlive(
        self,
        encrypted_path: str,
        normalized_keys: list[tuple[str, str]],
        output_path: str,
        label: str,
        is_fixed_key: bool = False,
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
        status: str | None = None,
        stream_type: str = "video",
    ) -> bool:
        """Decrypt a non-live (static) encrypted file using `flux` (`-f progressive`) — subprocess only"""
        if not self.flux_path:
            return False

        try:
            pairs = normalized_keys
            if is_fixed_key and normalized_keys:
                _, key_hex = normalized_keys[0]
                pairs = [("00000000000000000000000000000000", key_hex)]
            if not pairs:
                return False

            with _AnsiSafePathGuard(encrypted_path, output_path) as guard:
                cmd = [
                    self.flux_path,
                    "-i",
                    guard.safe_encrypted_path,
                    "-o",
                    guard.safe_output_path,
                    "-f",
                    "progressive"
                ]
                for kid, key in pairs:
                    cmd.extend(["--key", f"{kid.lower()}:{key.lower()}"])

                logger.info(f"flux cmd: {self._redacted_cmd(cmd)}")
                _flux_t0 = time.monotonic()

                result = None
                for attempt in range(_OPEN_RETRY_ATTEMPTS):
                    result = run_with_progress(
                        cmd,
                        label,
                        guard.safe_encrypted_path,
                        guard.safe_output_path,
                        progress_cb=progress_cb,
                        status=status,
                        engine_name="FLUX",
                        stream_type=stream_type,
                    )
                    if result is True:
                        break
                    stderr_text = result[1] if isinstance(result, tuple) else str(result)
                    if attempt < _OPEN_RETRY_ATTEMPTS - 1 and _is_transient_open_error(stderr_text):
                        logger.warning(f"flux could not open input (attempt {attempt + 1}/{_OPEN_RETRY_ATTEMPTS}), retrying: {stderr_text}")
                        time.sleep(_open_retry_delay(attempt))
                        continue
                    break

                if result is not True:
                    stderr_text = result[1] if isinstance(result, tuple) else str(result)
                    logger.debug(f"flux declined/failed after {time.monotonic() - _flux_t0:.1f}s: {stderr_text}")
                    return False

                if not os.path.exists(guard.safe_output_path) or os.path.getsize(guard.safe_output_path) <= 0:
                    logger.debug(f"flux reported success but output is missing/empty after {time.monotonic() - _flux_t0:.1f}s — falling back")
                    return False

                try:
                    src_traks = _count_traks(_mp4dump_parse_file(guard.safe_encrypted_path))
                    out_traks = _count_traks(_mp4dump_parse_file(guard.safe_output_path))
                    if out_traks < src_traks:
                        logger.warning(f"flux output has fewer tracks than the source ({out_traks} < {src_traks}) — likely a mixed cleartext+protected multiplex it silently narrowed")
                        return False
                except Exception as exc:
                    logger.debug(f"flux track-count safety check failed ({exc}) — trusting flux's own success")

                guard.finalize()
                logger.info(f"flux finished -> {os.path.basename(output_path)} in {time.monotonic() - _flux_t0:.1f}s ({os.path.getsize(output_path)} bytes)")
                return True

        except Exception as e:
            logger.warning(f"flux decrypt failed, falling back: {e}")
            return False

    def _get_flux_daemon(self) -> "_FluxDaemon | None":
        """Get or start the persistent flux daemon for live decryption, if available."""
        if self._flux_daemon_failed:
            return None
        if self._flux_daemon is not None:
            return self._flux_daemon
        if not self.flux_path:
            self._flux_daemon_failed = True
            return None
        try:
            self._flux_daemon = _FluxDaemon(self.flux_path)
            logger.debug("flux daemon started for this track's live decrypt")
            return self._flux_daemon
        except Exception as exc:
            logger.warning(f"flux daemon failed to start, falling back to one-shot spawns: {exc}")
            self._flux_daemon_failed = True
            return None

    def close_flux_daemon(self) -> None:
        """Stop this Decryptor's persistent flux daemon (if any) — call once a track's live decrypt is done."""
        if self._flux_daemon is not None:
            self._flux_daemon.close()
            self._flux_daemon = None

    def _decrypt_flux_live(
        self,
        encrypted_path: str,
        decrypted_path: str,
        normalized_keys: list[tuple[str, str]],
        init_path: str | None = None,
    ) -> tuple:
        """Decrypt a live (streaming) encrypted segment using `flux`"""
        logger.debug(f"decrypt_flux_live(): {os.path.basename(encrypted_path)} -> {os.path.basename(decrypted_path)}")

        if not self.flux_path:
            return False, "Error flux: not available", None

        if not normalized_keys:
            logger.error("flux live decryption requested without usable keys")
            return False, "Error flux: no usable keys", None

        try:
            with _AnsiSafePathGuard(encrypted_path, decrypted_path) as guard:
                daemon = self._get_flux_daemon()
                if daemon is not None:
                    keys_arg = [f"{kid.lower()}:{raw_key.lower()}" for kid, raw_key in normalized_keys]
                    daemon_init = init_path if (init_path and os.path.exists(init_path)) else None
                    ok, err = daemon.decrypt(guard.safe_encrypted_path, guard.safe_output_path, keys_arg, daemon_init)

                    if ok:
                        size = os.path.getsize(guard.safe_output_path) if os.path.exists(guard.safe_output_path) else 0
                        if size <= 0:
                            return False, "Error flux: output file missing or empty", None
                        guard.finalize()
                        logger.debug(f"flux live segment decrypted successfully via daemon: {size} bytes")
                        return True, "flux live segment decrypted", None

                    logger.debug(f"flux daemon job failed ({err}), falling back to one-shot spawn for this segment")

                cmd = [self.flux_path]
                if init_path and os.path.exists(init_path):
                    cmd.extend(["--fragments-info", init_path])

                cmd.extend(["-i", guard.safe_encrypted_path, "-o", guard.safe_output_path, "-f", "progressive"])
                for kid, raw_key in normalized_keys:
                    cmd.extend(["--key", f"{kid.lower()}:{raw_key.lower()}"])

                logger.debug(f"flux live cmd: {self._redacted_cmd(cmd)}")

                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=180,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
                )

                if result.returncode != 0:
                    msg = _strip_profile_lines(result.stderr).strip() if result.stderr else "Unknown error"
                    logger.error(f"flux live decryption failed: {msg}")
                    return False, f"Error flux: {msg}", None

                size = os.path.getsize(guard.safe_output_path) if os.path.exists(guard.safe_output_path) else 0
                if size <= 0:
                    return False, "Error flux: output file missing or empty", None

                guard.finalize()
                logger.debug(f"flux live segment decrypted successfully: {size} bytes")
                return True, "flux live segment decrypted", None

        except Exception as exc:
            logger.error(f"Exception flux live: {exc}")
            return False, f"Exception flux: {exc}", None

    def _decrypt_bento4_nonlive(
        self,
        encrypted_path: str,
        normalized_keys: list[tuple[str, str]],
        output_path: str,
        label: str,
        is_fixed_key: bool = False,
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
        status: str | None = None,
    ) -> bool:
        """Decrypt a non-live (static) encrypted file using Bento4's mp4decrypt tool."""
        if not self.mp4decrypt_path:
            return False

        with _AnsiSafePathGuard(encrypted_path, output_path) as guard:
            cmd = [self.mp4decrypt_path]

            pairs = normalized_keys
            if is_fixed_key and normalized_keys:
                _, key_hex = normalized_keys[0]
                pairs = [("00000000000000000000000000000000", key_hex)]

            for kid, key in pairs:
                cmd.extend(["--key", f"{kid.lower()}:{key.lower()}"])
            cmd.extend([guard.safe_encrypted_path, guard.safe_output_path])

            logger.info(f"Bento4 cmd: {self._redacted_cmd(cmd)}")
            _bento4_t0 = time.monotonic()

            result = None
            for attempt in range(_OPEN_RETRY_ATTEMPTS):
                result = run_with_progress(
                    cmd,
                    label,
                    guard.safe_encrypted_path,
                    guard.safe_output_path,
                    progress_cb=progress_cb,
                    status=status,
                    engine_name="BENTO4",
                )
                if result is True:
                    if not os.path.exists(guard.safe_output_path) or os.path.getsize(guard.safe_output_path) <= 0:
                        logger.error(f"Bento4 reported success but output is missing/empty after {time.monotonic() - _bento4_t0:.1f}s")
                        return False
                    guard.finalize()
                    logger.info(f"Bento4 finished -> {os.path.basename(output_path)} in {time.monotonic() - _bento4_t0:.1f}s ({os.path.getsize(output_path)} bytes)")
                    return True

                stderr_text = result[1] if isinstance(result, tuple) else str(result)
                if attempt < _OPEN_RETRY_ATTEMPTS - 1 and _is_transient_open_error(stderr_text):
                    logger.warning(f"Bento4 could not open input (attempt {attempt + 1}/{_OPEN_RETRY_ATTEMPTS}), retrying: {stderr_text}")
                    time.sleep(_open_retry_delay(attempt))
                    continue
                break

            logger.error(f"Bento4 failed after {time.monotonic() - _bento4_t0:.1f}s: {result}")
            console.print(f"[red]Bento4 failed: {result}")
            return False

    def _decrypt_bento4_live(
        self,
        encrypted_path: str,
        decrypted_path: str,
        normalized_keys: list[tuple[str, str]],
        init_path: str | None = None,
    ) -> tuple:
        """Decrypt a live (streaming) encrypted segment using Bento4's mp4decrypt tool."""
        logger.debug(f"decrypt_bento4_live(): {os.path.basename(encrypted_path)} -> {os.path.basename(decrypted_path)}")

        if not self.mp4decrypt_path:
            return False, "Error Bento4: not available", None

        try:
            with _AnsiSafePathGuard(encrypted_path, decrypted_path) as guard:
                cmd = [self.mp4decrypt_path]
                if init_path and os.path.exists(init_path):
                    cmd.extend(["--fragments-info", init_path])

                if not normalized_keys:
                    logger.error("Bento4 live decryption requested without usable keys")
                    return False, "Error Bento4: no usable keys", None

                for kid, raw_key in normalized_keys:
                    cmd.extend(["--key", f"{kid}:{raw_key}"])
                cmd.extend([guard.safe_encrypted_path, guard.safe_output_path])
                logger.debug(f"Bento4 live cmd: {self._redacted_cmd(cmd)}")

                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=180,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
                )
                if result.returncode != 0:
                    msg = result.stderr.strip() if result.stderr else "Unknown error"
                    logger.error(f"Bento4 live decryption failed: {msg}")
                    return False, f"Error Bento4: {msg}", None

                size = os.path.getsize(guard.safe_output_path) if os.path.exists(guard.safe_output_path) else 0
                if size <= 0:
                    return False, "Error Bento4: output file missing or empty", None

                guard.finalize()
                logger.debug(f"Bento4 live segment decrypted successfully: {size} bytes")
                return True, "Bento4 live segment decrypted", None

        except Exception as exc:
            logger.error(f"Exception Bento4 live: {exc}")
            return False, f"Exception Bento4: {exc}", None

    def _decrypt_shaka_nonlive(
        self,
        encrypted_path: str,
        normalized_keys: list[tuple[str, str]],
        output_path: str,
        stream_type: str,
        label: str,
        is_fixed_key: bool = False,
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
        status: str | None = None,
    ) -> bool:
        """Decrypt a non-live (static) encrypted file using Shaka Packager."""
        if not self.shaka_packager_path:
            return False

        keys_arg: list[str] = []
        for idx, (kid, key) in enumerate(normalized_keys, start=1):
            shaka_kid = "00000000000000000000000000000000" if is_fixed_key else kid
            keys_arg.append(f"label={idx}:key_id={shaka_kid.lower()}:key={key.lower()}")

        shaka_output = output_path
        if not output_path.lower().endswith((".mp4", ".m4v", ".mpd")):
            shaka_output = output_path + ".tmp.mp4"

        with _AnsiSafePathGuard(encrypted_path, shaka_output, forbid_comma=True) as guard:
            stream_name = stream_type if stream_type in ("video", "audio", "text") else "0"
            stream_spec = f"input={guard.safe_encrypted_path},stream={stream_name},output={guard.safe_output_path}"

            cmd = [
                self.shaka_packager_path,
                stream_spec,
                "--enable_raw_key_decryption",
                "--keys",
                ",".join(keys_arg),
            ]
            logger.info(f"Shaka cmd: {self._redacted_cmd(cmd)}")
            _shaka_t0 = time.monotonic()

            result = None
            for attempt in range(_OPEN_RETRY_ATTEMPTS):
                result = run_with_progress(
                    cmd,
                    label,
                    guard.safe_encrypted_path,
                    guard.safe_output_path,
                    engine_name="SHAKA",
                    progress_cb=progress_cb,
                    status=status,
                )
                if result is True:
                    guard.finalize()

                    if shaka_output != output_path and os.path.exists(shaka_output):
                        try:
                            os.replace(shaka_output, output_path)
                        except OSError:
                            try:
                                shutil.copy2(shaka_output, output_path)
                                os.remove(shaka_output)
                            except Exception as exc:
                                logger.error(f"Shaka output move failed after {time.monotonic() - _shaka_t0:.1f}s: {exc}")
                                return False

                    if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
                        logger.error(f"Shaka reported success but output is missing/empty after {time.monotonic() - _shaka_t0:.1f}s")
                        return False
                    logger.info(f"Shaka finished -> {os.path.basename(output_path)} in {time.monotonic() - _shaka_t0:.1f}s ({os.path.getsize(output_path)} bytes)")
                    return True

                stderr_text = result[1] if isinstance(result, tuple) else str(result)
                if attempt < _OPEN_RETRY_ATTEMPTS - 1 and _is_transient_open_error(stderr_text):
                    logger.warning(f"Shaka could not open input (attempt {attempt + 1}/{_OPEN_RETRY_ATTEMPTS}), retrying: {stderr_text}")
                    time.sleep(_open_retry_delay(attempt))
                    continue
                break

        stderr_msg = result[1] if isinstance(result, tuple) else "Unknown error"
        logger.error(f"Shaka failed after {time.monotonic() - _shaka_t0:.1f}s: {stderr_msg}")
        console.print(f"[red]Shaka failed: {stderr_msg}")
        return False

    def decrypt(
        self,
        encrypted_path: str,
        keys,
        output_path: str,
        stream_type: str = "video",
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
    ) -> bool:
        """Decrypt the given encrypted file using the provided keys and output to the specified path."""
        try:
            mode, kid, _pssh, _codec, enc_method, scheme, is_piff = self.detect_encryption(encrypted_path)
            norm_keys = KeysManager.normalize(keys)

            if mode is None:
                if not norm_keys:
                    logger.info("File appears clear and no keys provided: copying")
                    shutil.copy(encrypted_path, output_path)
                    return True
                
                if stream_type == "subtitle":
                    shutil.copy(encrypted_path, output_path)
                    return True
                
                mode = "unknown"

            norm_keys = KeysManager.resolve_fixed_key(encrypted_path, kid, norm_keys)
            if not norm_keys:
                logger.error("No valid keys available for decryption")
                return False

            norm_keys = KeysManager.resolve_placeholder_kid(kid, norm_keys)

            if kid and not KeysManager.is_zero_kid(kid):
                available_kids = {k.lower() for k, _ in norm_keys}
                if kid.lower() not in available_kids:
                    logger.error(f"No key matches required KID {kid} (have: {', '.join(k[:8] for k in available_kids)}); refusing to decrypt with mismatched keys")
                    return False

            method_display = (enc_method or mode or "unknown").upper().replace("_", "-")
            filename = os.path.basename(encrypted_path)
            is_fixed_key_shaka = KeysManager.is_zero_kid(kid)
            if is_piff:
                logger.debug("Legacy PIFF-brand content detected (dual PIFF+CENC)")
            
            # Try the preferred engine first, then fall through the rest of the priority
            # order on failure -- flux has shown intermittent, hard-to-reproduce sample
            # corruption under heavy real-pipeline concurrency (passes every isolated
            # repro, exit 0, right size, but ~99% decode-error content) that its own
            # subprocess-level checks don't always catch; shaka/bento4 decrypt the exact
            # same input cleanly every time, so retrying with them beats surfacing a
            # silently-corrupt track.
            for engine in self._engine_order(enc_method, scheme):
                if engine == "shaka":
                    label = f"[cyan]Dec[/cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]Shaka[/yellow]"
                    ok = self._decrypt_shaka_nonlive(
                        encrypted_path, norm_keys, output_path, stream_type, label,
                        is_fixed_key=is_fixed_key_shaka, progress_cb=progress_cb, status=method_display,
                    )
                elif engine == "bento4":
                    label = f"[cyan]Dec[/cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]Bento4[/yellow]"
                    ok = self._decrypt_bento4_nonlive(
                        encrypted_path, norm_keys, output_path, label,
                        is_fixed_key=is_fixed_key_shaka, progress_cb=progress_cb, status=method_display,
                    )
                else:
                    label = f"[cyan]Dec[/cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]flux[/yellow]"
                    ok = self._decrypt_flux_nonlive(
                        encrypted_path, norm_keys, output_path, label,
                        is_fixed_key=False, progress_cb=progress_cb, status=method_display, stream_type=stream_type,
                    )

                if ok:
                    return True

                logger.warning(f"{engine} failed/produced unusable output for {filename} -- trying next engine")

            if mode == "unknown":
                logger.error("Forced decryption failed in unknown mode; refusing to copy encrypted content as decrypted output.")
                return False

            return False

        except Exception as exc:
            logger.error(f"Decryption error: {exc}")
            console.print(f"[red]Decryption error: {exc}")
            return False

    def decrypt_file(
        self,
        encrypted_path: str,
        decrypted_path: str,
        keys,
        label: str,
        progress_cb: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple:
        """Decrypt a file using the provided keys and return a tuple indicating success and an optional error message."""
        norm_keys = KeysManager.normalize(keys)
        if not norm_keys:
            return False, "Could not parse any keys."

        mode, kid, _pssh, _codec, _enc_method, scheme, is_piff = self.detect_encryption(encrypted_path)
        norm_keys = KeysManager.resolve_fixed_key(encrypted_path, kid, norm_keys)

        method_display = (_enc_method or mode or "unknown").upper().replace("_", "-")
        filename = os.path.basename(encrypted_path)
        is_fixed_key_shaka = KeysManager.is_zero_kid(kid)
        if is_piff:
            logger.debug("Legacy PIFF-brand content detected (dual PIFF+CENC )")
        
        engine = self._engine_for(_enc_method, scheme)

        if engine == "shaka":
            engine_name = "Shaka"
            ok = self._decrypt_shaka_nonlive(
                encrypted_path, norm_keys, decrypted_path, "video",
                f"[bold cyan]Dec[/bold cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]Shaka[/yellow]",
                is_fixed_key=is_fixed_key_shaka, progress_cb=progress_cb,
            )
        elif engine == "bento4":
            engine_name = "Bento4"
            ok = self._decrypt_bento4_nonlive(
                encrypted_path, norm_keys, decrypted_path,
                f"[bold cyan]Dec[/bold cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]Bento4[/yellow]",
                is_fixed_key=is_fixed_key_shaka, progress_cb=progress_cb,
            )
        else:
            engine_name = "flux"
            ok = self._decrypt_flux_nonlive(
                encrypted_path, norm_keys, decrypted_path,
                f"[bold cyan]Dec[/bold cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]flux[/yellow]",
                is_fixed_key=False, progress_cb=progress_cb, status=method_display,
            )

        if ok:
            return True, None
        return False, f"{engine_name} decryption failed for {filename}"

    def decrypt_segment_live(
        self, encrypted_path: str, decrypted_path: str, raw_keys, init_path: str | None = None
    ) -> tuple:
        """Decrypt a live (streaming) encrypted segment using the provided keys and return a tuple indicating success and an optional error message."""
        norm_keys = KeysManager.normalize(raw_keys)

        if not self.flux_path:
            logger.debug(f"decrypt_segment_live(): {os.path.basename(encrypted_path)} -> {os.path.basename(decrypted_path)} [LIVE -> BENTO4 (flux unavailable)]")
            return self._decrypt_bento4_live(encrypted_path, decrypted_path, norm_keys, init_path=init_path)

        logger.debug(f"decrypt_segment_live(): {os.path.basename(encrypted_path)} -> {os.path.basename(decrypted_path)} [LIVE -> flux]")
        return self._decrypt_flux_live(encrypted_path, decrypted_path, norm_keys, init_path=init_path)
