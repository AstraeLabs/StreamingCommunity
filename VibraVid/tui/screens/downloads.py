# 30.07.26

"""Downloads screen: live progress panel with per-track bars, status badges, cancel/retry."""

import logging

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Button, DataTable, Footer, Header, Static

from VibraVid.core.ui.tracker import download_tracker

logger = logging.getLogger(__name__)


def make_progress_bar(percentage: float, width: int = 12) -> str:
    """Create a block progress bar string [████████░░] 80.0%."""
    pct = max(0.0, min(100.0, percentage))
    filled_len = int(width * pct / 100.0)
    bar = "█" * filled_len + "░" * (width - filled_len)
    return f"[{bar}] {pct:5.1f}%"


def format_status_badge(status: str) -> str:
    """Format status into a high-visibility semantic badge."""
    st = str(status).lower()
    if "run" in st or "down" in st or "active" in st:
        return "[bold cyan]● RUNNING[/bold cyan]"
    elif "comp" in st or "done" in st or "finish" in st:
        return "[bold green]✓ DONE[/bold green]"
    elif "fail" in st or "err" in st:
        return "[bold red]✖ FAILED[/bold red]"
    elif "stop" in st or "cancel" in st:
        return "[bold yellow]⏸ STOPPED[/bold yellow]"
    else:
        return f"[dim]⏳ {status.upper()}[/dim]"


class DownloadsScreen(Screen):
    """Live download progress panel with cancel/retry controls."""

    def __init__(self) -> None:
        super().__init__()
        self._refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="downloads-panel"):
            yield Static("Active & Recent Downloads", classes="panel-title")
            yield DataTable(id="downloads-table")
            yield Static("Track Details & Streams", classes="panel-title")
            yield DataTable(id="tasks-table")
            with Horizontal(id="downloads-actions"):
                yield Button("Cancel Selected", id="cancel-btn", disabled=True, variant="warning")
                yield Button("Retry Failed", id="retry-btn", disabled=True, variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        main_table = self.query_one("#downloads-table", DataTable)
        main_table.add_columns("ID", "Title", "Site", "Status", "Progress Bar", "Speed", "Size", "Segments")
        main_table.cursor_type = "row"
        main_table.show_cursor = True

        tasks_table = self.query_one("#tasks-table", DataTable)
        tasks_table.add_columns("Track / Stream", "Progress Bar", "Speed", "Size", "Segments")

        self._refresh_timer = self.set_interval(0.5, self._refresh_downloads)
        self._refresh_downloads()

    def on_unmount(self) -> None:
        if self._refresh_timer:
            self._refresh_timer.stop()

    def _refresh_downloads(self) -> None:
        active = download_tracker.get_active_downloads()
        main_table = self.query_one("#downloads-table", DataTable)
        tasks_table = self.query_one("#tasks-table", DataTable)

        main_table.clear()
        for dl in active:
            dl_id = str(dl.get("id", "?"))[:8]
            title = str(dl.get("title", "?"))[:38]
            site = str(dl.get("site", "?"))
            status = format_status_badge(str(dl.get("status", "?")))
            progress = make_progress_bar(float(dl.get("progress", 0)))
            speed = str(dl.get("speed", "0B/s"))
            size = str(dl.get("size", "0B/0B"))
            segments = str(dl.get("segments", "0/0"))
            main_table.add_row(dl_id, title, site, status, progress, speed, size, segments, key=dl.get("id"))

        tasks_table.clear()
        if main_table.cursor_row is not None and active:
            row_keys = list(main_table.rows.keys())
            if main_table.cursor_row < len(row_keys):
                row_key = row_keys[main_table.cursor_row]
                dl = next((d for d in active if d.get("id") == row_key), None)
                if dl:
                    tasks = dl.get("tasks", {})
                    for task_key, task_data in tasks.items():
                        label = task_data.get("label", task_key)
                        progress = make_progress_bar(float(task_data.get("progress", 0)))
                        speed = str(task_data.get("speed", "0B/s"))
                        size = str(task_data.get("size", "0B/0B"))
                        segments = str(task_data.get("segments", "0/0"))
                        tasks_table.add_row(label, progress, speed, size, segments)

        cancel_btn = self.query_one("#cancel-btn", Button)
        retry_btn = self.query_one("#retry-btn", Button)
        cancel_btn.disabled = not active
        failed = [dl for dl in active if dl.get("status") in ("failed", "cancelled")]
        retry_btn.disabled = not failed

    @on(DataTable.RowSelected, "#downloads-table")
    def _on_row_selected(self) -> None:
        self._refresh_downloads()

    @on(Button.Pressed, "#cancel-btn")
    def _on_cancel(self) -> None:
        main_table = self.query_one("#downloads-table", DataTable)
        if main_table.cursor_row is None:
            return
        row_keys = list(main_table.rows.keys())
        if main_table.cursor_row < len(row_keys):
            row_key = row_keys[main_table.cursor_row]
            download_tracker.request_stop(row_key)
            self.app.notify(f"Cancel requested for {row_key[:8]}", severity="information")

    @on(Button.Pressed, "#retry-btn")
    def _on_retry(self) -> None:
        self.app.notify("Retry triggered from active tracker", severity="information")