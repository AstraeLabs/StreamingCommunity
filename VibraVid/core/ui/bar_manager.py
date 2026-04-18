# 13.03.26

import platform
from contextlib import nullcontext
from typing import Any, Dict, Optional

from rich.console import Console
from rich.progress import Progress, TextColumn

from VibraVid.core.ui.tracker import download_tracker, context_tracker
from VibraVid.core.ui.progress_bar import (CustomBarColumn, ColoredSegmentColumn, CompactTimeColumn, CompactTimeRemainingColumn, TransferStatsColumn)


console = Console(force_terminal=True if platform.system().lower() != "windows" else None)


class DownloadBarManager:
    def __init__(self, download_id: Optional[str] = None):
        self.download_id = download_id
        self.tasks: Dict[str, Any] = {}
        self.subtitle_sizes: Dict[str, str] = {}
        
        self.progress_ctx = (
            nullcontext()
            if context_tracker.is_gui
            else Progress(
                TextColumn("[purple]{task.description}", justify="left"),
                CustomBarColumn(bar_width=40),
                ColoredSegmentColumn(),
                TextColumn("[dim][[/dim]"), CompactTimeColumn(), ("[dim]<[/dim]"), CompactTimeRemainingColumn(), TextColumn("[dim]][/dim]"),
                TransferStatsColumn(),
                console=console,
                refresh_per_second=10.0,
            )
        )
        self.progress: Optional[Progress] = None

    def __enter__(self):
        self.progress = self.progress_ctx.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.progress_ctx:
            self.progress_ctx.__exit__(exc_type, exc_val, exc_tb)

    def add_prebuilt_tasks(self, prebuilt_tasks):
        """Pre-crates tasks to maintain order."""
        if self.progress:
            for task_key, task_label in prebuilt_tasks:
                if task_key not in self.tasks:
                    # If task_label already contains Rich markup (starts with [), use it as-is otherwise wrap it with [cyan] for consistency
                    final_label = task_label if task_label.startswith("[") else f"[cyan]{task_label}[/cyan]"
                    initial_segment = "0/100" if task_key.startswith("decrypt_") else "0/0"
                    compact_metrics = task_key.startswith("decrypt_")
                    self.tasks[task_key] = self.progress.add_task(
                        final_label,
                        total=100,
                        segment=initial_segment,
                        speed="" if compact_metrics else "0Bps",
                        size="" if compact_metrics else "0B/0B",
                        compact_metrics=compact_metrics,
                    )
                    
    def add_external_track_task(self, label: str, track_key: str):
        if self.progress:
            if track_key not in self.tasks:
                self.tasks[track_key] = self.progress.add_task(
                    f"[cyan]{label}",
                    total=100, segment="0/1", speed="0Bps", size="0B/0B", compact_metrics=False,
                )
                
    def get_task_id(self, task_key: str):
        return self.tasks.get(task_key)

    def handle_progress_line(self, parsed: Optional[Dict[str, Any]]):
        if not parsed:
            return

        key = parsed.get("task_key") or parsed.get("_task_key") or f"{parsed.get('track', 'trk')}_{parsed.get('label', '')}"
        label = parsed.get("label", key)

        # ── Create task if first time we see this key ──────────────────────
        if key not in self.tasks:
            compact_metrics = bool(parsed.get("compact_metrics")) or key.startswith("decrypt_")
            self.tasks[key] = (
                self.progress.add_task(
                    f"[cyan]{label}",
                    total=100,
                    segment="0/0",
                    speed="" if compact_metrics else "0Bps",
                    size="" if compact_metrics else "0B/0B",
                    compact_metrics=compact_metrics,
                )
                if self.progress else "gui"
            )

        # ── Update tracker (for GUI mode) ──────────────────────────────────
        if self.download_id:
            download_tracker.update_progress(
                self.download_id, key,
                parsed.get("pct"),
                parsed.get("speed"),
                parsed.get("size"),
                parsed.get("segments"),
                label=label,
                display_label=parsed.get("display_label"),
            )

        # ── Update Rich progress bar ───────────────────────────────────────
        if not self.progress or self.tasks.get(key) == "gui":
            return

        tid = self.tasks[key]
        if "compact_metrics" in parsed:
            self.progress.update(tid, compact_metrics=bool(parsed["compact_metrics"]))

        if "pct" in parsed:
            try:
                self.progress.update(tid, completed=parsed["pct"])
            except Exception:
                pass
        if "speed" in parsed and not parsed.get("compact_metrics"):
            self.progress.update(tid, speed=parsed["speed"])
        if "size" in parsed and not parsed.get("compact_metrics"):
            self.progress.update(tid, size=parsed["size"])
        if "segments" in parsed:
            self.progress.update(tid, segment=parsed["segments"])

        # Subtitle completion
        if "final_size" in parsed:
            self.progress.update(tid, size=parsed["final_size"], completed=100)
            lang_raw = parsed.get("_lang_code") or key.replace("sub_", "", 1).split("_")[0]
            codec    = parsed.get("codec", "")
            if lang_raw:
                self.subtitle_sizes[f"{lang_raw}:{codec}" if codec else lang_raw] = parsed["final_size"]

    def finish_all_tasks(self):
        if self.progress:
            for tid in self.tasks.values():
                if tid != "gui":
                    self.progress.update(tid, completed=100)