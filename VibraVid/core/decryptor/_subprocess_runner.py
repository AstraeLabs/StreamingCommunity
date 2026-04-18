# 01.04.26

import os
import subprocess
import threading
import time
from typing import Callable, Dict, Any, Optional

from VibraVid.core.ui.bar_manager import console


def _render_bar(percent: int, length: int = 10) -> str:
    """Return a Rich-formatted inline progress bar string."""
    filled = int((percent / 100) * length)
    bar = (
        "[dim][[/dim]"
        + f"[green]{'=' * filled}[/green]"
        + f"[dim]{'-' * (length - filled)}[/dim]"
        + "[dim]][/dim]"
    )
    return f"{bar} [dim]{percent:3d}%[/dim]"


def run_with_progress(cmd: list, label: str, encrypted_path: str, output_path: str, progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None) -> tuple:
    """
    Launch *cmd* as a subprocess and monitor its progress by watching how
    fast the *output_path* file grows relative to *encrypted_path*.

    Progress is reported either via *progress_cb* (dict with ``task_key``,
    ``pct``, etc.) or by printing an inline Rich bar to the console.

    Returns:
        ``True`` on success (exit-code 0 and output file > 1 kB).
        ``(False, stderr_text)`` on failure.
    """
    file_size             = os.path.getsize(encrypted_path) if os.path.isfile(encrypted_path) else 0
    progress_percent      = 0
    last_rendered_percent = -1
    stop_monitor          = threading.Event()
    last_progress_update  = time.monotonic()
    last_observed_percent = -1
    process_holder        = {"process": None}
    task_key              = f"decrypt_{os.path.basename(output_path)}"

    def _emit(percent: int, current_size: int) -> None:
        if progress_cb is None:
            return
        
        try:
            progress_cb(
                {
                    "task_key":        task_key,
                    "label":           label,
                    "pct":             percent,
                    "segments":        f"{percent}/100",
                    "compact_metrics": True,
                }
            )
        except Exception:
            pass

    def _monitor() -> None:
        nonlocal progress_percent, last_progress_update, last_observed_percent
        while not stop_monitor.is_set():
            now     = time.monotonic()
            process = process_holder["process"]
            running = process is not None and process.poll() is None

            if os.path.exists(output_path) and file_size > 0:
                current_size     = os.path.getsize(output_path)
                observed_percent = min(int((current_size / file_size) * 100), 99)
                if observed_percent != last_observed_percent:
                    last_observed_percent = observed_percent
                    progress_percent      = observed_percent
                    last_progress_update  = now
                    _emit(progress_percent, current_size)

                elif running and progress_percent < 99 and now - last_progress_update >= 0.10:
                    progress_percent     = min(progress_percent + 1, 99)
                    last_progress_update = now
                    _emit(progress_percent, current_size)

            elif running and progress_percent < 95 and now - last_progress_update >= 0.10:
                progress_percent     = min(progress_percent + 1, 95)
                last_progress_update = now
                _emit(progress_percent, 0)

            time.sleep(0.03)

    stderr_lines: list[str] = []
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,)
        process_holder["process"] = process

        monitor_thread = threading.Thread(target=_monitor, daemon=True)
        monitor_thread.start()

        def _read_stderr() -> None:
            for line in process.stderr:
                stderr_lines.append(line)

        stderr_thread = threading.Thread(target=_read_stderr, daemon=True)
        stderr_thread.start()

        if progress_cb is None:
            console.print(f"{label} {_render_bar(0)}", end="\r")
        else:
            _emit(0, 0)

        while process.poll() is None:
            if progress_cb is None and progress_percent != last_rendered_percent:
                console.print(f"{label} {_render_bar(progress_percent)}", end="\r")
                last_rendered_percent = progress_percent
            time.sleep(0.05)

        process.wait()
        stderr_thread.join(timeout=2)

    except Exception as exc:
        stop_monitor.set()
        return False, str(exc)
    finally:
        stop_monitor.set()

    final_percent = 100 if process.returncode == 0 else progress_percent
    final_size    = os.path.getsize(output_path) if os.path.exists(output_path) else 0

    if progress_cb is None:
        console.print(f"{label} {_render_bar(final_percent)}")
    else:
        _emit(final_percent, final_size)

    time.sleep(0.3)

    if process.returncode == 0 and os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
        return True

    stderr_text = "".join(stderr_lines).strip()
    return False, stderr_text