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
from VibraVid.setup import get_ffmpeg_path, get_ffprobe_path
from VibraVid.core.decryptor.decryptor import Decryptor


setup_logger()
console = Console()

BASE_URL = "https://raw.githubusercontent.com/shaka-project/shaka-packager/main/packager/app/test/testdata"
# packager_test.py PackagerAppTest.setUp(): the raw key/kid every `encryption=True`
# test (cenc/cbcs/cbc1/cens/fixed-key/trick-play/no-clear-lead/...) shares.
KID = "31323334353637383930313233343536"
KEY = "32333435363738393021323334353637"

WORK_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "Video", "_shaka_suite")

# (testdata dir, encrypted filename, decrypted-reference filename)
FIXTURES = [
    ("av1-mp4-with-encryption", "bear-av1-video.mp4", "decrypted-bear-av1-video-0.mp4"),
    ("encryption-and-no-clear-lead", "bear-640x360-audio.mp4", "decrypted-bear-640x360-audio-0.mp4"),
    ("encryption-and-no-clear-lead", "bear-640x360-video.mp4", "decrypted-bear-640x360-video-0.mp4"),
    ("encryption-and-trick-play", "bear-640x360-audio.mp4", "decrypted-bear-640x360-audio-0.mp4"),
    ("encryption-and-trick-play", "bear-640x360-video.mp4", "decrypted-bear-640x360-video-0.mp4"),
    ("encryption-cbc-1", "bear-640x360-audio.mp4", "decrypted-bear-640x360-audio-0.mp4"),
    ("encryption-cbc-1", "bear-640x360-video.mp4", "decrypted-bear-640x360-video-0.mp4"),
    ("encryption-cbcs-with-full-protection", "bear-640x360-audio.mp4", "decrypted-bear-640x360-audio-0.mp4"),
    ("encryption-cbcs-with-full-protection", "bear-640x360-video.mp4", "decrypted-bear-640x360-video-0.mp4"),
    ("encryption-cbcs", "bear-640x360-audio.mp4", "decrypted-bear-640x360-audio-0.mp4"),
    ("encryption-cbcs", "bear-640x360-video.mp4", "decrypted-bear-640x360-video-0.mp4"),
    ("encryption-cens", "bear-640x360-audio.mp4", "decrypted-bear-640x360-audio-0.mp4"),
    ("encryption-cens", "bear-640x360-video.mp4", "decrypted-bear-640x360-video-0.mp4"),
    ("encryption-of-only-video-stream", "bear-640x360-video.mp4", "decrypted-bear-640x360-video-0.mp4"),
    ("encryption-using-fixed-key", "bear-640x360-audio.mp4", "decrypted-bear-640x360-audio-0.mp4"),
    ("encryption-using-fixed-key", "bear-640x360-video.mp4", "decrypted-bear-640x360-video-0.mp4"),
    ("encryption", "bear-640x360-audio.mp4", "decrypted-bear-640x360-audio-0.mp4"),
    ("encryption", "bear-640x360-video.mp4", "decrypted-bear-640x360-video-0.mp4"),
    ("flac-with-encryption", "bear-flac-audio.mp4", "decrypted-bear-flac-audio-0.mp4"),
    ("hevc-with-encryption", "bear-640x360-hevc-video.mp4", "decrypted-bear-640x360-hevc-video-0.mp4"),
    ("mv-hevc-mp4-with-encryption", "water-mv-hevc-video.mp4", "decrypted-water-mv-hevc-video-0.mp4"),
    ("opus-vp9-mp4-with-encryption", "bear-320x240-vp9-opus-audio.mp4", "decrypted-bear-320x240-vp9-opus-audio-0.mp4"),
    ("opus-vp9-mp4-with-encryption", "bear-320x240-vp9-opus-video.mp4", "decrypted-bear-320x240-vp9-opus-video-0.mp4"),
    ("vp8-mp4-with-encryption", "bear-640x360-video.mp4", "decrypted-bear-640x360-video-0.mp4"),
    ("webm-subsample-encryption", "bear-320x180-vp9-altref-video.webm", "decrypted-bear-320x180-vp9-altref-video-0.webm"),
    ("webm-vp9-full-sample-encryption", "bear-320x180-vp9-altref-video.webm", "decrypted-bear-320x180-vp9-altref-video-0.webm"),
    ("webm-with-encryption", "bear-640x360-video.webm", "decrypted-bear-640x360-video-0.webm"),
]

