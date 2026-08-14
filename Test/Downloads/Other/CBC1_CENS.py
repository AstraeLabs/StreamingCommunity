# 14.08.26
# ruff: noqa: E402

import filecmp
import os
import subprocess
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(src_path)

from rich.console import Console

from VibraVid.utils import setup_logger
from VibraVid.setup import get_ffmpeg_path, get_shaka_packager_path
from VibraVid.core.decryptor.decryptor import Decryptor


setup_logger()
console = Console()

WORK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "Video", "_cbc1_cens_check")
KID = "11223344556677889900112233445566"
KEY = "00112233445566778899aabbccddeeff"


def _make_source(path: str) -> None:
    ffmpeg = get_ffmpeg_path()
    cmd = [
        ffmpeg, "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-c:v", "libx264", "-profile:v", "main", "-pix_fmt", "yuv420p", "-g", "30",
        "-c:a", "aac", "-b:a", "128k",
        path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _package(src: str, out_dir: str, scheme: str) -> str:
    packager = get_shaka_packager_path()
    os.makedirs(out_dir, exist_ok=True)
    video_init = os.path.join(out_dir, "video_init.mp4")
    video_tmpl = os.path.join(out_dir, "video_$Number$.m4s")
    mpd = os.path.join(out_dir, "manifest.mpd")
    cmd = [
        packager,
        f"in={src},stream=video,init_segment={video_init},segment_template={video_tmpl}",
        "--enable_raw_key_encryption",
        "--keys", f"key_id={KID}:key={KEY}",
        "--protection_scheme", scheme,
        "--mpd_output", mpd,
        "--generate_static_live_mpd",
        "--segment_duration", "2",
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    whole = os.path.join(out_dir, "video_all.mp4")
    with open(whole, "wb") as out:
        out.write(open(video_init, "rb").read())
        i = 1
        while os.path.exists(seg := os.path.join(out_dir, f"video_{i}.m4s")):
            out.write(open(seg, "rb").read())
            i += 1
    return whole


def _packager_decrypt(src: str, out: str) -> tuple[bool, str]:
    packager = get_shaka_packager_path()
    cmd = [
        packager,
        f"in={src},stream=video,output={out}",
        "--enable_raw_key_decryption",
        "--keys", f"key_id={KID}:key={KEY}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and os.path.exists(out), proc.stderr


def check(scheme: str) -> None:
    console.print(f"\n[bold cyan]=== {scheme} ===")
    out_dir = os.path.join(WORK_DIR, scheme)
    src = os.path.join(WORK_DIR, "src.mp4")
    if not os.path.exists(src):
        os.makedirs(WORK_DIR, exist_ok=True)
        _make_source(src)

    encrypted = _package(src, out_dir, scheme)

    vibravid_out = os.path.join(out_dir, "vibravid.mp4")
    dec = Decryptor()
    ok, err = dec.decrypt_file(encrypted, vibravid_out, f"{KID}:{KEY}", label=f"[{scheme}]")
    console.print(f"VibraVid decrypt: {'[green]OK' if ok else f'[red]FAILED ({err})'}")

    shaka_out = os.path.join(out_dir, "shaka.mp4")
    shaka_ok, shaka_err = _packager_decrypt(encrypted, shaka_out)
    console.print(f"Shaka Packager reference decrypt: {'[green]OK' if shaka_ok else f'[red]FAILED ({shaka_err.strip()[-300:]})'}")

    if not (ok and shaka_ok):
        return

    ffmpeg = get_ffmpeg_path()
    es_a = os.path.join(out_dir, "vibravid.h264")
    es_b = os.path.join(out_dir, "shaka.h264")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", vibravid_out, "-c", "copy", "-f", "h264", es_a], check=True)
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", shaka_out, "-c", "copy", "-f", "h264", es_b], check=True)
    identical = filecmp.cmp(es_a, es_b, shallow=False)
    console.print(f"[bold]{'[green]MATCH — bit-identical H.264 elementary stream' if identical else '[red]MISMATCH — decrypted bytes differ'}")


check("cbc1")
check("cens")
