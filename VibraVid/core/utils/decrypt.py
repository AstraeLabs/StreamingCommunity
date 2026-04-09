# 19.05.25

import os
import re
import time
import struct
import json
import shutil
import subprocess
import logging
import threading
from io import StringIO
from typing import Optional

from rich.console import Console

from VibraVid.setup import get_bento4_decrypt_path, get_mp4dump_path, get_shaka_packager_path, get_ffmpeg_path


console = Console()
logger = logging.getLogger(__name__)
_WIDEVINE_SYSTEM_ID  = "edef8ba979d64acea3c827dcd51d21ed"
_PLAYREADY_SYSTEM_ID = "9a04f07998404286ab92e65be0885f95"
_SCHEME_TO_MODE = {
    "cenc": "ctr",
    "cens": "ctr",
    "cbcs": "cbc",
    "cbc1": "cbc",
}

def _render_bar(percent: int, length: int = 10) -> str:
    """Return a Rich-markup progress bar like [dim][[/dim][green]========[/green][dim]--] 81%[/dim]"""
    filled = int((percent / 100) * length)
    bar = (
        "[dim][[/dim]"
        + f"[green]{'=' * filled}[/green]"
        + f"[dim]{'-' * (length - filled)}[/dim]"
        + "[dim]][/dim]"
    )
    return f"{bar} [dim]{percent:3d}%[/dim]"


def _render_markup(text: str) -> str:
    """Render Rich markup into a string with ANSI codes."""
    buf = StringIO()
    temp_console = Console(file=buf, force_terminal=True)
    temp_console.print(text, end="")
    return buf.getvalue()


def _run_with_progress(cmd: list, label: str, encrypted_path: str, output_path: str) -> tuple:
    """
    Run *cmd* via Popen, monitor the size of *output_path* relative to
    *encrypted_path* and print an inline progress bar using Rich markup.

    The label should already contain the full description, e.g.:
        "Decrypting movie.mp4 using shaka method ctr"

    Returns:
        True          on success (returncode 0 and output > 1000 bytes)
        (False, msg)  on failure — caller decides how to handle
    """
    file_size = os.path.getsize(encrypted_path) if os.path.isfile(encrypted_path) else 0
    progress_percent = 0
    stop_monitor = threading.Event()

    def _monitor():
        nonlocal progress_percent
        while not stop_monitor.is_set():
            if os.path.exists(output_path) and file_size > 0:
                current_size = os.path.getsize(output_path)
                progress_percent = min(int((current_size / file_size) * 100), 99)
            time.sleep(0.15)

    monitor_thread = threading.Thread(target=_monitor, daemon=True)
    monitor_thread.start()

    stderr_lines = []
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Collect stderr in background so it doesn't block
        def _read_stderr():
            for line in process.stderr:
                stderr_lines.append(line)

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        # Refresh bar while process runs — \r keeps it on the same line
        while process.poll() is None:
            output = _render_markup(f"{label} {_render_bar(progress_percent)}")
            console.file.write(f"\r{output}")
            console.file.flush()
            time.sleep(0.1)

        process.wait()
        stderr_thread.join(timeout=2)

    except Exception as e:
        stop_monitor.set()
        console.print()  # newline after bar
        logger.error(f"Process execution failed: {e}")
        return False, str(e)
    finally:
        stop_monitor.set()

    # Final bar — 100 % on success, actual value on failure
    final_pct = 100 if process.returncode == 0 else progress_percent
    output = _render_markup(f"{label} {_render_bar(final_pct)}")
    console.file.write(f"\r{output}\n")
    console.file.flush()

    # Let the OS flush the output file to disk
    time.sleep(0.3)

    if process.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
        return True

    stderr_text = "".join(stderr_lines).strip()
    logger.error(f"Decryption process failed (rc={process.returncode}): {stderr_text}")
    return False, stderr_text


def _run_without_progress(cmd: list, output_path: str) -> tuple:
    """
    Run *cmd* via subprocess.run (no interactive progress).
    Returns (True, "") on success, (False, stderr) on failure.
    Used for Shaka non-live decryption where file-size monitoring is unreliable.
    """
    try:
        process = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if process.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            return True, ""
        return False, process.stderr if process.stderr else "Unknown error"
    except Exception as e:
        logger.error(f"Process execution failed: {e}")
        return False, str(e)

