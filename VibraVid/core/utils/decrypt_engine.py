# 10.04.26

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from io import StringIO
from typing import Optional

from rich.console import Console

from VibraVid.setup import (get_bento4_decrypt_path, get_ffmpeg_path, get_mp4dump_path, get_shaka_packager_path)


console = Console()
logger = logging.getLogger(__name__)

_WIDEVINE_SYSTEM_ID = "edef8ba979d64acea3c827dcd51d21ed"
_SCHEME_TO_MODE = {
    "cenc": "ctr",
    "cens": "ctr",
    "cbcs": "cbc",
    "cbc1": "cbc",
}
_VIDEO_CODEC_MAP = {
    "avc1": "H.264",
    "avc3": "H.264",
    "hev1": "HEVC",
    "hevC": "HEVC",
    "hev0": "HEVC",
    "vp9": "VP9",
    "av01": "AV1",
}


@dataclass
class EncryptionInfo:
    encrypted: bool = False
    scheme: Optional[str] = None
    kid: Optional[str] = None
    pssh_b64: Optional[str] = None
    video_codec: Optional[str] = None
    encryption_method: Optional[str] = None
    pssh_boxes: list[dict] = field(default_factory=list)


class KeysManager:
    def __init__(self, keys=None):
        self._keys: list[tuple[str, str]] = []
        if keys:
            self.add_keys(keys)

    def add_keys(self, keys):
        if isinstance(keys, str):
            for k in keys.split("|"):
                pair = k.strip()
                if ":" in pair:
                    kid, key = pair.split(":", 1)
                    self._keys.append((kid.strip(), key.strip()))
        elif isinstance(keys, list):
            for k in keys:
                if isinstance(k, str):
                    pair = k.strip()
                    if ":" in pair:
                        kid, key = pair.split(":", 1)
                        self._keys.append((kid.strip(), key.strip()))
                elif isinstance(k, dict):
                    kid = k.get("kid", "")
                    key = k.get("key", "")
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
    def __init__(self, license_url: str = None, drm_type: str = None, **_kwargs):
        logger.info(f"Initializing Decryptor license_url={license_url!r} drm_type={drm_type!r}")
        self.mp4decrypt_path = get_bento4_decrypt_path()
        self.mp4dump_path = get_mp4dump_path()
        self.shaka_packager_path = get_shaka_packager_path()
        self.ffmpeg_path = get_ffmpeg_path()
        self.license_url = license_url
        self.drm_type = drm_type

    def detect_encryption(self, file_path):
        """Return (mode, kid, pssh_b64, codec, enc_method) or 5xNone if clear."""
        logger.info(f"Detecting encryption: {os.path.basename(file_path)}")
        info = self._detect_encryption_info(file_path)

        if not info.encrypted:
            logger.info("No encryption indicators found")
            return None, None, None, None, None

        mode = _SCHEME_TO_MODE.get(info.scheme or "")
        if mode is None:
            mode = "ctr"
            console.print("[dim]Encryption detected (no explicit scheme). Defaulting to CTR mode.")

        logger.info(f"Encryption finalized: scheme={info.scheme}, mode={mode}, kid={info.kid}, codec={info.video_codec}, enc_method={info.encryption_method}")
        return mode, info.kid, info.pssh_b64, info.video_codec, info.encryption_method

    def decrypt(self, encrypted_path, keys, output_path, stream_type: str = "video"):
        """Non-live decrypt API. Returns bool success."""
        logger.info(f"decrypt(): {os.path.basename(encrypted_path)} stream={stream_type} keys={keys} [NON-LIVE]")
        try:
            mode, kid, _pssh, _codec, enc_method = self.detect_encryption(encrypted_path)
            normalized_keys = self._normalize_keys(keys)

            if mode is None:
                if not normalized_keys:
                    logger.info("File appears clear and no keys provided: copying")
                    shutil.copy(encrypted_path, output_path)
                    return True
                mode = "unknown"

            normalized_keys = self._resolve_fixed_key_if_needed(encrypted_path, kid, normalized_keys)
            if not normalized_keys:
                logger.error("No valid keys available for decryption")
                return False

            method_display = (mode or "unknown").upper()
            filename = os.path.basename(encrypted_path)
            use_shaka = bool(enc_method and "sample" in enc_method.lower())

            if use_shaka:
                label = (f"[cyan]Dec[/cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]Shaka[/yellow]")
                ok = self._decrypt_shaka_nonlive(
                    encrypted_path,
                    normalized_keys,
                    output_path,
                    stream_type,
                    label,
                    self._is_zero_kid(kid),
                )
            else:
                label = (f"[cyan]Dec[/cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]Bento4[/yellow]")
                ok = self._decrypt_bento4_nonlive(
                    encrypted_path,
                    normalized_keys,
                    output_path,
                    label,
                    self._is_zero_kid(kid),
                )

            if ok:
                logger.info(f"Decryption successful: {os.path.basename(output_path)}")
                return True

            if mode == "unknown":
                # Keep previous behavior: fallback to copy for force-attempt path.
                logger.error("Forced decryption failed; copying input to output.")
                shutil.copy(encrypted_path, output_path)
                return True

            return False
        except Exception as exc:
            logger.error(f"Decryption error: {exc}")
            console.print(f"[red]Decryption error: {exc}")
            return False

    def decrypt_file(self, encrypted_path: str, decrypted_path: str, keys, label: str) -> tuple:
        """Manual downloader API. Returns (success, error_message)."""
        normalized_keys = self._normalize_keys(keys)
        if not normalized_keys:
            return False, "Could not parse any keys."

        mode, kid, _pssh, _codec, _enc_method = self.detect_encryption(encrypted_path)
        normalized_keys = self._resolve_fixed_key_if_needed(encrypted_path, kid, normalized_keys)

        method_display = (mode or "unknown").upper()
        filename = os.path.basename(encrypted_path)
        rich_label = (f"[bold cyan]Dec[/bold cyan] [green]{filename}[/green] [[magenta]{method_display}[/magenta]] - [yellow]Bento4[/yellow]")

        ok = self._decrypt_bento4_nonlive(
            encrypted_path,
            normalized_keys,
            decrypted_path,
            rich_label,
            self._is_zero_kid(kid),
        )
        if ok:
            return True, None
        return False, f"Bento4 decryption failed for {filename}"

    def decrypt_segment_live(self, encrypted_path: str, decrypted_path: str, raw_key: str, raw_kid: str = "", init_path: Optional[str] = None) -> tuple:
        """Decrypt one live DASH fragment. Returns (ok, message, bytes|None)."""
        logger.info(f"decrypt_segment_live(): {os.path.basename(encrypted_path)} -> {os.path.basename(decrypted_path)} [LIVE -> BENTO4]")
        try:
            kid = raw_kid if raw_kid and len(raw_kid) == 32 else "00000000000000000000000000000000"
            cmd = [self.mp4decrypt_path]
            if init_path and os.path.exists(init_path):
                cmd.extend(["--fragments-info", init_path])
            cmd.extend(["--key", f"{kid}:{raw_key}", encrypted_path, decrypted_path])
            logger.info(f"Bento4 live cmd: {' '.join(cmd)}")

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

            logger.info(f"Bento4 live segment decrypted successfully: {len(data)} bytes")
            return True, "Bento4 live segment decrypted", data

        except Exception as exc:
            logger.error(f"Exception Bento4 live: {exc}")
            return False, f"Exception Bento4: {exc}", None

    def _detect_encryption_info(self, file_path: str) -> EncryptionInfo:
        json_info = self._parse_json_dump(self._run_mp4dump(file_path, fmt="json"))
        if json_info.encrypted:
            return self._finalize_info(json_info)

        text_info = self._parse_text_dump(self._run_mp4dump(file_path, fmt="text"))
        if text_info.encrypted:
            return self._finalize_info(text_info)

        bin_info = self._parse_binary(file_path)
        if bin_info.encrypted:
            return self._finalize_info(bin_info)

        return EncryptionInfo()

    def _finalize_info(self, info: EncryptionInfo) -> EncryptionInfo:
        info.pssh_b64 = self._select_preferred_pssh(info.pssh_boxes)
        return info

    def _run_mp4dump(self, file_path: str, fmt: str = "json") -> Optional[str]:
        try:
            cmd = [self.mp4dump_path, "--verbosity", "0", "--format", fmt, file_path]
            logger.info(f"mp4dump cmd: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, timeout=15)
            raw = result.stdout
            for enc in ("utf-8", "utf-16", "utf-16-le", "latin-1"):
                try:
                    text = raw.decode(enc).lstrip("\ufeff")
                    return text if text.strip() else None
                except (UnicodeDecodeError, ValueError):
                    continue
        except Exception as exc:
            logger.error(f"mp4dump ({fmt}) failed: {exc}")
        return None

    def _parse_json_dump(self, text: Optional[str]) -> EncryptionInfo:
        if not text:
            return EncryptionInfo()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return EncryptionInfo()

        info = EncryptionInfo()
        pssh_boxes = self._find_boxes_by_name(data, "pssh")
        tenc_boxes = self._find_boxes_by_name(data, "tenc")
        schm_boxes = self._find_boxes_by_name(data, "schm")
        encv_boxes = self._find_boxes_by_name(data, "encv")
        sinf_boxes = self._find_boxes_by_name(data, "sinf")
        saio_boxes = self._find_boxes_by_name(data, "saio")
        saiz_boxes = self._find_boxes_by_name(data, "saiz")

        for box in tenc_boxes:
            if "default_KID" in box:
                info.kid = self._clean_kid(box["default_KID"])
                break

        for schm in schm_boxes:
            scheme = schm.get("scheme_type")
            if scheme:
                info.scheme = str(scheme).lower()
                break

        for tenc in tenc_boxes:
            crypt = tenc.get("default_crypt_byte_block", 0)
            skip = tenc.get("default_skip_byte_block", 0)
            if crypt > 0 and skip > 0:
                info.encryption_method = "SAMPLE_AES"
                break

        for encv in encv_boxes:
            frma_boxes = self._find_boxes_by_name([encv], "frma")
            for frma in frma_boxes:
                fmt = frma.get("original_format")
                if fmt:
                    info.video_codec = _VIDEO_CODEC_MAP.get(fmt, fmt)
                    break
            if info.video_codec:
                break

        if pssh_boxes or tenc_boxes or sinf_boxes or saio_boxes or saiz_boxes:
            info.encrypted = True
        info.pssh_boxes = pssh_boxes
        return info

    def _parse_text_dump(self, text: Optional[str]) -> EncryptionInfo:
        if not text:
            return EncryptionInfo()

        info = EncryptionInfo()

        def _normalize_spaces(line: str) -> str:
            return re.sub(r"(?<!\S)((?:\S )+\S)(?!\S)", lambda m: m.group(0).replace(" ", ""), line)

        normalized = "\n".join(_normalize_spaces(line) for line in text.splitlines())

        pssh_blocks = re.findall(
            r"\[pssh\].*?system_id\s*=\s*\[([0-9a-f\s]+)\].*?data_size\s*=\s*(\d+)",
            normalized,
            re.IGNORECASE | re.DOTALL,
        )
        for sid_raw, data_size in pssh_blocks:
            sid = sid_raw.replace(" ", "").lower()
            info.pssh_boxes.append({"system_id": sid, "data_size": int(data_size)})
            info.encrypted = True

        scheme_match = re.search(r"scheme_type\s*=\s*[\"']?(\w+)[\"']?", normalized, re.IGNORECASE)
        if scheme_match:
            info.scheme = scheme_match.group(1).lower()
            info.encrypted = True

        kid_match = re.search(
            r"\[tenc\].*?default_KID\s*=\s*\[([0-9a-f\s]+)\]",
            normalized,
            re.IGNORECASE | re.DOTALL,
        )
        if kid_match:
            info.kid = self._clean_kid(kid_match.group(1))
            info.encrypted = True

        crypt_match = re.search(r"default_crypt_byte_block\s*=\s*(\d+)", normalized, re.IGNORECASE)
        skip_match = re.search(r"default_skip_byte_block\s*=\s*(\d+)", normalized, re.IGNORECASE)
        if crypt_match and skip_match:
            if int(crypt_match.group(1)) > 0 and int(skip_match.group(1)) > 0:
                info.encryption_method = "SAMPLE_AES"

        codec_match = re.search(r"\[encv\].*?original_format\s*=\s*(\w+)", normalized, re.IGNORECASE | re.DOTALL)
        if codec_match:
            codec_raw = codec_match.group(1)
            info.video_codec = _VIDEO_CODEC_MAP.get(codec_raw, codec_raw)

        for marker in (r"\[sinf\]", r"\[saio\]", r"\[saiz\]"):
            if re.search(marker, normalized, re.IGNORECASE):
                info.encrypted = True
                break

        return info

    def _parse_binary(self, file_path: str) -> EncryptionInfo:
        info = EncryptionInfo()
        try:
            with open(file_path, "rb") as f:
                data = f.read(min(os.path.getsize(file_path), 2 * 1024 * 1024))

            for marker in (b"cenc", b"cens", b"cbcs", b"cbc1"):
                if marker in data:
                    info.scheme = marker.decode()
                    info.encrypted = True
                    break

            schm = b"schm"
            idx = data.find(schm)
            if idx != -1 and idx + 12 <= len(data):
                raw_scheme = data[idx + 8 : idx + 12]
                try:
                    scheme = raw_scheme.decode("ascii").lower()
                    if scheme in _SCHEME_TO_MODE:
                        info.scheme = scheme
                        info.encrypted = True
                except Exception:
                    pass

            marker = b"pssh"
            cursor = 0
            while True:
                pos = data.find(marker, cursor)
                if pos == -1:
                    break
                if pos + 28 <= len(data):
                    sid = data[pos + 8 : pos + 24].hex().lower()
                    size = struct.unpack_from(">I", data, pos + 24)[0] if pos + 28 <= len(data) else 0
                    info.pssh_boxes.append({"system_id": sid, "data_size": size})
                    info.encrypted = True

                    if sid == _WIDEVINE_SYSTEM_ID and not info.kid:
                        pssh_data_start = pos + 28
                        if pssh_data_start + 18 <= len(data):
                            info.kid = data[pssh_data_start + 2 : pssh_data_start + 18].hex().lower()
                cursor = pos + 4
        except Exception as exc:
            logger.error(f"Binary parse failed: {exc}")

        return info

    @staticmethod
    def _normalize_keys(keys) -> list[tuple[str, str]]:
        if isinstance(keys, KeysManager):
            raw = keys.get_keys_list()
        elif isinstance(keys, str):
            raw = [k.strip() for k in keys.split("|") if k.strip()]
        elif isinstance(keys, list):
            raw = keys
        else:
            raw = []

        normalized: list[tuple[str, str]] = []
        for item in raw:
            if isinstance(item, (tuple, list)) and len(item) == 2:
                normalized.append((str(item[0]).lower(), str(item[1]).lower()))
            elif isinstance(item, str):
                for pair in item.split("|"):
                    p = pair.strip()
                    if not p:
                        continue
                    if ":" in p:
                        kid, key = p.split(":", 1)
                        normalized.append((kid.strip().lower(), key.strip().lower()))
                    else:
                        normalized.append(("1", p.lower()))
        return normalized

    @staticmethod
    def _is_zero_kid(kid: Optional[str]) -> bool:
        return bool(kid and kid.lower() == "0" * len(kid))

    def _resolve_fixed_key_if_needed(self, encrypted_path: str, detected_kid: Optional[str], normalized_keys: list[tuple[str, str]]) -> list[tuple[str, str]]:
        if not self._is_zero_kid(detected_kid) or len(normalized_keys) <= 1:
            return normalized_keys

        pssh_kid = self._extract_widevine_kid_from_file(encrypted_path)
        if not pssh_kid:
            logger.warning("Fixed-key stream with multiple keys but no PSSH KID extracted; using first key")
            return [normalized_keys[0]]

        for pair in normalized_keys:
            if pair[0].lower() == pssh_kid:
                logger.info(f"Fixed-key stream: selected key by PSSH KID match ({pssh_kid})")
                return [pair]

        logger.warning(f"No key matched PSSH KID {pssh_kid}; using first key")
        return [normalized_keys[0]]

    def _extract_widevine_kid_from_file(self, file_path: str) -> Optional[str]:
        try:
            with open(file_path, "rb") as f:
                data = f.read(min(os.path.getsize(file_path), 2 * 1024 * 1024))

            marker = b"pssh"
            sid = bytes.fromhex(_WIDEVINE_SYSTEM_ID)
            cursor = 0

            while True:
                pos = data.find(marker, cursor)
                if pos < 4:
                    return None

                box_start = pos - 4
                if box_start + 8 > len(data):
                    return None

                box_size = int.from_bytes(data[box_start:pos], "big", signed=False)
                if box_size < 32 or box_start + box_size > len(data):
                    cursor = pos + 4
                    continue

                box = data[box_start : box_start + box_size]
                version = box[8]
                if version not in (0, 1):
                    cursor = pos + 4
                    continue

                if box[12:28] != sid:
                    cursor = pos + 4
                    continue

                offset = 28
                if version == 1:
                    if offset + 4 > len(box):
                        return None
                    kid_count = int.from_bytes(box[offset:offset + 4], "big", signed=False)
                    offset += 4 + kid_count * 16

                if offset + 4 > len(box):
                    return None

                pssh_size = int.from_bytes(box[offset:offset + 4], "big", signed=False)
                offset += 4
                if offset + pssh_size > len(box):
                    return None

                payload = box[offset : offset + pssh_size]
                if len(payload) >= 18:
                    return payload[2:18].hex().lower()
                return None
        except Exception as exc:
            logger.warning(f"Failed extracting KID from Widevine PSSH: {exc}")
            return None

    def _decrypt_bento4_nonlive(self, encrypted_path: str, normalized_keys: list[tuple[str, str]], output_path: str, label: str, is_fixed_key: bool = False) -> bool:
        cmd = [self.mp4decrypt_path]

        pairs = normalized_keys
        if is_fixed_key and normalized_keys:
            _, key_hex = normalized_keys[0]
            pairs = [("00000000000000000000000000000000", key_hex)]

        for kid, key in pairs:
            cmd.extend(["--key", f"{kid.lower()}:{key.lower()}"])
        cmd.extend([encrypted_path, output_path])

        logger.info(f"Bento4 cmd: {' '.join(cmd)}")
        result = _run_with_progress(cmd, label, encrypted_path, output_path)
        if result is True:
            return True

        stderr_msg = result[1] if isinstance(result, tuple) else "Unknown error"
        logger.error(f"Bento4 failed: {stderr_msg}")
        console.print(f"[red]Bento4 failed: {stderr_msg}")
        return False

    def _decrypt_shaka_nonlive(self, encrypted_path: str, normalized_keys: list[tuple[str, str]], output_path: str, _stream_type: str, label: str, is_fixed_key: bool = False) -> bool:
        keys_arg: list[str] = []
        for idx, (kid, key) in enumerate(normalized_keys, start=1):
            shaka_kid = "00000000000000000000000000000000" if is_fixed_key else kid
            keys_arg.append(f"label={idx}:key_id={shaka_kid.lower()}:key={key.lower()}")

        shaka_output = output_path
        if not output_path.lower().endswith((".mp4", ".m4v", ".mpd")):
            shaka_output = output_path + ".tmp.mp4"

        stream_spec = f"input={encrypted_path},stream=0,output={shaka_output}"
        cmd = [
            self.shaka_packager_path,
            stream_spec,
            "--enable_raw_key_decryption",
            "--keys",
            " ".join(keys_arg),
        ]

        logger.info(f"Shaka cmd: {' '.join(cmd)}")
        result = _run_with_progress(cmd, label, encrypted_path, shaka_output)
        if result is True:
            if shaka_output != output_path and os.path.exists(shaka_output):
                shutil.move(shaka_output, output_path)
            return True

        stderr_msg = result[1] if isinstance(result, tuple) else "Unknown error"
        logger.error(f"Shaka failed: {stderr_msg}")
        console.print(f"[red]Shaka failed: {stderr_msg}")
        return False

    @staticmethod
    def _find_boxes_by_name(data, name: str):
        found = []
        if isinstance(data, list):
            for item in data:
                found.extend(Decryptor._find_boxes_by_name(item, name))
        elif isinstance(data, dict):
            if str(data.get("name", "")).lower() == name.lower():
                found.append(data)
            for value in data.values():
                if isinstance(value, (dict, list)):
                    found.extend(Decryptor._find_boxes_by_name(value, name))
        return found

    @staticmethod
    def _clean_kid(kid_raw):
        if isinstance(kid_raw, list):
            return "".join(f"{byte:02x}" for byte in kid_raw)
        return re.sub(r"[\[\]\s]", "", str(kid_raw)).lower()

    @staticmethod
    def _select_preferred_pssh(pssh_boxes: list[dict]) -> Optional[str]:
        if not pssh_boxes:
            return None
        for box in pssh_boxes:
            if box.get("system_id", "").replace(" ", "").lower() == _WIDEVINE_SYSTEM_ID:
                return box.get("system_id")
        return pssh_boxes[0].get("system_id")


# ------------------ LIVE DECRYPTION WITH PROGRESS MONITORING ------------------
def _render_bar(percent: int, length: int = 10) -> str:
    filled = int((percent / 100) * length)
    bar = (
        "[dim][[/dim]"
        + f"[green]{'=' * filled}[/green]"
        + f"[dim]{'-' * (length - filled)}[/dim]"
        + "[dim]][/dim]"
    )
    return f"{bar} [dim]{percent:3d}%[/dim]"


def _render_markup(text: str) -> str:
    buf = StringIO()
    temp = Console(file=buf, force_terminal=True)
    temp.print(text, end="")
    return buf.getvalue()


def _run_with_progress(cmd: list, label: str, encrypted_path: str, output_path: str) -> tuple:
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

    monitor = threading.Thread(target=_monitor, daemon=True)
    monitor.start()

    stderr_lines: list[str] = []
    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        def _read_stderr():
            for line in process.stderr:
                stderr_lines.append(line)

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        while process.poll() is None:
            output = _render_markup(f"{label} {_render_bar(progress_percent)}")
            console.file.write(f"\r{output}")
            console.file.flush()
            time.sleep(0.1)

        process.wait()
        stderr_thread.join(timeout=2)
    except Exception as exc:
        stop_monitor.set()
        console.print()
        return False, str(exc)
    finally:
        stop_monitor.set()

    final_percent = 100 if process.returncode == 0 else progress_percent
    output = _render_markup(f"{label} {_render_bar(final_percent)}")
    console.file.write(f"\r{output}\n")
    console.file.flush()

    time.sleep(0.3)
    if process.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
        return True

    stderr_text = "".join(stderr_lines).strip()
    return False, stderr_text