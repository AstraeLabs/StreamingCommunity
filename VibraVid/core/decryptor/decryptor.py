# 01.04.26

import logging
import os
import shutil
from typing import Any, Callable, Dict, Optional

from VibraVid.setup import (get_bento4_decrypt_path, get_ffmpeg_path, get_mp4dump_path, get_shaka_packager_path,)
from VibraVid.core.ui.bar_manager import console

from ._models import SCHEME_TO_MODE
from .keys_manager import normalize_keys, is_zero_kid, resolve_fixed_key_if_needed
from ._mp4_inspector import detect_encryption_info
from ._backends import (decrypt_bento4_nonlive, decrypt_bento4_live, decrypt_shaka_nonlive)


logger = logging.getLogger(__name__)


class Decryptor:
    def __init__(self, license_url: str = None, drm_type: str = None, **_kwargs) -> None:
        logger.debug(f"Initializing Decryptor license_url={license_url!r} drm_type={drm_type!r}")
        self.mp4decrypt_path = get_bento4_decrypt_path()
        self.mp4dump_path = get_mp4dump_path()
        self.shaka_packager_path  = get_shaka_packager_path()
        self.ffmpeg_path = get_ffmpeg_path()
        self.license_url = license_url
        self.drm_type = drm_type

    def detect_encryption(self, file_path: str) -> tuple:
        """
        Probe *file_path* and return ``(mode, kid, pssh_b64, codec, enc_method)``.
        All values are ``None`` when the file is unencrypted.
        """
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

    def decrypt(self, encrypted_path: str, keys, output_path: str, stream_type: str = "video", progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None) -> bool:
        """
        Decrypt *encrypted_path* → *output_path* after download is complete.

        Automatically picks Shaka (for SAMPLE-AES/CBCS) or Bento4 (CENC/CTR).
        Returns ``True`` on success.
        """
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

            method_display = (mode or "unknown").upper()
            filename = os.path.basename(encrypted_path)
            use_shaka = bool(enc_method and "sample" in enc_method.lower())

            if use_shaka:
                label = (f"[cyan]Dec[/cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]Shaka[/yellow]")
                ok = decrypt_shaka_nonlive(
                    self.shaka_packager_path,
                    encrypted_path,
                    norm_keys,
                    output_path,
                    stream_type,
                    label,
                    is_fixed_key=is_zero_kid(kid),
                    progress_cb=progress_cb,
                )
            
            else:
                label = (f"[cyan]Dec[/cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]Bento4[/yellow]")
                ok = decrypt_bento4_nonlive(
                    self.mp4decrypt_path,
                    encrypted_path,
                    norm_keys,
                    output_path,
                    label,
                    is_fixed_key=is_zero_kid(kid),
                    progress_cb=progress_cb,
                )

            if ok:
                logger.info(f"Decryption successful: {os.path.basename(output_path)}")
                return True

            if mode == "unknown":
                # Preserve legacy behaviour: fallback copy for force-attempt path.
                logger.error("Forced decryption failed; copying input to output.")
                shutil.copy(encrypted_path, output_path)
                return True

            return False

        except Exception as exc:
            logger.error(f"Decryption error: {exc}")
            console.print(f"[red]Decryption error: {exc}")
            return False

    def decrypt_file(self, encrypted_path: str, decrypted_path: str, keys, label: str, progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None) -> tuple:
        """
        Manual-downloader API.  Returns ``(success: bool, error_message: str | None)``.
        """
        norm_keys = normalize_keys(keys)
        if not norm_keys:
            return False, "Could not parse any keys."

        mode, kid, _pssh, _codec, _enc_method = self.detect_encryption(encrypted_path)
        norm_keys = resolve_fixed_key_if_needed(encrypted_path, kid, norm_keys)

        method_display = (mode or "unknown").upper()
        filename = os.path.basename(encrypted_path)
        rich_label = (f"[bold cyan]Dec[/bold cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]Bento4[/yellow]")

        ok = decrypt_bento4_nonlive(
            self.mp4decrypt_path,
            encrypted_path,
            norm_keys,
            decrypted_path,
            rich_label,
            is_fixed_key=is_zero_kid(kid),
            progress_cb=progress_cb,
        )

        if ok:
            return True, None

        return False, f"Bento4 decryption failed for {filename}"

    def decrypt_segment_live(self, encrypted_path: str, decrypted_path: str, raw_keys, init_path: Optional[str] = None) -> tuple:
        """
        Decrypt one live DASH fragment.

        Returns ``(ok: bool, message: str, data: bytes | None)``.
        """
        logger.debug(f"decrypt_segment_live(): {os.path.basename(encrypted_path)} -> {os.path.basename(decrypted_path)} [LIVE -> BENTO4]")
        norm_keys = normalize_keys(raw_keys)
        
        return decrypt_bento4_live(
            self.mp4decrypt_path,
            encrypted_path,
            decrypted_path,
            norm_keys,
            init_path=init_path,
        )