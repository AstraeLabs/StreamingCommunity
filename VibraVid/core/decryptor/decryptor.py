# 01.04.26

import logging
import os
import shutil
import subprocess
from typing import Any, Callable, Dict, Optional

from VibraVid.setup import get_bento4_decrypt_path, get_ffmpeg_path, get_mp4dump_path, get_shaka_packager_path
from VibraVid.core.ui.bar_manager import console

from ._subprocess_runner import run_with_progress
from ._models import SCHEME_TO_MODE
from .keys_manager import normalize_keys, is_zero_kid, resolve_fixed_key_if_needed
from ._mp4_inspector import detect_encryption_info


logger = logging.getLogger(__name__)


class Decryptor:
    def __init__(self, license_url: str = None, drm_type: str = None, **_kwargs) -> None:
        logger.debug(f"Initializing Decryptor license_url={license_url!r} drm_type={drm_type!r}")
        self.mp4decrypt_path = get_bento4_decrypt_path()
        self.mp4dump_path = get_mp4dump_path()
        self.shaka_packager_path = get_shaka_packager_path()
        self.ffmpeg_path = get_ffmpeg_path()
        self.license_url = license_url
        self.drm_type = drm_type

    @staticmethod
    def _redacted_cmd(cmd: list[str]) -> str:
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

    def detect_encryption(self, file_path: str) -> tuple:
        logger.debug(f"Detecting encryption: {os.path.basename(file_path)}")
        info = detect_encryption_info(self.mp4dump_path, file_path)

        if not info.encrypted:
            logger.info("No encryption indicators found")
            return None, None, None, None, None

        mode = SCHEME_TO_MODE.get(info.scheme or "")
        if mode is None:
            mode = "ctr"
            console.print("[dim]Encryption detected (no explicit scheme). Defaulting to CTR mode.")

        logger.debug(f"Encryption finalized: scheme={info.scheme}, mode={mode}, kid={info.kid}, codec={info.video_codec}, enc_method={info.encryption_method}")
        return mode, info.kid, info.pssh_b64, info.video_codec, info.encryption_method

    def _decrypt_bento4_nonlive(self, encrypted_path: str, normalized_keys: list[tuple[str, str]], output_path: str, label: str, is_fixed_key: bool = False, progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None) -> bool:
        cmd = [self.mp4decrypt_path]

        pairs = normalized_keys
        if is_fixed_key and normalized_keys:
            _, key_hex = normalized_keys[0]
            pairs = [("00000000000000000000000000000000", key_hex)]

        for kid, key in pairs:
            cmd.extend(["--key", f"{kid.lower()}:{key.lower()}"])
        cmd.extend([encrypted_path, output_path])

        logger.info(f"Bento4 cmd: {self._redacted_cmd(cmd)}")
        result = run_with_progress(cmd, label, encrypted_path, output_path, progress_cb=progress_cb)
        if result is True:
            if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
                logger.error("Bento4 reported success but output is missing/empty")
                return False
            return True

        logger.error(f"Bento4 failed: {result}")
        console.print(f"[red]Bento4 failed: {result}")
        return False

    def _decrypt_bento4_live(self, encrypted_path: str, decrypted_path: str, normalized_keys: list[tuple[str, str]], init_path: Optional[str] = None) -> tuple:
        logger.debug(f"decrypt_bento4_live(): {os.path.basename(encrypted_path)} -> {os.path.basename(decrypted_path)}")
        try:
            cmd = [self.mp4decrypt_path]
            if init_path and os.path.exists(init_path):
                cmd.extend(["--fragments-info", init_path])

            if not normalized_keys:
                logger.error("Bento4 live decryption requested without usable keys")
                return False, "Error Bento4: no usable keys", None

            for kid, raw_key in normalized_keys:
                cmd.extend(["--key", f"{kid}:{raw_key}"])
            cmd.extend([encrypted_path, decrypted_path])
            logger.debug(f"Bento4 live cmd: {self._redacted_cmd(cmd)}")

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if result.returncode != 0:
                msg = result.stderr.strip() if result.stderr else "Unknown error"
                logger.error(f"Bento4 live decryption failed: {msg}")
                return False, f"Error Bento4: {msg}", None

            if not os.path.exists(decrypted_path):
                return False, "Error Bento4: output file missing", None

            with open(decrypted_path, "rb") as f:
                data = f.read()

            if not data:
                return False, "Error Bento4: empty output", None

            logger.debug(f"Bento4 live segment decrypted successfully: {len(data)} bytes")
            return True, "Bento4 live segment decrypted", data

        except Exception as exc:
            logger.error(f"Exception Bento4 live: {exc}")
            return False, f"Exception Bento4: {exc}", None

    def _decrypt_shaka_nonlive(self, encrypted_path: str, normalized_keys: list[tuple[str, str]], output_path: str, stream_type: str, label: str, is_fixed_key: bool = False, progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None) -> bool:
        keys_arg: list[str] = []
        for idx, (kid, key) in enumerate(normalized_keys, start=1):
            shaka_kid = "00000000000000000000000000000000" if is_fixed_key else kid
            keys_arg.append(f"label={idx}:key_id={shaka_kid.lower()}:key={key.lower()}")

        shaka_output = output_path
        if not output_path.lower().endswith((".mp4", ".m4v", ".mpd")):
            shaka_output = output_path + ".tmp.mp4"

        stream_name = stream_type if stream_type in ("video", "audio", "text") else "0"
        stream_spec = f"input={encrypted_path},stream={stream_name},output={shaka_output}"
        cmd = [
            self.shaka_packager_path,
            stream_spec,
            "--enable_raw_key_decryption",
            "--keys",
            ",".join(keys_arg),
        ]

        logger.info(f"Shaka cmd: {self._redacted_cmd(cmd)}")
        result = run_with_progress(cmd, label, encrypted_path, shaka_output, progress_cb=progress_cb)
        if result is True:
            if shaka_output != output_path and os.path.exists(shaka_output):
                try:
                    os.replace(shaka_output, output_path)
                except OSError:
                    try:
                        shutil.copy2(shaka_output, output_path)
                        os.remove(shaka_output)
                    except Exception as exc:
                        logger.error(f"Shaka output move failed: {exc}")
                        return False

            if not os.path.exists(output_path) or os.path.getsize(output_path) <= 0:
                logger.error("Shaka reported success but output is missing/empty")
                return False
            return True

        stderr_msg = result[1] if isinstance(result, tuple) else "Unknown error"
        logger.error(f"Shaka failed: {stderr_msg}")
        console.print(f"[red]Shaka failed: {stderr_msg}")
        return False

    def decrypt(self, encrypted_path: str, keys, output_path: str, stream_type: str = "video", progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None) -> bool:
        logger.info(f"decrypt(): {os.path.basename(encrypted_path)} stream={stream_type} keys={keys} [NON-LIVE]")
        try:
            mode, kid, _pssh, _codec, enc_method = self.detect_encryption(encrypted_path)
            norm_keys = normalize_keys(keys)

            if mode is None:
                if not norm_keys:
                    logger.info("File appears clear and no keys provided: copying")
                    shutil.copy(encrypted_path, output_path)
                    return True
                mode = "unknown"

            norm_keys = resolve_fixed_key_if_needed(encrypted_path, kid, norm_keys)
            if not norm_keys:
                logger.error("No valid keys available for decryption")
                return False

            method_display = (enc_method or mode or "unknown").upper().replace("_", "-")
            filename = os.path.basename(encrypted_path)
            use_shaka = bool((enc_method and "sample" in enc_method.lower()) or mode == "cbc")

            if use_shaka and self.shaka_packager_path:
                label = f"[cyan]Dec[/cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]Shaka[/yellow]"
                ok = self._decrypt_shaka_nonlive(
                    encrypted_path, norm_keys, output_path,
                    stream_type, label, is_fixed_key=is_zero_kid(kid), progress_cb=progress_cb,
                )
            else:
                if use_shaka:
                    logger.warning("CBCS/SAMPLE-AES detected but Shaka Packager not available — falling back to Bento4")
                
                label = f"[cyan]Dec[/cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]Bento4[/yellow]"
                ok = self._decrypt_bento4_nonlive(
                    encrypted_path, norm_keys, output_path,
                    label, is_fixed_key=is_zero_kid(kid), progress_cb=progress_cb,
                )

            if ok:
                logger.info(f"Decryption successful: {os.path.basename(output_path)}")
                return True

            if mode == "unknown":
                logger.error("Forced decryption failed in unknown mode; refusing to copy encrypted content as decrypted output.")
                return False

            return False

        except Exception as exc:
            logger.error(f"Decryption error: {exc}")
            console.print(f"[red]Decryption error: {exc}")
            return False

    def decrypt_file(self, encrypted_path: str, decrypted_path: str, keys, label: str, progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None) -> tuple:
        norm_keys = normalize_keys(keys)
        if not norm_keys:
            return False, "Could not parse any keys."

        mode, kid, _pssh, _codec, _enc_method = self.detect_encryption(encrypted_path)
        norm_keys = resolve_fixed_key_if_needed(encrypted_path, kid, norm_keys)

        method_display = (_enc_method or mode or "unknown").upper().replace("_", "-")
        filename = os.path.basename(encrypted_path)
        rich_label = f"[bold cyan]Dec[/bold cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]Bento4[/yellow]"

        ok = self._decrypt_bento4_nonlive(
            encrypted_path, norm_keys, decrypted_path,
            rich_label, is_fixed_key=is_zero_kid(kid), progress_cb=progress_cb,
        )

        if ok:
            return True, None
        return False, f"Bento4 decryption failed for {filename}"

    def decrypt_segment_live(self, encrypted_path: str, decrypted_path: str, raw_keys, init_path: Optional[str] = None) -> tuple:
        logger.debug(f"decrypt_segment_live(): {os.path.basename(encrypted_path)} -> {os.path.basename(decrypted_path)} [LIVE -> BENTO4]")
        norm_keys = normalize_keys(raw_keys)
        return self._decrypt_bento4_live(encrypted_path, decrypted_path, norm_keys, init_path=init_path)