# ffmpeg -c copy raw-elementary-stream format per source codec (probed at runtime).
# flac/opus deliberately absent: re-muxing to their native container (.flac,
# ogg) isn't byte-deterministic across two independently produced files
# (STREAMINFO/page-boundary differences) even when the underlying audio is
# identical — confirmed by cross-checking against decoded-PCM compare while
# building this test. They fall through to the PCM path below instead.
_ES_FORMAT = {
    "h264": "h264",
    "hevc": "hevc",
    "vp8": "ivf",
    "vp9": "ivf",
    "av1": "ivf",
    "aac": "adts",
}


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


def _probe_codec(path: str) -> str | None:
    ffprobe = get_ffprobe_path()
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    codec = proc.stdout.strip()
    if codec:
        return codec
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return proc.stdout.strip() or None


def _compare(flux_path: str, ref_path: str, work_dir: str) -> tuple[bool, str]:
    codec = _probe_codec(ref_path)
    ffmpeg = get_ffmpeg_path()
    es_fmt = _ES_FORMAT.get(codec or "")

    if es_fmt:
        es_a = os.path.join(work_dir, f"a.{es_fmt}")
        es_b = os.path.join(work_dir, f"b.{es_fmt}")
        pa = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", flux_path, "-c", "copy", "-f", es_fmt, es_a], capture_output=True)
        pb = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", ref_path, "-c", "copy", "-f", es_fmt, es_b], capture_output=True)
        if pa.returncode == 0 and pb.returncode == 0 and os.path.exists(es_a) and os.path.exists(es_b):
            identical = filecmp.cmp(es_a, es_b, shallow=False)
            return identical, f"ES({codec})"

    # Fallback: decoded raw compare (video->YUV, audio->PCM).
    is_video = codec in ("h264", "hevc", "vp8", "vp9", "av1")
    ext = "yuv" if is_video else "pcm"
    out_fmt = ["-f", "rawvideo", "-pix_fmt", "yuv420p"] if is_video else ["-f", "s16le"]
    ra = os.path.join(work_dir, f"a.{ext}")
    rb = os.path.join(work_dir, f"b.{ext}")
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", flux_path, *out_fmt, ra], capture_output=True)
    subprocess.run([ffmpeg, "-v", "error", "-y", "-i", ref_path, *out_fmt, rb], capture_output=True)
    if os.path.exists(ra) and os.path.exists(rb):
        return filecmp.cmp(ra, rb, shallow=False), f"decoded({codec})"
    return False, f"no-compare({codec})"


def run() -> None:
    os.makedirs(WORK_DIR, exist_ok=True)
    table = Table(title="Shaka Packager testdata suite")
    table.add_column("Fixture")
    table.add_column("Result")
    table.add_column("Method")

    counts = {"MATCH": 0, "MISMATCH": 0, "FLUX_FAIL": 0, "FETCH_FAIL": 0}

    for dir_name, enc_name, ref_name in FIXTURES:
        tag = f"{dir_name}/{enc_name}"
        fixture_dir = os.path.join(WORK_DIR, dir_name)
        os.makedirs(fixture_dir, exist_ok=True)

        enc_path = os.path.join(fixture_dir, f"enc_{enc_name}")
        ref_path = os.path.join(fixture_dir, f"ref_{enc_name}")
        flux_path = os.path.join(fixture_dir, f"flux_{enc_name}")

        if not os.path.exists(enc_path) and not _download(f"{BASE_URL}/{dir_name}/{enc_name}", enc_path):
            table.add_row(tag, "[red]FETCH_FAIL", "-")
            counts["FETCH_FAIL"] += 1
            continue
        if not os.path.exists(ref_path) and not _download(f"{BASE_URL}/{dir_name}/{ref_name}", ref_path):
            table.add_row(tag, "[red]FETCH_FAIL", "-")
            counts["FETCH_FAIL"] += 1
            continue

        dec = Decryptor()
        ok, err = dec.decrypt_file(enc_path, flux_path, f"{KID}:{KEY}", label=f"[{tag}]")
        if not ok:
            table.add_row(tag, "[red]FLUX_FAIL", str(err)[:60])
            counts["FLUX_FAIL"] += 1
            continue

        identical, method = _compare(flux_path, ref_path, fixture_dir)
        if identical:
            table.add_row(tag, "[green]MATCH", method)
            counts["MATCH"] += 1
        else:
            table.add_row(tag, "[red]MISMATCH", method)
            counts["MISMATCH"] += 1

    console.print(table)
    console.print(
        f"\n[bold]{counts['MATCH']} match, {counts['MISMATCH']} mismatch, "
        f"{counts['FLUX_FAIL']} flux failures, {counts['FETCH_FAIL']} fetch failures[/bold] "
        f"out of {len(FIXTURES)} fixtures."
    )


if __name__ == "__main__":
    run()
