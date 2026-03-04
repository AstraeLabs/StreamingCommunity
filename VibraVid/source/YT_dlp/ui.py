# 10.01.26

from rich import box
from rich.table import Table
from ..utils.object import StreamInfo


def build_table(streams: list, selected: set, cursor: int, window_size: int = 15, highlight_cursor: bool = True) -> Table:
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="cyan",
        border_style="blue",
        padding=(0, 1),
    )

    cols = [
        ("#",          "cyan"),
        ("Type",       "cyan"),
        ("Sel",        "green"),
        ("ID",         "dim"),
        ("Ext",        "dim"),
        ("Resolution", "yellow"),
        ("FPS",        "yellow"),
        ("Bitrate",    "yellow"),
        ("Codec",      "green"),
        ("Language",   "blue"),
    ]
    for col, color in cols:
        table.add_column(
            col, style=color,
            justify="right" if col == "#" else "left",
        )

    total = len(streams)
    half  = max(1, window_size // 2)
    start = max(0, cursor - half)
    end   = min(total, start + window_size)
    if end - start < window_size:
        start = max(0, end - window_size)

    if start > 0:
        table.add_row(*["…"] + [""] * (len(cols) - 1))

    for idx in range(start, end):
        s: StreamInfo = streams[idx]

        is_selected = idx in selected
        is_cursor   = (idx == cursor) and highlight_cursor

        style = "bold white on blue" if is_cursor else ("dim" if idx % 2 == 1 else None)

        # Bitrate
        rate = ""
        try:
            bps = int(s.raw_bandwidth or 0)
            if bps >= 1_000_000:
                rate = f"{bps / 1_000_000:.2f} Mbps"
            elif bps >= 1_000:
                rate = f"{bps / 1_000:.0f} kbps"
            elif bps > 0:
                rate = f"{bps} bps"
        except (ValueError, TypeError):
            rate = s.bandwidth or ""

        type_color = {"Video": "red", "Audio": "green", "Subtitle": "yellow"}.get(s.type, "white")
        sel_color  = "green" if is_selected else type_color

        # FPS only for video
        fps_str = ""
        if s.type == "Video" and s.frame_rate and s.frame_rate != 0:
            fps_str = str(int(s.frame_rate)) if s.frame_rate == int(s.frame_rate) else str(s.frame_rate)

        # Resolution only for video
        res_str = str(s.resolution or "") if s.type == "Video" else ""

        # Codec short form
        codec_str = s.get_short_codec() if hasattr(s, "get_short_codec") else (s.codec or "")

        # Language: for subtitles show name (may contain "(auto)")
        lang_str = str(s.name or s.language or "") if s.type == "Subtitle" else str(s.language or "")

        table.add_row(
            str(idx + 1),
            f"[{sel_color}]{s.type or ''}[/{sel_color}]",
            "X" if is_selected else "",
            str(getattr(s, "id", "") or ""),
            str(s.extension or ""),
            res_str,
            fps_str,
            rate,
            codec_str,
            lang_str,
            style=style,
        )

    if end < total:
        table.add_row(*["…"] + [""] * (len(cols) - 1))

    return table