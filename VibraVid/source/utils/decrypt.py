# 19.05.25

import os
import re
import base64
import subprocess
import shutil
import logging

from rich.console import Console

try:
    from Cryptodome.Cipher import AES
    from Cryptodome.Util.Padding import unpad
except Exception:
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import unpad
    except Exception:
        logging.warning("PyCryptodome not found, HLS segment decryption will not work. Install with 'pip install pycryptodome' for AES-128-CBC support.")

from VibraVid.setup import (get_bento4_decrypt_path, get_mp4dump_path, get_shaka_packager_path)
from VibraVid.utils.vault import obj_externalSupaDbVault


console = Console()
logger = logging.getLogger(__name__)


class Decryptor:
    def __init__(self, preference: str = "bento4", license_url: str = None, drm_type: str = None):
        logger.info(f"Initializing Decryptor with preference: {preference}, license_url: {license_url}, drm_type: {drm_type}")
        self.preference = preference.lower()
        self.mp4decrypt_path = get_bento4_decrypt_path()
        self.mp4dump_path = get_mp4dump_path()
        self.shaka_packager_path = get_shaka_packager_path()
        self.license_url = license_url
        self.drm_type = drm_type
        self.is_supa_db_connected = obj_externalSupaDbVault is not None

    def detect_encryption(self, file_path):
        """Detect encryption scheme using mp4dump. Returns tuple: (scheme, kid, pssh)."""
        logger.info(f"Detecting encryption: {os.path.basename(file_path)}")
        kid = None
        pssh = None

        try:
            cmd = [self.mp4dump_path, file_path]
            logger.info(f"Running command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

            if result.returncode != 0:
                return None, None, None

            output = result.stdout

            # Extract KID if available
            kid_match = re.search(r"default_KID\s*=\s*\[([\da-fA-F\s]+)\]", output, re.IGNORECASE)
            if kid_match:
                kid_raw = kid_match.group(1)
                kid = re.sub(r"\s+", "", kid_raw).lower()
                logger.info(f"Found KID: {kid}")

            # Extract PSSH (in hex format like [AB CD EF ...] or base64)
            pssh_match = re.search(r"pssh\s*=\s*\[((?:[0-9a-fA-F]{2}\s*)+)\]", output, re.IGNORECASE)
            if pssh_match:
                hex_str = pssh_match.group(1)
                hex_clean = re.sub(r"\s+", "", hex_str)
                logger.info(f"Found PSSH hex: {hex_clean}")
                try:
                    pssh_bytes = bytes.fromhex(hex_clean)
                    pssh = base64.b64encode(pssh_bytes).decode("utf-8")
                    console.print(f"[dim]PSSH: {pssh[:50]}...")
                except Exception:
                    logger.error(f"Could not convert PSSH hex to base64: {hex_clean[:100]}")

            # 1. Search for scheme_type
            scheme_match = re.search(r"scheme_type\s*=\s*(\w+)", output, re.IGNORECASE)
            if scheme_match:
                scheme = scheme_match.group(1).lower()
                logger.info(f"Found scheme_type: {scheme}")
                if scheme in ["cenc", "cens"]:
                    return "ctr", kid, pssh
                elif scheme in ["cbcs", "cbc1"]:
                    return "cbc", kid, pssh

            # 2. Search for isProtected=1
            protected_match = re.search(r"default_isProtected\s*=\s*1", output, re.IGNORECASE)
            if protected_match:
                console.print("[dim]Found isProtected=1 but no scheme_type. Defaulting to CTR mode.")
                return "ctr", kid, pssh

            # 3. Search for sinf box
            if "[sinf]" in output:
                console.print("[dim]Found sinf box, indicating encryption. Defaulting to CTR mode.")
                return "ctr", kid, pssh

            # 4. Search for frma
            frma_match = re.search(r"original_format\s*=\s*(\w+)", output, re.IGNORECASE)
            if frma_match and frma_match.group(1) != output.split()[-1]:
                console.print("[dim]Found original_format different from file type, indicating encryption. Defaulting to CTR mode.")
                return "ctr", kid, pssh

            logger.info("No encryption indicators found")
            return None, None, None

        except Exception as e:
            logger.error(f"Encryption detection failed for {file_path}: {e}")
            return None, None, None

    def decrypt(self, encrypted_path, keys, output_path, stream_type: str = "video"):
        """Decrypt a file using the preferred method. Returns True on success."""
        logger.info(f"Starting decryption for: {os.path.basename(encrypted_path)} with keys: {keys} and stream_type: {stream_type}")
        try:
            logger.info(f"Decrypting file: {os.path.basename(encrypted_path)} as {stream_type}")
            encryption_scheme, kid, pssh = self.detect_encryption(encrypted_path)
            if encryption_scheme is None:
                logger.info(f"File {os.path.basename(encrypted_path)} is not encrypted, copying to destination.")
                shutil.copy(encrypted_path, output_path)
                return True

            if isinstance(keys, str):
                keys = [keys]

            # Validate that at least one key matches the detected KID
            if kid:
                key_kids = []
                for single_key in keys:
                    if ":" in single_key:
                        key_kid, _ = single_key.split(":", 1)
                        key_kids.append(key_kid.lower())
                    else:
                        key_kids.append(single_key.lower())

                if key_kids and kid.lower() not in key_kids:
                    error_msg = f"Detected KID ({kid}) does not match any provided key KIDs ({key_kids})"
                    logger.error(error_msg)
                    console.print(f"[red]Error: {error_msg}")

                    # Mark mismatched keys as invalid in Supabase
                    if self.is_supa_db_connected:
                        for key_kid in key_kids:
                            self._mark_key_invalid(key_kid, pssh)

                    console.print("[red]File cannot be decrypted - wrong key for this content")
                    return False

            else:
                logger.error("No KID detected in the file, cannot validate keys. Proceeding with decryption attempt but results may vary.")

            logger.info(f"Decrypting {os.path.basename(encrypted_path)} ({encryption_scheme.upper()}) using {self.preference}...")
            console.print(f"[dim]Decrypting [cyan]{os.path.basename(encrypted_path)}[/cyan] ({encryption_scheme.upper()}) with {self.preference}...")

            if self.preference == "shaka" and self.shaka_packager_path:
                result = self._decrypt_shaka(encrypted_path, keys, output_path, stream_type)
            else:
                result = self._decrypt_bento4(encrypted_path, keys, output_path)

            # Mark key as valid if decryption succeeded
            if result:
                logger.info(f"Decryption successful: {os.path.basename(output_path)}")
                if kid and self.is_supa_db_connected:
                    self._mark_key_valid(kid, pssh)
            else:
                logger.error(f"Decryption failed for {os.path.basename(encrypted_path)}")

            return result

        except Exception as e:
            logger.error(f"Decryption error: {e}")
            console.print(f"[red]Decryption error: {e}.")
            return False

    def _mark_key_invalid(self, kid: str, pssh: str = None):
        """Mark a key as invalid for this specific license URL (and PSSH if provided) in Supabase Vault."""
        if self.is_supa_db_connected:
            if obj_externalSupaDbVault.update_key_validity(kid, False, self.license_url, self.drm_type, pssh):
                console.print(f"[yellow]Marked key {kid} as invalid in Supabase")

    def _mark_key_valid(self, kid: str, pssh: str = None):
        """Mark a key as valid for this specific license URL (and PSSH if provided) in Supabase Vault after successful decryption."""
        if self.is_supa_db_connected:
            if obj_externalSupaDbVault.update_key_validity(kid, True, self.license_url, self.drm_type, pssh):
                logger.info(f"Marked key {kid} as valid")

    def _decrypt_bento4(self, encrypted_path, keys, output_path):
        """Decrypt a file using Bento4. Returns True on success."""
        cmd = [self.mp4decrypt_path]
        logger.info(f"Preparing Bento4 command with keys: {keys}, output: {output_path}, encrypted file: {encrypted_path}")

        for single_key in keys:
            if ":" in single_key:
                kid, key_val = single_key.split(":", 1)
                cmd.extend(["--key", f"{kid.lower()}:{key_val.lower()}"])
            else:
                cmd.extend(["--key", single_key.lower()])

        cmd.extend([encrypted_path, output_path])
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if (result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000):
            return True
        else:
            console.print(f"[red]Bento4 Decryption failed: {result.stderr.strip()}.")
            logger.error(f"Bento4 decryption failed: {result.stderr.strip()}")
            return False

    def _decrypt_shaka(self, encrypted_path, keys, output_path, stream_type):
        """Decrypt a file using Shaka Packager. Returns True on success."""
        cmd = [self.shaka_packager_path]
        logger.info(f"Preparing Shaka Packager command for stream type: {stream_type} with keys: {keys}, output: {output_path}, encrypted file: {encrypted_path}")

        # Build stream specifier
        stream_spec = (f"input='{encrypted_path}',stream={stream_type},output='{output_path}'")
        cmd.append(stream_spec)
        cmd.append("--enable_fixed_key_decryption")

        keys_arg = []
        for single_key in keys:
            if ":" in single_key:
                kid, key_val = single_key.split(":", 1)
                keys_arg.append(f"key_id={kid.lower()}:key={key_val.lower()}")
            else:
                keys_arg.append(f"key={single_key.lower()}")

        if keys_arg:
            cmd.extend(["--keys", ",".join(keys_arg)])

        logger.info(f"Running command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if (result.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000):
            return True
        else:
            if "stream=" in stream_spec:
                logger.error("Shaka decryption failed with stream type, retrying without it...")
                cmd_retry = [
                    self.shaka_packager_path,
                    f"input='{encrypted_path}',output='{output_path}'",
                    "--enable_fixed_key_decryption",
                ]
                if keys_arg:
                    cmd_retry.extend(["--keys", ",".join(keys_arg)])

                result_retry = subprocess.run(cmd_retry, capture_output=True, text=True, timeout=300)
                if result_retry.returncode == 0 and os.path.exists(output_path):
                    return True

            console.print(f"[red]Shaka Decryption failed: {result.stderr.strip()}.")
            logger.error(f"Shaka decryption failed: {result.stderr.strip()}")
            return False

    def decrypt_hls_segment(self, encrypted_path, key_data, iv, output_path):
        """Decrypt an HLS segment using AES-128-CBC. Returns True on success."""
        logger.info(f"Decrypting HLS segment: {os.path.basename(encrypted_path)}")

        try:
            with open(encrypted_path, "rb") as f:
                encrypted_data = f.read()

            iv_bytes = bytes.fromhex(iv)
            cipher = AES.new(key_data, AES.MODE_CBC, iv_bytes)
            decrypted_data = cipher.decrypt(encrypted_data)
            decrypted_data = unpad(decrypted_data, AES.block_size)

            with open(output_path, "wb") as f:
                f.write(decrypted_data)
            return True

        except Exception as e:
            logger.exception(f"HLS segment decryption error: {e}")
            return False