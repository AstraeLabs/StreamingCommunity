# 30.07.26

"""History screen: past downloads viewer with status, paths, timestamps and errors."""

import datetime
import logging
import uuid
from typing import Any, Dict, List, Optional

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import Button, DataTable, Header, Static
from VibraVid.tui.widgets.custom_footer import CustomFooter

from VibraVid.cli.command.equivalent_command import EquivalentCommandBuilder
from VibraVid.cli.command.queue import (
    _PROCESS_TAG,
    _QueueLock,
    _load_queue,
    _now_iso,
    _queue_path,
    _save_queue,
)
from VibraVid.core.ui.tracker import download_tracker

logger = logging.getLogger(__name__)


def _format_time(ts: Any) -> str:
    if not ts:
        return "-"
    try:
        if isinstance(ts, (int, float)):
            return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        return str(ts)
    except Exception:
        return str(ts)


class HistoryScreen(Screen):
    """Past download history panel."""

    def __init__(self) -> None:
        super().__init__()
        self._refresh_timer: Optional[Timer] = None
        self._history_items: List[Dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="history-panel"):
            yield Static("Download History", classes="panel-title")
            yield Static(
                "Past downloads (last 50 items loaded from download_tracker & history cache)",
                id="history-status-text",
                classes="detail-meta",
            )
            yield DataTable(id="history-table")
            yield Static("History Item Details", classes="panel-title")
            yield Static(
                "Select an item above to view details.",
                id="history-item-detail",
                classes="history-detail-box",
            )
            with Horizontal(id="history-actions"):
                yield Button("Refresh", id="refresh-btn")
                yield Button("Re-enqueue item", id="reenqueue-btn")
                yield Button("Clear history", id="clear-btn")
        yield CustomFooter()

    def on_mount(self) -> None:
        table = self.query_one("#history-table", DataTable)
        table.add_columns("ID", "Title", "Site", "Type", "Status", "Output Path", "Finished")
        table.cursor_type = "row"
        table.show_cursor = True

        self._refresh_timer = self.set_interval(2.0, self._refresh_history)
        self._refresh_history()

    def on_unmount(self) -> None:
        if self._refresh_timer:
            self._refresh_timer.stop()

    def _refresh_history(self) -> None:
        """Reload history items from download_tracker."""
        items = download_tracker.get_history()
        self._history_items = items

        table = self.query_one("#history-table", DataTable)
        current_cursor = table.cursor_row
        table.clear()

        for dl in items:
            item_id = str(dl.get("id", "?"))[:8]
            title = str(dl.get("title", "?"))[:35]
            site = str(dl.get("site", "?"))
            media_type = str(dl.get("type", "Film"))
            status = str(dl.get("status", "unknown"))
            path = str(dl.get("path") or "-")
            path_short = path if len(path) <= 40 else "..." + path[-37:]
            end_time = _format_time(dl.get("end_time") or dl.get("last_update"))

            if status == "completed":
                status_fmt = f"[green]{status}[/green]"
            elif status in ("failed", "timed_out"):
                status_fmt = f"[red]{status}[/red]"
            elif status == "cancelled":
                status_fmt = f"[yellow]{status}[/yellow]"
            else:
                status_fmt = f"[white]{status}[/white]"

            table.add_row(
                item_id,
                title,
                site,
                media_type,
                status_fmt,
                path_short,
                end_time,
                key=str(dl.get("id")),
            )

        if current_cursor is not None and current_cursor < len(items):
            table.cursor_row = current_cursor

        counts = {}
        for dl in items:
            st = dl.get("status", "unknown")
            counts[st] = counts.get(st, 0) + 1

        summary = (
            f"Total: {len(items)} items  ·  "
            f"[green]Completed: {counts.get('completed', 0)}[/green]  ·  "
            f"[red]Failed: {counts.get('failed', 0)}[/red]  ·  "
            f"[yellow]Cancelled: {counts.get('cancelled', 0)}[/yellow]"
        )
        self.query_one("#history-status-text", Static).update(summary)

        self._update_item_detail()
        self._update_buttons()

    def _update_item_detail(self) -> None:
        table = self.query_one("#history-table", DataTable)
        detail_box = self.query_one("#history-item-detail", Static)

        if table.cursor_row is None or not self._history_items or table.cursor_row >= len(self._history_items):
            detail_box.update("Select an item above to view details.")
            return

        dl = self._history_items[table.cursor_row]
        lines = [
            f"[bold cyan]ID:[/] {dl.get('id', '?')}   [bold cyan]Site:[/] {dl.get('site', '?')}   [bold cyan]Type:[/] {dl.get('type', '?')}",
            f"[bold cyan]Title:[/] {dl.get('title', '?')}",
            f"[bold cyan]Status:[/] {dl.get('status', '?')}   [bold cyan]Progress:[/] {dl.get('progress', 0):.1f}%",
            f"[bold cyan]Output Path:[/] {dl.get('path') or '-'}",
            f"[bold cyan]Start Time:[/] {_format_time(dl.get('start_time'))}   [bold cyan]End Time:[/] {_format_time(dl.get('end_time'))}",
        ]

        if dl.get("error"):
            lines.append(f"[bold red]Error Details:[/] {dl.get('error')}")

        quality = dl.get("quality")
        language = dl.get("language")
        if quality or language:
            lines.append(f"[bold cyan]Quality/Lang:[/] {quality or '-'} / {language or '-'}")

        tasks = dl.get("tasks", {})
        if tasks:
            lines.append(f"[bold cyan]Tasks ({len(tasks)}):[/] " + ", ".join(f"{k}: {v.get('progress', 0):.1f}%" for k, v in tasks.items()))

        detail_box.update("\n".join(lines))

    def _update_buttons(self) -> None:
        clear_btn = self.query_one("#clear-history-btn", Button)
        retry_btn = self.query_one("#retry-history-btn", Button)

        clear_btn.disabled = len(self._history_items) == 0

        table = self.query_one("#history-table", DataTable)
        if table.cursor_row is not None and table.cursor_row < len(self._history_items):
            retry_btn.disabled = False
        else:
            retry_btn.disabled = True

    @on(DataTable.RowSelected, "#history-table")
    @on(DataTable.RowHighlighted, "#history-table")
    def _on_row_changed(self) -> None:
        self._update_item_detail()
        self._update_buttons()

    # ── Actions ───────────────────────────────────────────────────────────

    @on(Button.Pressed, "#refresh-btn")
    def _on_refresh(self) -> None:
        self._refresh_history()
        self.app.notify("History refreshed", severity="information")

    @on(Button.Pressed, "#clear-history-btn")
    def _on_clear_history(self) -> None:
        try:
            download_tracker.clear_history()
            self._refresh_history()
            self.app.notify("Cleared download history", severity="information")
        except Exception as e:
            self.app.notify(f"Could not clear history: {e}", severity="error")

    @on(Button.Pressed, "#retry-history-btn")
    def _on_retry_history(self) -> None:
        table = self.query_one("#history-table", DataTable)
        if table.cursor_row is None or table.cursor_row >= len(self._history_items):
            return

        dl = self._history_items[table.cursor_row]
        site = dl.get("site")
        title = dl.get("title")

        if not site or not title:
            self.app.notify("Missing site or title information to retry.", severity="warning")
            return

        builder = EquivalentCommandBuilder(excluded_dests=[])
        argv = builder.build_argv_from_params(site=site, search=title, item="1")

        if not argv:
            self.app.notify("Could not construct equivalent command to retry.", severity="error")
            return

        tag = _PROCESS_TAG
        path = _queue_path(tag)
        item = {
            "id": uuid.uuid4().hex[:8],
            "argv": argv,
            "status": "pending",
            "tag": tag,
            "enqueued_at": _now_iso(),
            "started_at": None,
            "finished_at": None,
            "returncode": None,
            "attempts": 0,
        }

        try:
            with _QueueLock(path):
                data = _load_queue(path)
                data.setdefault("items", []).append(item)
                _save_queue(path, data)
            self.app.notify(f"Re-queued download '{title[:25]}' ({item['id']})", severity="information")
        except Exception as e:
            self.app.notify(f"Failed to re-queue download: {e}", severity="error")