class KeysManager:
    def __init__(self, keys=None):
        self._keys = []
        if keys:
            self.add_keys(keys)

    def add_keys(self, keys):
        if isinstance(keys, str):
            for k in keys.split('|'):
                k = k.strip()
                if ':' in k:
                    kid, key = k.split(':', 1)
                    self._keys.append((kid.strip(), key.strip()))

        elif isinstance(keys, list):
            for k in keys:
                if isinstance(k, str):
                    k = k.strip()
                    if ':' in k:
                        kid, key = k.split(':', 1)
                        self._keys.append((kid.strip(), key.strip()))
                elif isinstance(k, dict):
                    kid = k.get('kid', '')
                    key = k.get('key', '')
                    if kid and key:
                        self._keys.append((kid.strip(), key.strip()))

    def get_keys_list(self):
        return [f"{kid}:{key}" for kid, key in self._keys]

    def __len__(self):
        return len(self._keys)

    def __iter__(self):
        return iter(self._keys)

    def __getitem__(self, index):
        return self._keys[index]

    def __bool__(self):
        return len(self._keys) > 0


class Decryptor:
    def __init__(self, preference: str = "bento4", license_url: str = None, drm_type: str = None):
        logger.info(f"Initializing Decryptor preference={preference!r} license_url={license_url!r} drm_type={drm_type!r}")
        self.preference = preference.lower()
        self.mp4decrypt_path     = get_bento4_decrypt_path()
        self.mp4dump_path        = get_mp4dump_path()
        self.shaka_packager_path = get_shaka_packager_path()
        self.ffmpeg_path         = get_ffmpeg_path()
        self.license_url = license_url
        self.drm_type    = drm_type

    def detect_encryption(self, file_path):
        """Detect encryption scheme. Returns (mode, kid, pssh_b64) or (None, None, None)."""
        logger.info(f"Detecting encryption: {os.path.basename(file_path)}")

        result = self._run_mp4dump(file_path, fmt="json")
        if result:
            info = self._parse_json_dump(result)
            if info["encrypted"]:
                logger.info(f"JSON parse OK → scheme={info['scheme']} kid={info['kid']}")
                return self._finalize(info)

        result = self._run_mp4dump(file_path, fmt="text")
        if result:
            info = self._parse_text_dump(result)
            if info["encrypted"]:
                logger.info(f"Text parse OK → scheme={info['scheme']} kid={info['kid']}")
                return self._finalize(info)

        info = self._parse_binary(file_path)
        if info["encrypted"]:
            logger.info(f"Binary parse OK → scheme={info['scheme']} kid={info['kid']}")
            return self._finalize(info)

        logger.info("No encryption indicators found")
        return None, None, None

    def _run_mp4dump(self, file_path, fmt="json"):
        try:
            cmd = [self.mp4dump_path, "--verbosity", "0", "--format", fmt, file_path]
            logger.info(f"mp4dump cmd: {' '.join(cmd)}")
            r = subprocess.run(cmd, capture_output=True, timeout=15)
            raw = r.stdout
            for enc in ("utf-8", "utf-16", "utf-16-le", "latin-1"):
                try:
                    text = raw.decode(enc).lstrip("\ufeff")
                    return text if text.strip() else None
                except (UnicodeDecodeError, ValueError):
                    continue
        except Exception as e:
            logger.error(f"mp4dump ({fmt}) failed: {e}")
        return None

    def _parse_json_dump(self, text):
        info = {"encrypted": False, "scheme": None, "kid": None, "pssh_boxes": []}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return info

        pssh_boxes = self._find_boxes_by_name(data, "pssh")
        tenc_boxes = self._find_boxes_by_name(data, "tenc")
        sinf_boxes = self._find_boxes_by_name(data, "sinf")
        schm_boxes = self._find_boxes_by_name(data, "schm")
        saio_boxes = self._find_boxes_by_name(data, "saio")
        saiz_boxes = self._find_boxes_by_name(data, "saiz")
        trex_boxes = self._find_boxes_by_name(data, "trex")

        for box in tenc_boxes + trex_boxes:
            if "default_KID" in box:
                info["kid"] = self._clean_kid(box["default_KID"])
                break

        for schm in schm_boxes:
            if "scheme_type" in schm:
                info["scheme"] = schm["scheme_type"].lower()
                break

        if pssh_boxes or tenc_boxes or sinf_boxes or saio_boxes or saiz_boxes:
            info["encrypted"] = True

        info["pssh_boxes"] = pssh_boxes
        return info

    def _parse_text_dump(self, text):
        info = {"encrypted": False, "scheme": None, "kid": None, "pssh_boxes": []}

        def _norm(line):
            return re.sub(r'(?<!\S)((?:\S )+\S)(?!\S)', lambda m: m.group(0).replace(' ', ''), line)

        lines = [_norm(sline) for sline in text.splitlines()]
        text_norm = "\n".join(lines)

        pssh_blocks = re.findall(
            r'\[pssh\].*?system_id\s*=\s*\[([0-9a-f\s]+)\].*?data_size\s*=\s*(\d+)',
            text_norm, re.IGNORECASE | re.DOTALL
        )
        for sid_raw, dsize in pssh_blocks:
            sid = sid_raw.replace(" ", "").lower()
            info["pssh_boxes"].append({"system_id": sid, "data_size": int(dsize)})
            info["encrypted"] = True
            logger.info(f"[text] PSSH system_id={sid} data_size={dsize}")

        m = re.search(r'scheme_type\s*=\s*["\']?(\w+)["\']?', text_norm, re.IGNORECASE)
        if m:
            info["scheme"] = m.group(1).lower()
            info["encrypted"] = True
            logger.info(f"[text] scheme_type={info['scheme']}")

        m = re.search(r'\[tenc\].*?default_KID\s*=\s*\[([0-9a-f\s]+)\]', text_norm, re.IGNORECASE | re.DOTALL)
        if m:
            info["kid"] = self._clean_kid(m.group(1))
            info["encrypted"] = True
            logger.info(f"[text] KID={info['kid']}")

        for tag in (r'\[sinf\]', r'\[saio\]', r'\[saiz\]'):
            if re.search(tag, text_norm, re.IGNORECASE):
                info["encrypted"] = True
                logger.info(f"[text] Found {tag}")
                break

        return info

    def _parse_binary(self, file_path):
        info = {"encrypted": False, "scheme": None, "kid": None, "pssh_boxes": []}
        try:
            with open(file_path, "rb") as f:
                data = f.read(min(os.path.getsize(file_path), 2 * 1024 * 1024))

            for scheme_marker in (b"cenc", b"cens", b"cbcs", b"cbc1"):
                if scheme_marker in data:
                    info["scheme"] = scheme_marker.decode()
                    info["encrypted"] = True
                    logger.info(f"[binary] scheme from marker: {info['scheme']}")
                    break

            schm = b'\x73\x63\x68\x6d'
            idx = data.find(schm)
            if idx != -1 and idx + 12 <= len(data):
                raw_scheme = data[idx + 8: idx + 12]
                try:
                    scheme_str = raw_scheme.decode("ascii").lower()
                    if scheme_str in _SCHEME_TO_MODE:
                        info["scheme"] = scheme_str
                        info["encrypted"] = True
                        logger.info(f"[binary] scheme from schm: {info['scheme']}")
                except Exception:
                    pass

            pssh = b'\x70\x73\x73\x68'
            search_start = 0
            while True:
                idx = data.find(pssh, search_start)
                if idx == -1:
                    break
                if idx + 28 <= len(data):
                    system_id = data[idx + 8: idx + 24].hex().lower()
                    data_size = struct.unpack_from(">I", data, idx + 24)[0] if idx + 28 <= len(data) else 0
                    info["pssh_boxes"].append({"system_id": system_id, "data_size": data_size})
                    info["encrypted"] = True
                    logger.info(f"[binary] PSSH system_id={system_id} data_size={data_size}")

                    if system_id == _WIDEVINE_SYSTEM_ID and not info["kid"]:
                        pssh_data_start = idx + 28
                        if pssh_data_start + 18 <= len(data):
                            kid_bytes = data[pssh_data_start + 2: pssh_data_start + 18]
                            info["kid"] = kid_bytes.hex().lower()
                            logger.info(f"[binary] KID from Widevine PSSH: {info['kid']}")
                search_start = idx + 4

            uuid_marker = bytes.fromhex("A2394F525A9B4F14A2446C427C648DF4".lower())
            if uuid_marker in data:
                info["encrypted"] = True
                logger.info("[binary] Found UUID sample-encryption box")

        except Exception as e:
            logger.error(f"Binary parse failed: {e}")
        return info

    def _finalize(self, info):
        """Convert raw info dict → (mode, kid, pssh_b64)."""
        scheme = info.get("scheme") or ""
        kid    = info.get("kid")
        pssh   = self._extract_pssh_b64(info.get("pssh_boxes", []))

        mode = _SCHEME_TO_MODE.get(scheme)
        if mode is None and info.get("encrypted"):
            console.print("[dim]Encryption detected (no explicit scheme). Defaulting to CTR mode.")
            mode = "ctr"

        return mode, kid, pssh

    def _extract_pssh_b64(self, pssh_boxes):
        """Return system_id string for the best available PSSH box (Widevine preferred)."""
        if not pssh_boxes:
            return None
        for box in pssh_boxes:
            if box.get("system_id", "").replace(" ", "").lower() == _WIDEVINE_SYSTEM_ID:
                return box.get("system_id")
        return pssh_boxes[0].get("system_id")

    @staticmethod
    def _clean_kid(kid_raw):
        if isinstance(kid_raw, list):
            return "".join(f"{b:02x}" for b in kid_raw)
        return re.sub(r'[\[\]\s]', '', str(kid_raw)).lower()

    def _find_boxes_by_name(self, data, name):
        results = []
        if isinstance(data, list):
            for item in data:
                results.extend(self._find_boxes_by_name(item, name))
        elif isinstance(data, dict):
            if data.get("name", "").lower() == name.lower():
                results.append(data)
            for v in data.values():
                if isinstance(v, (dict, list)):
                    results.extend(self._find_boxes_by_name(v, name))
        return results

    @staticmethod
    def _normalize_keys(keys) -> list:
        """
        Accept keys in any of these forms and return a list of (kid, key_hex) tuples:
          - KeysManager instance
          - list of "kid:key" strings
          - single "kid:key" string
          - list of (kid, key) tuples
        """
        if isinstance(keys, KeysManager):
            raw = keys.get_keys_list()
        elif isinstance(keys, str):
            raw = [k.strip() for k in keys.split("|") if k.strip()]
        elif isinstance(keys, list):
            raw = keys
        else:
            raw = []

        normalized = []
        for k in raw:
            if isinstance(k, (tuple, list)) and len(k) == 2:
                normalized.append((str(k[0]).lower(), str(k[1]).lower()))
            elif isinstance(k, str):
                # handle pipe-separated pairs inside a single list element
                for pair in k.split("|"):
                    pair = pair.strip()
                    if ":" in pair:
                        kid_v, key_v = pair.split(":", 1)
                        normalized.append((kid_v.strip().lower(), key_v.strip().lower()))
                    elif pair:
                        normalized.append(("1", pair.lower()))
        return normalized

    def decrypt(self, encrypted_path, keys, output_path, stream_type: str = "video"):
        """
        Non-live decrypt entry point (v1 interface).
        Detects encryption, validates KID, calls the right tool.
        Returns True on success, False on failure.

        Display format:
            Decrypting {filename} using {tool} method {mode} [progress bar]
        """
        logger.info(f"decrypt(): {os.path.basename(encrypted_path)} stream={stream_type} keys={keys}")
        try:
            encryption_mode, kid, pssh = self.detect_encryption(encrypted_path)

            if encryption_mode is None:
                if not keys:
                    logger.info("File is not encrypted and no keys provided — copying.")
                    shutil.copy(encrypted_path, output_path)
                    return True
                else:
                    logger.error("Encryption not detected but keys provided — forcing decryption attempt.")
                    encryption_mode = "unknown"

            normalized = self._normalize_keys(keys)

            # KID / key validation (warn only, let tool decide)
            if kid:
                key_kids = [pair[0] for pair in normalized]
                if key_kids and kid.lower() not in key_kids:
                    if kid.lower() == "0" * len(kid):
                        logger.info(f"Fixed-key encryption (KID all-zeros) — using provided key KIDs ({key_kids})")
                    else:
                        logger.warning(f"Detected KID ({kid}) not in provided key KIDs ({key_kids}) — proceeding anyway.")
            else:
                logger.info("No KID detected — proceeding with provided keys.")

            is_fixed_key = bool(kid and kid.lower() == "0" * len(kid))
            fname  = os.path.basename(encrypted_path)
            method_display = (encryption_mode or "unknown").upper()
            label  = (f"[dim]Decrypting [cyan]{fname}[/cyan] using [yellow]{self.preference}[/yellow] method [magenta]{method_display}[/magenta][/dim]")

            success = False
            if self.preference == "shaka" and self.shaka_packager_path:
                success = self._decrypt_shaka_nonlive(
                    encrypted_path, normalized, output_path, stream_type, label, is_fixed_key
                )
            else:
                success = self._decrypt_bento4_nonlive(
                    encrypted_path, normalized, output_path, label, is_fixed_key
                )

            if success:
                logger.info(f"Decryption successful: {os.path.basename(output_path)}")
                return True

            if encryption_mode == "unknown":
                logger.error("Forced decryption failed — file was likely clear-text. Copying.")
                shutil.copy(encrypted_path, output_path)
                return True

            logger.error(f"Decryption failed: {os.path.basename(encrypted_path)}")
            return False

        except Exception as e:
            logger.error(f"Decryption error: {e}")
            console.print(f"[red]Decryption error: {e}.")
            return False

    def _decrypt_bento4_nonlive(self, encrypted_path, normalized_keys, output_path, label, is_fixed_key=False):
        """
        Non-live Bento4 decrypt using the full merged file.
        normalized_keys is a list of (kid, key_hex) tuples.
        Shows:  Decrypting {fname} using bento4 method {mode} [====------] 65%
        """
        cmd = [self.mp4decrypt_path]

        if is_fixed_key and normalized_keys:
            # All keys map to the all-zeros KID that the tenc box advertises
            _, key_val = normalized_keys[0]
            flat = [("00000000000000000000000000000000", key_val)]
            logger.info("Fixed-key encryption: using zero-KID for all keys")
        else:
            flat = normalized_keys

        for kid_v, key_v in flat:
            cmd.extend(["--key", f"{kid_v.lower()}:{key_v.lower()}"])
        cmd.extend([encrypted_path, output_path])

        logger.info(f"Bento4 cmd: {' '.join(cmd)}")
        result = _run_with_progress(cmd, label, encrypted_path, output_path)

        if result is True:
            return True

        _, stderr_text = result if isinstance(result, tuple) else (False, "")
        console.print(f"[red]Bento4 failed: {stderr_text}")
        logger.error(f"Bento4 failed: {stderr_text}")
        return False

    def _decrypt_shaka_nonlive(self, encrypted_path, normalized_keys, output_path, stream_type, label, is_fixed_key=False):
        """
        Non-live Shaka decrypt using the full merged file.
        Shows:  Decrypting {fname} using shaka method {mode} [====------] 65%
        """
        if is_fixed_key and len(normalized_keys) > 1:
            logger.info(f"Fixed-key encryption: using only first key ({normalized_keys[0]})")
            normalized_keys = normalized_keys[:1]

        keys_arg = []
        for kid_v, key_v in normalized_keys:
            actual_kid = "00000000000000000000000000000000" if is_fixed_key else kid_v
            keys_arg.append(f"key_id={actual_kid.lower()}:key={key_v.lower()}")

        # Shaka needs a recognised output extension
        shaka_output = output_path
        if not output_path.lower().endswith(('.mp4', '.m4v', '.mpd')):
            shaka_output = output_path + '.tmp.mp4'
            logger.info(f"Using temporary output for Shaka: {shaka_output}")

        stream_spec = f"input={encrypted_path},stream={stream_type},output={shaka_output}"
        cmd = [
            self.shaka_packager_path,
            stream_spec,
            "--enable_fixed_key_decryption",
            "--keys", ",".join(keys_arg),
        ]
        logger.info(f"Shaka cmd: {' '.join(cmd)}")

        # Shaka does not grow its output linearly → use without-progress variant
        # but still print the label so the user sees it
        console.print(f"{label} [dim](running...)[/dim]")
        success, stderr_msg = _run_without_progress(cmd, shaka_output)

        if success:
            if shaka_output != output_path and os.path.exists(shaka_output):
                shutil.move(shaka_output, output_path)
            console.print(f"{label} {_render_markup(_render_bar(100))}")
            return True

        console.print(f"[red]Shaka failed: {stderr_msg}[/red]")
        logger.error(f"Shaka failed: {stderr_msg}")
        return False

    def decrypt_file(self, encrypted_path: str, decrypted_path: str, keys, label: str) -> tuple:
        """
        Non-live decrypt called by _decrypt_check() in manual.py.
        *keys* may be a KeysManager, a list of "kid:key" strings, or a single string.
        *label* is the stream type string ("video" / "audio") used only for logging.

        Display format (same as decrypt()):
            Decrypting {filename} using {tool} method {mode} [progress bar]

        Returns (True, None) on success or (False, error_message) on failure.
        """
        if not keys:
            return False, "No decryption keys provided."

        normalized = self._normalize_keys(keys)
        if not normalized:
            return False, "Could not parse any keys."

        # Detect encryption to get mode/kid for the progress label
        encryption_mode, kid, _pssh = self.detect_encryption(encrypted_path)

        is_fixed_key = bool(kid and kid.lower() == "0" * len(kid))
        method_display = (encryption_mode or "unknown").upper()
        fname = os.path.basename(encrypted_path)
        rich_label = (f"[dim]Decrypting [cyan]{fname}[/cyan] using [yellow]{self.preference}[/yellow] method [magenta]{method_display}[/magenta][/dim]")
        logger.info(f"decrypt_file(): {fname} → {os.path.basename(decrypted_path)} stream={label} preference={self.preference} method={method_display}")

        if self.preference == "shaka" and self.shaka_packager_path:
            # Treat label as stream_type hint (video/audio)
            stream_type = label if label in ("video", "audio") else "video"
            ok = self._decrypt_shaka_nonlive(
                encrypted_path, normalized, decrypted_path,
                stream_type, rich_label, is_fixed_key
            )
        else:
            ok = self._decrypt_bento4_nonlive(
                encrypted_path, normalized, decrypted_path,
                rich_label, is_fixed_key
            )

        if ok:
            return True, None
        return False, f"{self.preference} decryption failed for {fname}"

    def decrypt_segment_live(self, encrypted_path: str, decrypted_path: str, raw_key: str, init_path: Optional[str] = None) -> tuple:
        """
        Decrypt a single MP4 segment (fragment) during download (live mode).
        raw_key is the bare hex key string (no KID prefix).
        Returns (success: bool, message: str, decrypted_bytes: bytes | None).
        """
        engine = self.preference

        try:
            if engine == "ffmpeg" and self.ffmpeg_path:
                # FFmpeg needs init + fragment concatenated
                temp_concat = encrypted_path + ".concat"
                with open(temp_concat, "wb") as f_out:
                    if init_path and os.path.exists(init_path):
                        with open(init_path, "rb") as f_init:
                            f_out.write(f_init.read())
                    with open(encrypted_path, "rb") as f_seg:
                        f_out.write(f_seg.read())

                cmd = [
                    self.ffmpeg_path, "-loglevel", "error", "-nostdin",
                    "-decryption_key", raw_key,
                    "-i", temp_concat, "-c", "copy", "-f", "mp4", "-y", decrypted_path,
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                if os.path.exists(temp_concat):
                    os.remove(temp_concat)

            elif engine == "shaka" and self.shaka_packager_path:
                seg_spec = (
                    f"in={encrypted_path},stream=video,"
                    f"init_segment={init_path},output={decrypted_path}"
                    if init_path
                    else f"in={encrypted_path},stream=video,output={decrypted_path}"
                )
                cmd = [
                    self.shaka_packager_path,
                    seg_spec,
                    "--enable_raw_key_decryption",
                    "--keys", f"key_id=1:{raw_key}",
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)

                # Shaka sometimes refuses single fragments → fallback to bento4
                if result.returncode != 0 and self.mp4decrypt_path:
                    engine = "bento4 (fallback)"
                    cmd = [self.mp4decrypt_path, "--key", f"1:{raw_key}"]
                    if init_path and os.path.exists(init_path):
                        cmd.extend(["--fragments-info", init_path])
                    cmd.extend([encrypted_path, decrypted_path])
                    result = subprocess.run(cmd, capture_output=True, text=True)

            else:
                # Default: bento4 / mp4decrypt
                engine = "bento4"
                cmd = [self.mp4decrypt_path, "--key", f"1:{raw_key}"]
                if init_path and os.path.exists(init_path):
                    cmd.extend(["--fragments-info", init_path])
                cmd.extend([encrypted_path, decrypted_path])
                result = subprocess.run(cmd, capture_output=True, text=True)

            if result.returncode == 0 and os.path.exists(decrypted_path):
                with open(decrypted_path, "rb") as f:
                    data = f.read()
                return True, f"{engine} segment decrypted", data
            else:
                return False, f"Error {engine}: {result.stderr}", None

        except Exception as e:
            return False, f"Exception {engine}: {str(e)}", None