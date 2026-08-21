# 12.06.26

import base64
import hashlib
import logging
import os
import re
import time
from collections.abc import Callable
from urllib.parse import urljoin

import httpx
from Cryptodome.Cipher import AES

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def b64_encode(b: bytes) -> str:
    return base64.b64encode(b).decode().replace("+", "-").replace("/", "_").rstrip("=")


def b64_decode(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s)


def new_file_key() -> tuple[bytes, bytes]:
    return os.urandom(32), os.urandom(8)


def pack_key(key: bytes, nonce: bytes) -> str:
    return b64_encode(key + nonce)


def unpack_key(s: str) -> tuple[bytes, bytes]:
    raw = b64_decode(s)
    return raw[:32], raw[32:40]


class _EncryptingReader:
    def __init__(self, path: str, key: bytes, nonce: bytes, on_progress: Callable[[int, int], None] | None = None):
        self._fh = open(path, "rb")
        self._total = os.path.getsize(path)
        self._cipher = AES.new(key, AES.MODE_CTR, nonce=nonce)
        self._on_progress = on_progress
        self._done = 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._fh.read(size)
        if not chunk:
            return b""
        enc = self._cipher.encrypt(chunk)
        self._done += len(chunk)
        if self._on_progress:
            self._on_progress(self._done, self._total)
        return enc

    def __len__(self) -> int:
        return self._total

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        chunk = self.read(1 << 20)
        if not chunk:
            raise StopIteration
        return chunk

    def fileno(self):
        import io

        raise io.UnsupportedOperation("_EncryptingReader has no fileno")

    def close(self):
        self._fh.close()


def new_upload_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(connect=30.0, read=300.0, write=60.0, pool=30.0))


def upload_file(
    upload_client: httpx.Client,
    file_path: str,
    endpoint: str,
    key: bytes,
    nonce: bytes,
    filename: str | None = None,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> dict:
    name = filename or os.path.basename(file_path)
    reader = _EncryptingReader(file_path, key, nonce, on_progress=on_progress)
    try:
        r = upload_client.post(
            endpoint,
            params={"filename": name},
            content=reader,
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(reader._total)},
        )
    finally:
        reader.close()
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(f"storage upload failed: {data}")
    return data


def _solve_pow(text: str) -> dict | None:
    m_ch = re.search(r'name="pow_challenge"\s+value="([^"]*)"', text)
    if not m_ch:
        return None
    challenge = m_ch.group(1)
    ts = re.search(r'name="pow_ts"\s+value="([^"]*)"', text).group(1)
    diff = int(re.search(r'name="pow_diff"\s+value="([^"]*)"', text).group(1))
    sig = re.search(r'name="pow_sig"\s+value="([^"]*)"', text).group(1)

    def leading_zero_bits(b, need):
        full = need >> 3
        rem = need & 7
        for i in range(full):
            if b[i] != 0:
                return False
        if rem and (b[full] & ((0xFF << (8 - rem)) & 0xFF)) != 0:
            return False
        return True

    prefix = (challenge + ":").encode()
    nonce = 0
    while True:
        hsh = hashlib.sha256(prefix + str(nonce).encode()).digest()
        if leading_zero_bits(hsh, diff):
            break
        nonce += 1

    return {
        "orig_ref": "",
        "pow_challenge": challenge,
        "pow_ts": ts,
        "pow_diff": diff,
        "pow_sig": sig,
        "pow_nonce": nonce,
    }


def _resolve_direct(session, landing_url: str, timeout: int = 60) -> str | None:
    headers = {"User-Agent": _UA}
    try:
        r = session.get(landing_url, timeout=timeout, headers=headers, stream=True)
        try:
            head = b""
            for chunk in r.iter_content(chunk_size=8192):
                if not chunk:
                    continue
                head += chunk
                if len(head) >= 32768:
                    break
        finally:
            r.close()
    except Exception:
        return None
    if not head.lstrip().startswith(b"<"):
        return None
    text = head.decode("utf-8", "replace")

    pow_data = _solve_pow(text)
    if pow_data is not None:
        try:
            r2 = session.post(landing_url, data=pow_data, timeout=timeout, headers=headers)
            if r2.ok:
                text = r2.text
        except Exception:
            pass

    m = re.search(r'var\s+u\s*=\s*\[(.*?)\]\.join\(""\)', text, re.S)
    if m:
        frags = re.findall(r'"((?:[^"\\]|\\.)*)"', m.group(1))
        if frags:
            joined = "".join(f.replace(r"\/", "/").replace(r"\"", '"').replace(r"\\", "\\") for f in frags)
            if joined.startswith("http"):
                return joined

    m2 = re.search(r'<a[^>]+class="[^"]*\bbtn-main\b[^"]*"[^>]+href="([^"]+)"', text)
    if not m2:
        m2 = re.search(r'<a[^>]+class="[^"]*\bbtn\b[^"]*"[^>]+href="([^"]+)"', text)
    if m2:
        return urljoin(landing_url, m2.group(1))
    return None


class IncompleteDownloadError(IOError):
    pass


def download_decrypt(
    session,
    landing_url: str,
    dest_path: str,
    key: bytes,
    nonce: bytes,
    total: int | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    chunk_size: int = 1 << 20,
    retries: int = 15,
    backoff: int = 4,
    incomplete_retries: int = 2,
    incomplete_backoff: int = 2,
) -> str:
    direct_url = _resolve_direct(session, landing_url) or landing_url
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
    headers = {"User-Agent": _UA}

    last_exc = None
    incomplete_attempts = 0
    for attempt in range(1, retries + 1):
        try:
            cipher = AES.new(key, AES.MODE_CTR, nonce=nonce)
            with session.get(direct_url, stream=True, timeout=300, headers=headers) as r:
                r.raise_for_status()
                dl_total = total
                if dl_total is None:
                    try:
                        dl_total = int(r.headers.get("Content-Length") or 0) or None
                    except (TypeError, ValueError):
                        dl_total = None
                written = 0
                tmp_path = dest_path + ".part"
                with open(tmp_path, "wb") as out:
                    for chunk in r.iter_content(chunk_size=chunk_size):
                        if not chunk:
                            continue
                        out.write(cipher.decrypt(chunk))
                        written += len(chunk)
                        if on_progress:
                            on_progress(written, dl_total)
                if dl_total is not None and written != dl_total:
                    os.remove(tmp_path)
                    raise IncompleteDownloadError(f"incomplete download: got {written} of {dl_total} bytes")
                os.replace(tmp_path, dest_path)
            return dest_path
        except IncompleteDownloadError as exc:
            last_exc = exc
            incomplete_attempts += 1
            if incomplete_attempts < incomplete_retries:
                time.sleep(incomplete_backoff)
                continue
            raise
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff * attempt)
                continue
            raise
    if last_exc:
        raise last_exc
    return dest_path
