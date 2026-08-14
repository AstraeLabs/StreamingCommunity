# 14.08.26
# ruff: noqa: E402

import filecmp
import os
import subprocess
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(src_path)

import httpx
from rich.console import Console

from VibraVid.utils import setup_logger
from VibraVid.setup import get_ffmpeg_path, get_shaka_packager_path
from VibraVid.core.decryptor.decryptor import Decryptor


setup_logger()
console = Console()

ASSET_BASE = "https://storage.googleapis.com/shaka-demo-assets/angel-one-clearkey/"
# Opus/webm "it" audio representation (Representation id=21 in the manifest).
AUDIO_URL = ASSET_BASE + "a-ita-0096k-libopus-2c.webm"
VIDEO_URL = ASSET_BASE + "v-0144p-0100k-vp9.webm"
KID = "feedf00deedeadbeeff0baadf00dd00d"
KEY = "00112233445566778899aabbccddeeff"

WORK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "Video", "_webm_check")


def _download(url: str, path: str) -> None:
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        with open(path, "wb") as fh:
            fh.write(resp.content)


def _packager_decrypt(src: str, stream: str, out: str) -> tuple[bool, str]:
    packager = get_shaka_packager_path()
    if not packager:
        return False, "packager.exe not found on PATH"
    cmd = [
        packager,
        f"in={src},stream={stream},output={out}",
        "--enable_raw_key_decryption",
        "--keys",
        f"key_id={KID}:key={KEY}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and os.path.exists(out), proc.stderr


def _decode_for_compare(src: str, out: str, stream_type: str) -> bool:
    ffmpeg = get_ffmpeg_path()
    if stream_type == "audio":
        cmd = [ffmpeg, "-v", "error", "-y", "-i", src, "-f", "s16le", "-ar", "48000", "-ac", "2", out]
    else:
        cmd = [ffmpeg, "-v", "error", "-y", "-i", src, "-f", "rawvideo", "-pix_fmt", "yuv420p", out]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        console.print(f"[red]ffmpeg decode failed: {proc.stderr.strip()}")
        return False
    return True


def check(label: str, url: str, stream_type: str) -> None:
    console.print(f"\n[bold cyan]=== {label} ({stream_type}) ===")
    os.makedirs(WORK_DIR, exist_ok=True)
    src = os.path.join(WORK_DIR, f"{label}_src.webm")
    vibravid_out = os.path.join(WORK_DIR, f"{label}_vibravid.webm")
    shaka_out = os.path.join(WORK_DIR, f"{label}_shaka.webm")

    console.print(f"[dim]Downloading {url}")
    _download(url, src)

    dec = Decryptor()
    ok, err = dec.decrypt_file(src, vibravid_out, KID + ":" + KEY, label=f"[{label}]")
    console.print(f"VibraVid decrypt: {'[green]OK' if ok else f'[red]FAILED ({err})'}")

    shaka_ok, shaka_err = _packager_decrypt(src, stream_type, shaka_out)
    console.print(f"Shaka Packager reference decrypt: {'[green]OK' if shaka_ok else f'[red]FAILED ({shaka_err.strip()[-300:]})'}")

    if not (ok and shaka_ok):
        console.print("[yellow]Skipping PCM/byte comparison — at least one side failed.")
        return

    ext = "pcm" if stream_type == "audio" else "yuv"
    out_a = os.path.join(WORK_DIR, f"{label}_vibravid.{ext}")
    out_b = os.path.join(WORK_DIR, f"{label}_shaka.{ext}")
    if _decode_for_compare(vibravid_out, out_a, stream_type) and _decode_for_compare(shaka_out, out_b, stream_type):
        identical = filecmp.cmp(out_a, out_b, shallow=False)
        kind = "PCM" if stream_type == "audio" else "YUV"
        console.print(f"[bold]{f'[green]MATCH — bit-identical decoded {kind}' if identical else f'[red]MISMATCH — decoded {kind} differs'}")


check("AUDIO_OPUS", AUDIO_URL, "audio")
check("VIDEO_VP9", VIDEO_URL, "video")
