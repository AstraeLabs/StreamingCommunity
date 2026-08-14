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
from rich.table import Table

from VibraVid.utils import setup_logger
from VibraVid.setup import get_ffmpeg_path, get_bento4_decrypt_path
from VibraVid.core.decryptor.decryptor import Decryptor


setup_logger()
console = Console()

BASE_URL = "https://raw.githubusercontent.com/axiomatic-systems/Bento4/master/Test/Data"
WORK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "Video", "_bento4_suite")

KID = "11223344556677889900112233445566"
KEY = "000102030405060708090a0b0c0d0e0f"
IV = "a0a1a2a3a4a5a6a7"

METHODS = ["MPEG-CENC", "MPEG-CBC1", "MPEG-CENS", "MPEG-CBCS"]

# (source filename, {track_id: media_kind}) — media_kind picks the ffmpeg -c copy ES format.
SOURCES = [
    ("video-h264-002.mp4", {1: "h264", 2: "aac"}),
    ("audio-aac-002.mp4", {1: "aac"}),
    ("audio-aac-003.mp4", {1: "aac"}),
]

_ES_FORMAT = {"h264": "h264", "aac": "adts"}


def _mp4encrypt_path() -> str:
    mp4decrypt = get_bento4_decrypt_path()
    return os.path.join(os.path.dirname(mp4decrypt), "mp4encrypt.exe")


def _download(url: str, path: str) -> bool:
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            with open(path, "wb") as fh:
                fh.write(resp.content)
        return os.path.getsize(path) > 100
    except Exception as exc:
        console.print(f"[red]download failed: {url} ({exc})")
        return False


def _encrypt(mp4encrypt: str, method: str, src: str, tracks: dict, out: str) -> bool:
    cmd = [mp4encrypt, "--method", method]
    for tid in tracks:
        cmd += ["--key", f"{tid}:{KEY}:{IV}", "--property", f"{tid}:KID:{KID}"]
    cmd += [src, out]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        console.print(f"[red]mp4encrypt failed: {proc.stderr.strip()[-300:]}")
    return proc.returncode == 0 and os.path.exists(out)


def _compare_es(a_path: str, b_path: str, kind: str, work_dir: str, tag: str) -> bool:
    ffmpeg = get_ffmpeg_path()
    fmt = _ES_FORMAT[kind]
    stream_sel = "0:v:0" if kind == "h264" else "0:a:0"
    es_a = os.path.join(work_dir, f"{tag}_a.{fmt}")
    es_b = os.path.join(work_dir, f"{tag}_b.{fmt}")
    pa = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", a_path, "-map", stream_sel, "-c", "copy", "-f", fmt, es_a], capture_output=True)
    pb = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", b_path, "-map", stream_sel, "-c", "copy", "-f", fmt, es_b], capture_output=True)
    if pa.returncode != 0 or pb.returncode != 0 or not (os.path.exists(es_a) and os.path.exists(es_b)):
        return False
    return filecmp.cmp(es_a, es_b, shallow=False)


def run() -> None:
    os.makedirs(WORK_DIR, exist_ok=True)
    mp4encrypt = _mp4encrypt_path()
    if not os.path.exists(mp4encrypt):
        console.print(f"[red]mp4encrypt not found at {mp4encrypt} — install the Bento4 SDK binaries first.")
        return

    table = Table(title="Bento4 testdata suite (self-generated CENC fixtures)")
    table.add_column("Fixture")
    table.add_column("Result")
    table.add_column("Method")

    counts = {"MATCH": 0, "MISMATCH": 0, "FLUX_FAIL": 0, "FETCH_FAIL": 0, "ENCRYPT_FAIL": 0}

    for src_name, tracks in SOURCES:
        src_path_ = os.path.join(WORK_DIR, src_name)
        if not os.path.exists(src_path_) and not _download(f"{BASE_URL}/{src_name}", src_path_):
            for method in METHODS:
                table.add_row(f"{src_name}/{method}", "[red]FETCH_FAIL", "-")
                counts["FETCH_FAIL"] += 1
            continue

        for method in METHODS:
            tag = f"{os.path.splitext(src_name)[0]}_{method}"
            enc_path = os.path.join(WORK_DIR, f"enc_{tag}.mp4")
            flux_path = os.path.join(WORK_DIR, f"flux_{tag}.mp4")

            if not _encrypt(mp4encrypt, method, src_path_, tracks, enc_path):
                table.add_row(tag, "[red]ENCRYPT_FAIL", "-")
                counts["ENCRYPT_FAIL"] += 1
                continue

            dec = Decryptor()
            ok, err = dec.decrypt_file(enc_path, flux_path, f"{KID}:{KEY}", label=f"[{tag}]")
            if not ok:
                table.add_row(tag, "[red]FLUX_FAIL", str(err)[:60])
                counts["FLUX_FAIL"] += 1
                continue

            all_match = True
            for tid, kind in tracks.items():
                if not _compare_es(flux_path, src_path_, kind, WORK_DIR, f"{tag}_t{tid}"):
                    all_match = False

            if all_match:
                table.add_row(tag, "[green]MATCH", f"ES({'+'.join(tracks.values())})")
                counts["MATCH"] += 1
            else:
                table.add_row(tag, "[red]MISMATCH", f"ES({'+'.join(tracks.values())})")
                counts["MISMATCH"] += 1

    console.print(table)
    total = sum(counts.values())
    console.print(
        f"\n[bold]{counts['MATCH']} match, {counts['MISMATCH']} mismatch, "
        f"{counts['ENCRYPT_FAIL']} encrypt failures, {counts['FLUX_FAIL']} flux failures, "
        f"{counts['FETCH_FAIL']} fetch failures[/bold] out of {total} fixtures."
    )


if __name__ == "__main__":
    run()
