# 24.01.26

from rich import box
from rich.table import Table

from VibraVid.utils import internet_manager

from ..utils.object import StreamInfo
from ..utils.trans_codec import get_channel_layout_name


def build_table(streams, selected: set, cursor: int, window_size: int = 12, highlight_cursor: bool = True):
    """Build and return the current table view"""
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="cyan",
        border_style="blue",
        padding=(0, 1)
    )

    cols = [("#", "cyan"), ("Type", "cyan"), ("Sel", "green"),
        ("Resolution", "yellow"), ("FrameRate", "yellow"), ("Bitrate", "yellow"), ("Codec", "green"),
        ("Channels", "blue"), 
        ("Language", "blue"), ("Name", "green"), 
        ("Duration", "magenta")
    ]
    for col, color in cols:
        table.add_column(col, style=color, justify="right" if col in ("#",) else "left")

    total = len(streams)
    half = max(1, window_size // 2)
    start = max(0, cursor - half)
    end = min(total, start + window_size)
    if end - start < window_size:
        start = max(0, end - window_size)

    if start > 0:
        table.add_row("...", "", "", "", "", "", "", "", "", "", "")

    for visible_idx in range(start, end):
        s: StreamInfo = streams[visible_idx]

        idx = visible_idx
        is_selected = idx in selected
        is_cursor = (idx == cursor) and highlight_cursor
        bitrate = s.bandwidth
        if bitrate in ("0 bps", "N/A"):
            bitrate = ''
        if is_cursor:
            style = "bold white on blue"
        else:
            style = "dim" if idx % 2 == 1 else None
        
        table.add_row(
            str(idx + 1),
            f"{s.type}",
            "X" if is_selected else "",
            s.resolution if s.type == "Video" else "",
            str(s.frame_rate) if s.frame_rate and s.frame_rate != 0 else "",
            bitrate,
            s.get_short_codec(),
            get_channel_layout_name(s.channels) if s.channels else "",
            s.language or '',
            s.name or '',
            internet_manager.format_time(s.total_duration, add_hours=True) if s.total_duration > 0 else "N/A",
            style=style
        )

    if end < total:
        table.add_row("...", "", "", "", "", "", "", "", "", "", "")
    return table