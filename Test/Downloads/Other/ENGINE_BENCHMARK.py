# 14.08.26
# ruff: noqa: E402

import csv
import os
import re
import subprocess
import sys
import time

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.append(src_path)

import psutil
from rich.console import Console
from rich.table import Table

from VibraVid.utils import setup_logger
from VibraVid.setup import get_bento4_decrypt_path, get_shaka_packager_path, get_flux_path


setup_logger()
console = Console()

WORK_DIR = r"C:\Users\Testing\Documents\GitHub\VibraVid_New\Video\.Custom_generic_temp"
CSV_PATH = os.path.join(WORK_DIR, "engine_benchmark.csv")

ZERO_KID = "00000000000000000000000000000000"

FILES = [
    {
        "name": "Custom.mp4",
        "path": os.path.join(WORK_DIR, "Custom.mp4"),
        "stream": "video",
        "kid": "b5d10e9e07534ebdb9e6d1016292db1c",
        "key": "76677a25245d006d27a200d415283f75",
        "zero_kid_quirk": False,
    },
    {
        "name": "Custom.en-us.m4a",
        "path": os.path.join(WORK_DIR, "Custom.en-us.m4a"),
        "stream": "audio",
        "kid": "762a5c88d1b14d45a33f527082ab6ba5",
        "key": "fdc780130fcec51ed5d707b2b83a3a16",
        "zero_kid_quirk": False,
    },
]

SAMPLE_INTERVAL = 0.2


def _build_cmd(engine: str, f: dict, out_path: str) -> list[str]:
    kid, key = f["kid"].lower(), f["key"].lower()
    bento4_shaka_kid = ZERO_KID if f.get("zero_kid_quirk") else kid
    if engine == "bento4":
        exe = get_bento4_decrypt_path()
        return [exe, "--key", f"{bento4_shaka_kid}:{key}", f["path"], out_path]
    if engine == "flux":
        exe = get_flux_path()
        if not exe:
            raise RuntimeError("flux binary not found")
        return [exe, "-i", f["path"], "-o", out_path, "-f", "progressive", "--key", f"{kid}:{key}"]
    if engine == "shaka":
        exe = get_shaka_packager_path()
        stream_spec = f"input={f['path']},stream={f['stream']},output={out_path}"
        keys_arg = f"label=1:key_id={bento4_shaka_kid}:key={key}"
        return [exe, stream_spec, "--enable_raw_key_decryption", "--keys", keys_arg]
    raise ValueError(engine)


_DURATION_RE = re.compile(r"([\d.]+)(ns|\xb5s|ms|s)")


def _duration_to_sec(text: str) -> float | None:
    """Parse a Rust `Duration` Debug string (e.g. "1.234567891s", "123.4ms") to seconds."""
    m = _DURATION_RE.fullmatch(text.strip())
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2)
    scale = {"ns": 1e-9, "\xb5s": 1e-6, "ms": 1e-3, "s": 1.0}[unit]
    return value * scale


_FLUX_PASS2_RE = re.compile(
    r"pass2 total:.*?\(read=([^,]+), crypto=([^,]+), write=([^)]+)\)"
)


def _parse_flux_phase_timing(stderr_text: str) -> dict:
    m = _FLUX_PASS2_RE.search(stderr_text)
    if not m:
        return {}
    read_s, crypto_s, write_s = (_duration_to_sec(g) for g in m.groups())
    return {"phase_read_sec": read_s, "phase_crypto_sec": crypto_s, "phase_write_sec": write_s}


