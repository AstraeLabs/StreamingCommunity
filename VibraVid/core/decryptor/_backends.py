# 01.04.26

import logging
import os
import shutil
import subprocess
from typing import Callable, Dict, Any, Optional

from VibraVid.core.ui.bar_manager import console
from ._subprocess_runner import run_with_progress


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bento4
# ---------------------------------------------------------------------------
def decrypt_bento4_nonlive(mp4decrypt_path: str, encrypted_path: str, normalized_keys: list[tuple[str, str]], output_path: str, label: str, is_fixed_key: bool = False, progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None) -> bool:
    """
    Decrypt *encrypted_path* with ``mp4decrypt``.

    When *is_fixed_key* is ``True``, the KID is zeroed out so Bento4 treats
    it as a fixed (keyid-less) stream.

    Returns ``True`` on success.
    """
    cmd = [mp4decrypt_path]

    pairs = normalized_keys
    if is_fixed_key and normalized_keys:
        _, key_hex = normalized_keys[0]
        pairs = [("00000000000000000000000000000000", key_hex)]

    for kid, key in pairs:
        cmd.extend(["--key", f"{kid.lower()}:{key.lower()}"])
    cmd.extend([encrypted_path, output_path])

    logger.info(f"Bento4 cmd: {' '.join(cmd)}")
    result = run_with_progress(cmd, label, encrypted_path, output_path, progress_cb=progress_cb)
    if result is True:
        return True

    stderr_msg = result[1] if isinstance(result, tuple) else "Unknown error"
    logger.error(f"Bento4 failed: {stderr_msg}")
    console.print(f"[red]Bento4 failed: {stderr_msg}")
    return False


def decrypt_bento4_live(mp4decrypt_path: str, encrypted_path: str, decrypted_path: str, normalized_keys: list[tuple[str, str]], init_path: Optional[str] = None) -> tuple:
    """
    Decrypt a single live DASH fragment with ``mp4decrypt``.

    Returns ``(ok: bool, message: str, data: bytes | None)``.
    """
    logger.debug(f"decrypt_bento4_live(): {os.path.basename(encrypted_path)} -> {os.path.basename(decrypted_path)}")
    try:
        cmd = [mp4decrypt_path]
        if init_path and os.path.exists(init_path):
            cmd.extend(["--fragments-info", init_path])

        if not normalized_keys:
            logger.error("Bento4 live decryption requested without usable keys")
            return False, "Error Bento4: no usable keys", None

        for kid, raw_key in normalized_keys:
            cmd.extend(["--key", f"{kid}:{raw_key}"])
        
        cmd.extend([encrypted_path, decrypted_path])
        logger.debug(f"Bento4 live cmd: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True)
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


# ---------------------------------------------------------------------------
# Shaka Packager
# ---------------------------------------------------------------------------
def decrypt_shaka_nonlive(shaka_packager_path: str, encrypted_path: str, normalized_keys: list[tuple[str, str]], output_path: str, _stream_type: str, label: str, is_fixed_key: bool = False, progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None) -> bool:
    """
    Decrypt *encrypted_path* with Shaka Packager (handles SAMPLE-AES / CBCS).

    Returns ``True`` on success.
    """
    keys_arg: list[str] = []
    for idx, (kid, key) in enumerate(normalized_keys, start=1):
        shaka_kid = "00000000000000000000000000000000" if is_fixed_key else kid
        keys_arg.append(f"label={idx}:key_id={shaka_kid.lower()}:key={key.lower()}")

    shaka_output = output_path
    if not output_path.lower().endswith((".mp4", ".m4v", ".mpd")):
        shaka_output = output_path + ".tmp.mp4"

    stream_spec = f"input={encrypted_path},stream=0,output={shaka_output}"
    cmd = [
        shaka_packager_path,
        stream_spec,
        "--enable_raw_key_decryption",
        "--keys",
        " ".join(keys_arg),
    ]

    logger.info(f"Shaka cmd: {' '.join(cmd)}")
    result = run_with_progress(cmd, label, encrypted_path, shaka_output, progress_cb=progress_cb)
    if result is True:
        if shaka_output != output_path and os.path.exists(shaka_output):
            shutil.move(shaka_output, output_path)
        return True

    stderr_msg = result[1] if isinstance(result, tuple) else "Unknown error"
    logger.error(f"Shaka failed: {stderr_msg}")
    console.print(f"[red]Shaka failed: {stderr_msg}")
    return False