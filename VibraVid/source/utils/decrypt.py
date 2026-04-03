# 19.05.25

import os
import re
import time
import struct
import json
import subprocess
import shutil
import logging
import threading
from io import StringIO

from rich.console import Console

from VibraVid.setup import (get_bento4_decrypt_path, get_mp4dump_path, get_shaka_packager_path)


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
    """Renderizza il markup Rich in una stringa con ANSI codes."""
    buf = StringIO()
    temp_console = Console(file=buf, force_terminal=True)
    temp_console.print(text, end="")
    return buf.getvalue()

def _run_with_progress(cmd: list, label: str, encrypted_path: str, output_path: str) -> bool:
    """Run *cmd* via Popen, monitor the size of *output_path*"""
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
        return False
    finally:
        stop_monitor.set()

    # Final bar — 100 % on success, actual value on failure
    final_pct = 100 if process.returncode == 0 else progress_percent
    output = _render_markup(f"{label} {_render_bar(final_pct)}")
    console.file.write(f"\r{output}\n")
    console.file.flush()

    if process.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
        return True

    stderr_text = "".join(stderr_lines).strip()
    return False, stderr_text  # caller will handle error printing


class KeysManager:
    def __init__(self, keys=None):
        self._keys = []
        if keys:
            self.add_keys(keys)
    
    def add_keys(self, keys):
        if isinstance(keys, str):
            for k in keys.split('|'):
                if ':' in k:
                    kid, key = k.split(':', 1)
                    self._keys.append((kid.strip(), key.strip()))

        elif isinstance(keys, list):
            for k in keys:
                if isinstance(k, str):
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
        logger.info(f"Initializing Decryptor with preference: {preference}, license_url: {license_url}, drm_type: {drm_type}")
        self.preference = preference.lower()
        self.mp4decrypt_path = get_bento4_decrypt_path()
        self.mp4dump_path    = get_mp4dump_path()
        self.shaka_packager_path = get_shaka_packager_path()
        self.license_url  = license_url
        self.drm_type     = drm_type

    def detect_encryption(self, file_path):
        """Detect encryption scheme. Returns (mode, kid, pssh_b64) or (None,None,None)."""
        logger.info(f"Detecting encryption: {os.path.basename(file_path)}")

        # 1. Try JSON output first (works on most files)
        result = self._run_mp4dump(file_path, fmt="json")
        if result:
            info = self._parse_json_dump(result)
            if info["encrypted"]:
                logger.info(f"JSON parse OK → scheme={info['scheme']} kid={info['kid']}")
                return self._finalize(info)

        # 2. Fallback: text output (handles m4a / fragmented streams that break JSON)
        result = self._run_mp4dump(file_path, fmt="text")
        if result:
            info = self._parse_text_dump(result)
            if info["encrypted"]:
                logger.info(f"Text parse OK → scheme={info['scheme']} kid={info['kid']}")
                return self._finalize(info)

        # 3. Last resort: read raw bytes
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
                    text = raw.decode(enc)
                    text = text.lstrip("\ufeff")
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

        # KID
        for box in tenc_boxes + trex_boxes:
            if "default_KID" in box:
                info["kid"] = self._clean_kid(box["default_KID"])
                break

        # Scheme
        for schm in schm_boxes:
            if "scheme_type" in schm:
                info["scheme"] = schm["scheme_type"].lower()
                break

        # Encrypted flag
        if pssh_boxes or tenc_boxes or sinf_boxes or saio_boxes or saiz_boxes:
            info["encrypted"] = True

        info["pssh_boxes"] = pssh_boxes
        return info

    def _parse_text_dump(self, text):
        info = {"encrypted": False, "scheme": None, "kid": None, "pssh_boxes": []}

        # Normalise: collapse inter-character spaces that mp4dump inserts
        def _norm(line):
            return re.sub(r'(?<!\S)((?:\S )+\S)(?!\S)', lambda m: m.group(0).replace(' ', ''), line)

        lines = [_norm(sline) for sline in text.splitlines()]
        text_norm = "\n".join(lines)

        # ── PSSH boxes ────────────────────────────────────────────────────────
        pssh_blocks = re.findall(r'\[pssh\].*?system_id\s*=\s*\[([0-9a-f\s]+)\].*?data_size\s*=\s*(\d+)', text_norm, re.IGNORECASE | re.DOTALL)
        for sid_raw, dsize in pssh_blocks:
            sid = sid_raw.replace(" ", "").lower()
            info["pssh_boxes"].append({"system_id": sid, "data_size": int(dsize)})
            info["encrypted"] = True
            logger.info(f"[text] PSSH system_id={sid} data_size={dsize}")

        # ── scheme_type (schm box) ─────────────────────────────────────────────
        m = re.search(r'scheme_type\s*=\s*["\']?(\w+)["\']?', text_norm, re.IGNORECASE)
        if m:
            info["scheme"] = m.group(1).lower()
            info["encrypted"] = True
            logger.info(f"[text] scheme_type={info['scheme']}")

        # ── KID (tenc box) ────────────────────────────────────────────────────
        m = re.search(r'\[tenc\].*?default_KID\s*=\s*\[([0-9a-f\s]+)\]', text_norm, re.IGNORECASE | re.DOTALL)
        if m:
            info["kid"] = self._clean_kid(m.group(1))
            info["encrypted"] = True
            logger.info(f"[text] KID={info['kid']}")

        # ── sinf / saio / saiz ────────────────────────────────────────────────
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

            # ── Look for encryption scheme markers ────────────────────────────
            for scheme_marker in (b"cenc", b"cens", b"cbcs", b"cbc1"):
                if scheme_marker in data:
                    info["scheme"] = scheme_marker.decode()
                    info["encrypted"] = True
                    logger.info(f"[binary] scheme from marker: {info['scheme']}")
                    break

            # ── schm box ──────────────────────────────────────────────────────
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

            # ── Search for 'pssh' boxes ───────────────────────────────────────
            pssh = b'\x70\x73\x73\x68'
            search_start = 0
            while True:
                idx = data.find(pssh, search_start)
                if idx == -1:
                    break

                # box layout: [4B size][4B "pssh"][4B version+flags][16B system_id][4B data_size][data]
                if idx + 28 <= len(data):
                    system_id = data[idx + 8: idx + 24].hex().lower()
                    data_size = struct.unpack_from(">I", data, idx + 24)[0] if idx + 28 <= len(data) else 0
                    info["pssh_boxes"].append({"system_id": system_id, "data_size": data_size})
                    info["encrypted"] = True
                    logger.info(f"[binary] PSSH system_id={system_id} data_size={data_size}")

                    # Extract KID from Widevine PSSH data bytes [2..18] if KID not yet found
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

    def decrypt(self, encrypted_path, keys, output_path, stream_type: str = "video"):
        """Decrypt a file using the preferred method. Returns True on success."""
        logger.info(f"Starting decryption: {os.path.basename(encrypted_path)} keys={keys} stream={stream_type}")
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

            if isinstance(keys, str):
                keys = [keys]

            # KID / key validation (warn only, let tool decide)
            if kid:
                key_kids = []
                for k in keys:
                    if ":" in k:
                        key_kids.append(k.split(":", 1)[0].lower())
                    else:
                        key_kids.append(k.lower())
                if key_kids and kid.lower() not in key_kids:
                    logger.error(f"Detected KID ({kid}) not in provided key KIDs ({key_kids}) — proceeding anyway.")
            else:
                logger.info("No KID detected — proceeding with provided keys.")

            scheme_display = encryption_mode.upper() if encryption_mode else "UNKNOWN"
            fname = os.path.basename(encrypted_path)
            label = f"[dim]Decrypting [cyan]{fname}[/cyan] ({scheme_display}) with {self.preference}"

            success = False
            if self.preference == "shaka" and self.shaka_packager_path:
                success = self._decrypt_shaka(encrypted_path, keys, output_path, stream_type, label)
            else:
                success = self._decrypt_bento4(encrypted_path, keys, output_path, label)

            if success:
                logger.info(f"Decryption successful: {os.path.basename(output_path)}")
                return True

            # Forced decryption on undetected file → fallback copy
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

    def _decrypt_bento4(self, encrypted_path, keys, output_path, label):
        cmd = [self.mp4decrypt_path]
        for k in keys:
            if ":" in k:
                kid_val, key_val = k.split(":", 1)
                cmd.extend(["--key", f"{kid_val.lower()}:{key_val.lower()}"])
            else:
                cmd.extend(["--key", k.lower()])
        cmd.extend([encrypted_path, output_path])

        logger.info(f"Bento4 cmd: {' '.join(cmd)}")
        result = _run_with_progress(cmd, label, encrypted_path, output_path)

        if result is True:
            return True

        _, stderr_text = result if isinstance(result, tuple) else (False, "")
        console.print(f"[red]Bento4 failed: {stderr_text}")
        logger.error(f"Bento4 failed: {stderr_text}")
        return False

    def _decrypt_shaka(self, encrypted_path, keys, output_path, stream_type, label):
        keys_arg = []
        for k in keys:
            if ":" in k:
                kid_val, key_val = k.split(":", 1)
                keys_arg.append(f"key_id={kid_val.lower()}:key={key_val.lower()}")
            else:
                keys_arg.append(f"key={k.lower()}")

        def _build_cmd(stream_spec):
            c = [self.shaka_packager_path, stream_spec, "--enable_fixed_key_decryption"]
            if keys_arg:
                c.extend(["--keys", ",".join(keys_arg)])
            return c

        # First attempt: with stream type
        stream_spec = f"input='{encrypted_path}',stream={stream_type},output='{output_path}'"
        cmd = _build_cmd(stream_spec)
        logger.info(f"Shaka cmd: {' '.join(cmd)}")

        result = _run_with_progress(cmd, label, encrypted_path, output_path)
        if result is True:
            return True

        # Retry without stream type
        logger.error("Shaka failed with stream type — retrying without it.")
        stream_spec_plain = f"input='{encrypted_path}',output='{output_path}'"
        cmd_retry = _build_cmd(stream_spec_plain)
        logger.info(f"Shaka retry cmd: {' '.join(cmd_retry)}")
        result_retry = _run_with_progress(cmd_retry, label, encrypted_path, output_path)

        if result_retry is True:
            return True

        _, stderr_text = result if isinstance(result, tuple) else (False, "")
        console.print(f"[red]Shaka failed: {stderr_text}")
        logger.error(f"Shaka failed: {stderr_text}")
        return False