def _run_and_measure(cmd: list[str]) -> dict:
    t0 = time.monotonic()
    proc = psutil.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )

    peak_rss = 0
    cpu_samples: list[float] = []

    def _procs():
        try:
            return [proc, *proc.children(recursive=True)]
        except psutil.NoSuchProcess:
            return []

    for p in _procs():
        try:
            p.cpu_percent(None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    while proc.poll() is None:
        time.sleep(SAMPLE_INTERVAL)
        total_rss = 0
        total_cpu = 0.0
        for p in _procs():
            try:
                total_rss += p.memory_info().rss
                total_cpu += p.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        peak_rss = max(peak_rss, total_rss)
        cpu_samples.append(total_cpu)

    stdout, stderr = proc.communicate()
    elapsed = time.monotonic() - t0
    avg_cpu = sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0.0
    stdout_text = (stdout or b"").decode("utf-8", errors="replace")
    stderr_text = (stderr or b"").decode("utf-8", errors="replace")

    return {
        "returncode": proc.returncode,
        "elapsed_sec": elapsed,
        "avg_cpu_percent": avg_cpu,
        "peak_cpu_percent": max(cpu_samples) if cpu_samples else 0.0,
        "peak_rss_mb": peak_rss / (1024 * 1024),
        "stdout_text": stdout_text,
        "stderr_text": stderr_text,
        "stderr": stderr_text[-300:],
        **_parse_flux_phase_timing(stderr_text),
    }


def run(engine_order: tuple[str, ...] = ("bento4", "shaka", "flux")) -> None:
    os.makedirs(WORK_DIR, exist_ok=True)
    fieldnames = [
        "order", "engine", "file", "success", "elapsed_sec", "avg_cpu_percent",
        "peak_cpu_percent", "peak_rss_mb", "phase_read_sec", "phase_crypto_sec",
        "phase_write_sec", "output_size_bytes", "error", "log_file",
    ]
    write_header = not os.path.exists(CSV_PATH)
    csv_fh = open(CSV_PATH, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_fh, fieldnames=fieldnames)
    if write_header:
        writer.writeheader()
        csv_fh.flush()

    table = Table(title=f"Engine benchmark (order: {' -> '.join(engine_order)})")
    for col in [
        "Engine", "File", "Result", "Time (s)", "Avg CPU%", "Peak CPU%", "Peak RAM (MB)",
        "Read (s)", "Crypto (s)", "Write (s)",
    ]:
        table.add_column(col)

    for f in FILES:
        if not os.path.exists(f["path"]):
            console.print(f"[red]missing input file: {f['path']}")
            continue

        for engine in engine_order:
            out_dir = os.path.join(WORK_DIR, "outputs", engine)
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, f["name"])
            if engine == "shaka" and not out_path.lower().endswith((".mp4", ".m4v")):
                out_path += ".mp4"
            if os.path.exists(out_path):
                os.remove(out_path)

            cmd = _build_cmd(engine, f, out_path)
            console.print(f"[cyan]running[/cyan] {engine} on {f['name']} ...")
            stats = _run_and_measure(cmd)

            success = stats["returncode"] == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0
            out_size = os.path.getsize(out_path) if os.path.exists(out_path) else 0

            log_path = out_path + ".log"
            with open(log_path, "w", encoding="utf-8") as log_fh:
                log_fh.write(f"cmd: {cmd}\n")
                log_fh.write(f"returncode: {stats['returncode']}\n")
                log_fh.write(f"elapsed_sec: {stats['elapsed_sec']:.3f}\n\n")
                log_fh.write("--- stdout ---\n")
                log_fh.write(stats["stdout_text"])
                log_fh.write("\n--- stderr ---\n")
                log_fh.write(stats["stderr_text"])

            row = {
                "order": "->".join(engine_order),
                "engine": engine,
                "file": f["name"],
                "success": success,
                "elapsed_sec": round(stats["elapsed_sec"], 3),
                "avg_cpu_percent": round(stats["avg_cpu_percent"], 1),
                "peak_cpu_percent": round(stats["peak_cpu_percent"], 1),
                "peak_rss_mb": round(stats["peak_rss_mb"], 1),
                "phase_read_sec": stats.get("phase_read_sec"),
                "phase_crypto_sec": stats.get("phase_crypto_sec"),
                "phase_write_sec": stats.get("phase_write_sec"),
                "output_size_bytes": out_size,
                "error": "" if success else stats["stderr"],
                "log_file": log_path,
            }
            writer.writerow(row)
            csv_fh.flush()

            result_label = "[green]OK" if success else "[red]FAIL"

            def _fmt_phase(v):
                return f"{v:.2f}" if v is not None else "-"

            table.add_row(
                engine, f["name"],
                result_label,
                f"{row['elapsed_sec']:.1f}",
                f"{row['avg_cpu_percent']:.1f}",
                f"{row['peak_cpu_percent']:.1f}",
                f"{row['peak_rss_mb']:.0f}",
                _fmt_phase(row["phase_read_sec"]),
                _fmt_phase(row["phase_crypto_sec"]),
                _fmt_phase(row["phase_write_sec"]),
            )

    csv_fh.close()
    console.print(table)
    console.print(f"\n[bold]Results appended to[/bold] {CSV_PATH}")
    console.print(f"[bold]Outputs kept in[/bold] {os.path.join(WORK_DIR, 'outputs')}\\<engine>\\<file>")


if __name__ == "__main__":
    run(("bento4", "shaka", "flux"))
    run(("flux", "shaka", "bento4"))
