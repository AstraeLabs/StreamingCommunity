# 30.07.26

"""Downloads screen: live progress panel with per-track bars, cancel/retry."""

import logging

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Button, DataTable, Footer, Header, Static

from VibraVid.core.ui.tracker import download_tracker

logger = logging.getLogger(__name__)


class DownloadsScreen(Screen):
    """Live download progress panel with cancel/retry controls."""

    def __init__(self) -> None:
        super().__init__()
        self._refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="downloads-panel"):
            yield Static("Active Downloads", classes="panel-title")
            yield DataTable(id="downloads-table")
            yield Static("Per-track progress", classes="panel-title")
            yield DataTable(id="tasks-table")
            with Horizontal(id="downloads-actions"):
                yield Button("Cancel selected", id="cancel-btn", disabled=True)
                yield Button("Retry failed", id="retry-btn", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        # Main downloads table
        main_table = self.query_one("#downloads-table", DataTable)
        main_table.add_columns("ID", "Title", "Site", "Status", "Progress", "Speed", "Size", "Segments")
        main_table.cursor_type = "row"
        main_table.show_cursor = True

        # Tasks table (per-track progress)
        tasks_table = self.query_one("#tasks-table", DataTable)
        tasks_table.add_columns("Task", "Progress", "Speed", "Size", "Segments")

        # Refresh every 500ms
        self._refresh_timer = self.set_interval(0.5, self._refresh_downloads)
        self._refresh_downloads()

    def on_unmount(self) -> None:
        if self._refresh_timer:
            self._refresh_timer.stop()

    def _refresh_downloads(self) -> None:
        """Refresh the downloads table from download_tracker."""
        active = download_tracker.get_active_downloads()
        main_table = self.query_one("#downloads-table", DataTable)
        tasks_table = self.query_one("#tasks-table", DataTable)

        # Clear and rebuild main table
        main_table.clear()
        for dl in active:
            dl_id = dl.get("id", "?")[:8]
            title = dl.get("title", "?")[:40]
            site = dl.get("site", "?")
            status = dl.get("status", "?")
            progress = f"{dl.get('progress', 0):.1f}%"
            speed = dl.get("speed", "0B/s")
            size = dl.get("size", "0B/0B")
            segments = dl.get("segments", "0/0")
            main_table.add_row(dl_id, title, site, status, progress, speed, size, segments, key=dl.get("id"))

        # Update tasks table for selected download
        tasks_table.clear()
        if main_table.cursor_row is not None and active:
            row_key = list(main_table.rows.keys())[main_table.cursor_row] if main_table.cursor_row < len(main_table.rows) else None
            if row_key:
                dl = next((d for d in active if d.get("id") == row_key), None)
                if dl:
                    tasks = dl.get("tasks", {})
                    for task_key, task_data in tasks.items():
                        label = task_data.get("label", task_key)
                        progress = f"{task_data.get('progress', 0):.1f}%"
                        speed = task_data.get("speed", "0B/s")
                        size = task_data.get("size", "0B/0B")
                        segments = task_data.get("segments", "0/0")
                        tasks_table.add_row(label, progress, speed, size, segments)

        # Update button states
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
        row_key = list(main_table.rows.keys())[main_table.cursor_row] if main_table.cursor_row < len(main_table.rows) else None
        if row_key:
            download_tracker.request_stop(row_key)
            self.app.notify(f"Cancel requested for {row_key[:8]}", severity="information")

    @on(Button.Pressed, "#retry-btn")
    def _on_retry(self) -> None:
        # TODO M3: implement retry from queue/history
        self.app.notify("Retry not yet implemented (M3)", severity="warning")