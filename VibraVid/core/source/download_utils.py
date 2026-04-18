# 01.04.24

from pathlib import Path


def normalize_path_key(path_value: str) -> str:
    """
    Return a canonical, case-folded absolute path string suitable for use as
    a dict key when comparing paths across the Python/C# boundary.

    Always returns a str (empty string when *path_value* is falsy).
    """
    if not path_value:
        return ""
    return str(Path(path_value).resolve(strict=False)).casefold()


def format_size(nb: int) -> str:
    """
    Format *nb* bytes as a compact human-readable string.

    Examples::

        format_size(0)             -> "0B"
        format_size(1_500)         -> "1KB"
        format_size(2_097_152)     -> "2.0MB"
        format_size(1_073_741_824) -> "1.00GB"
    """
    if nb >= 1_073_741_824:
        return f"{nb / 1_073_741_824:.2f}GB"
    if nb >= 1_048_576:
        return f"{nb / 1_048_576:.1f}MB"
    if nb >= 1_024:
        return f"{nb / 1_024:.0f}KB"
    return f"{nb}B"


def format_speed(bps: float) -> str:
    """
    Format *bps* (bytes per second) as a compact human-readable string.

    Returns ``"---"`` for non-positive values (including NaN / -inf).
    """
    if bps <= 0:
        return "---"
    if bps >= 1_048_576:
        return f"{bps / 1_048_576:.2f}MB/s"
    if bps >= 1_024:
        return f"{bps / 1_024:.0f}KB/s"
    return f"{bps:.0f}B/s"


def estimate_total_size(completed_bytes: int, done_segs: int, total_segs: int) -> int:
    """
    Linearly extrapolate total download size from completed segments.

    Returns *completed_bytes* unchanged when either counter is non-positive
    (i.e. when the estimate would be meaningless or division by zero).
    """
    if done_segs <= 0 or total_segs <= 0:
        return completed_bytes
    return int((completed_bytes / done_segs) * total_segs)