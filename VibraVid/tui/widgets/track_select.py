# 30.07.26
# by @ManoloZocco

"""Track selection widget: DataTable with multi-select for video/audio/subtitle streams."""

import logging

from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import DataTable, Static

from VibraVid.core.ui.ui import sort_streams_key

logger = logging.getLogger(__name__)


class TrackSelect(Widget):
    """DataTable with multi-select for stream tracks (video/audio/subtitle)."""

    class Confirmed(Message):
        """Posted when the user confirms the selection with ENTER."""

        def __init__(self, selected_indices: set[int], control) -> None:
            super().__init__()
            self.selected_indices = selected_indices
            self.control = control

    BINDINGS = [
        Binding("space", "toggle_selection", "Toggle"),
        Binding("enter", "confirm", "Confirm"),
    ]

    def __init__(self, streams: list, **kwargs) -> None:
        super().__init__(**kwargs)
        self._streams = sorted(streams, key=sort_streams_key)
        self._selected: set[int] = set()
        # Pre-select streams marked as selected
        for idx, stream in enumerate(self._streams):
            if getattr(stream, "selected", False):
                self._selected.add(idx)

    def compose(self) -> ComposeResult:
        yield Static("Select tracks to download (SPACE to toggle, ENTER to confirm)", classes="panel-title")
        yield DataTable(id="track-table")

    def on_mount(self) -> None:
        table = self.query_one("#track-table", DataTable)
        table.add_columns("Sel", "Type", "DRM", "Resolution", "Bitrate", "Codec", "Channels", "HDR", "Language")
        table.cursor_type = "row"
        table.show_cursor = True

        for idx, stream in enumerate(self._streams):
            stype_raw = getattr(stream, "type", "")
            sel = "✓" if idx in self._selected else ""
            drm = ""
            if hasattr(stream, "drm") and stream.drm:
                if stream.drm.is_encrypted() or stream.drm.get_all_drm_types():
                    drm = stream.drm.get_drm_display()

            res = ""
            hdr = ""
            if stype_raw == "video":
                res = getattr(stream, "resolution", "")
                hdr = stream.get_hdr_display() if hasattr(stream, "get_hdr_display") else ""

            bitrate = ""
            if hasattr(stream, "bitrate_display"):
                bitrate = stream.bitrate_display if stream.bitrate else ""
            elif hasattr(stream, "bandwidth"):
                bw = stream.bandwidth
                bitrate = "" if bw in ("0 bps", "N/A") else bw

            codec = stream.get_short_codec() if hasattr(stream, "get_short_codec") else ""
            channels = ""
            if hasattr(stream, "channels"):
                from VibraVid.core.utils.codec import get_channel_label
                channels = get_channel_label(stream.channels) if stream.channels else ""

            language = ""
            if hasattr(stream, "language"):
                lang = stream.language
                if lang not in ("und", ""):
                    language = lang

            table.add_row(sel, stype_raw, drm, res, bitrate, codec, channels, hdr, language, key=str(idx))

    # ── Actions ───────────────────────────────────────────────────────────

    def action_toggle_selection(self) -> None:
        table = self.query_one("#track-table", DataTable)
        if table.cursor_row is None:
            return
        idx = int(table.get_row(table.cursor_row)[0]) if table.cursor_row is not None else -1
        # Get the row key from cursor position
        row_key = list(table.rows.keys())[table.cursor_row] if table.cursor_row < len(table.rows) else None
        if row_key is None:
            return
        idx = int(getattr(row_key, "value", row_key))
        if idx in self._selected:
            self._selected.discard(idx)
            table.update_cell_at((table.cursor_row, 0), "")
        else:
            self._selected.add(idx)
            table.update_cell_at((table.cursor_row, 0), "✓")

    def action_confirm(self) -> None:
        if self._selected:
            self.post_message(self.Confirmed(self._selected, self))
        else:
            self.app.notify("Select at least one track first.", severity="warning")

    # ── Public API ────────────────────────────────────────────────────────

    def get_selected_streams(self) -> list:
        """Return the list of selected stream objects."""
        return [self._streams[idx] for idx in sorted(self._selected)]
