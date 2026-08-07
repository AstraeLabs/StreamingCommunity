# 07.08.26
# ruff: noqa: E402

import os
import sys
import time
from pathlib import Path

workspace_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(workspace_root))

import resource  # noqa: E402  (POSIX-only, matches CI/runtime target)

from VibraVid.core.ui.bar_manager import DownloadBarManager

WALL_SECONDS = 50.0
TICK_INTERVAL = 0.3

TRACKS = [
    ("video", 465, "1920x1080"),
    ("audio_ita", 465, "it-IT"),
    ("audio_eng", 465, "en-US"),
]
SUB_TRACKS = ["sub_ita", "sub_eng", "sub_ita_forced"]


def fmt_size(b):
    return f"{b/1_048_576:.1f}M"


def fmt_speed(bps):
    return f"{bps/1_048_576:.2f}M/s"


def run_benchmark(wall_seconds: float = WALL_SECONDS) -> dict:
    devnull = open(os.devnull, "w")
    saved_stdout_fd = os.dup(1)
    os.dup2(devnull.fileno(), 1)  # keep rich's writes off the real terminal

    mgr = DownloadBarManager(download_id=None)

    with mgr:
        for key, total, label in TRACKS:
            mgr.add_prebuilt_tasks([(key, label)])
        for key in SUB_TRACKS:
            mgr.add_external_track_task(key, key)

        for key in SUB_TRACKS:
            mgr.handle_progress_line({"task_key": key, "pct": 100, "final_size": "40K"})

        start = time.perf_counter()
        rusage_start = resource.getrusage(resource.RUSAGE_SELF)

        next_tick = {k: 0.0 for k, _, _ in TRACKS}
        done = {k: 0 for k, _, _ in TRACKS}
        total_bytes = {k: 0 for k, _, _ in TRACKS}
        seg_bytes = 180_000

        elapsed = 0.0
        while elapsed < wall_seconds:
            t0 = time.perf_counter()
            for key, total, _label in TRACKS:
                if done[key] >= total:
                    continue
                if elapsed >= next_tick[key]:
                    next_tick[key] += TICK_INTERVAL
                    n_new = max(1, int(total / (wall_seconds / TICK_INTERVAL)))
                    for _ in range(n_new):
                        if done[key] >= total:
                            break
                        done[key] += 1
                        total_bytes[key] += seg_bytes
                        speed = total_bytes[key] / max(elapsed, 0.1)
                        mgr.handle_progress_line(
                            {
                                "task_key": key,
                                "pct": int(done[key] / total * 100),
                                "segments": f"{done[key]}/{total}",
                                "size": f"{fmt_size(total_bytes[key])}/{fmt_size(seg_bytes*total)}",
                                "speed": fmt_speed(speed),
                                "final_size": fmt_size(seg_bytes),
                            }
                        )
                    mgr.handle_progress_line(
                        {
                            "task_key": key,
                            "pct": int(done[key] / total * 100),
                            "segments": f"{done[key]}/{total}",
                            "size": f"{fmt_size(total_bytes[key])}/{fmt_size(seg_bytes*total)}",
                            "speed": fmt_speed(total_bytes[key] / max(elapsed, 0.1)),
                        }
                    )
            dt = time.perf_counter() - t0
            time.sleep(max(0.0, 0.02 - dt))
            elapsed = time.perf_counter() - start

        rusage_end = resource.getrusage(resource.RUSAGE_SELF)
        wall = time.perf_counter() - start

    os.dup2(saved_stdout_fd, 1)
    os.close(saved_stdout_fd)
    devnull.close()

    user_cpu = rusage_end.ru_utime - rusage_start.ru_utime
    sys_cpu = rusage_end.ru_stime - rusage_start.ru_stime
    return {
        "wall_seconds": wall,
        "user_cpu_seconds": user_cpu,
        "sys_cpu_seconds": sys_cpu,
        "total_cpu_seconds": user_cpu + sys_cpu,
        "cpu_pct_of_1core": (user_cpu + sys_cpu) / wall * 100,
    }


if __name__ == "__main__":
    result = run_benchmark()
    print(
        "wall={wall_seconds:.2f}s user_cpu={user_cpu_seconds:.3f}s sys_cpu={sys_cpu_seconds:.3f}s "
        "total_cpu={total_cpu_seconds:.3f}s cpu_pct_of_1core={cpu_pct_of_1core:.1f}%".format(**result)
    )
