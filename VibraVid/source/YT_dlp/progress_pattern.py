# 10.01.26

import re

PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)%")
SPEED_RE = re.compile(r"(\d+(?:\.\d+)?(?:[KMGT]i?B/s|(?:MB|KB|GB|B)ps))")
SIZE_RE = re.compile(
    r"(\d+(?:\.\d+)?(?:MiB|GiB|KiB|MB|GB|KB|B)?)"
    r"/"
    r"(\d+(?:\.\d+)?(?:MiB|GiB|KiB|MB|GB|KB|B)?)"
)
TOT_SIZE_RE = re.compile(
    r"of\s+~?\s*(\d+(?:\.\d+)?(?:MiB|GiB|KiB|MB|GB|KB|B)?)"
)
SEGMENT_RE = re.compile(r"(\d+)/(\d+)")
YTDLP_PROGRESS_RE = re.compile(
    r"\[download\]\s+"
    r"(\d+(?:\.\d+)?)%\s+of\s*~?\s*"
    r"([\d.]+\s*(?:KiB|MiB|GiB|TiB|KB|MB|GB|TB|B))"
    r"(?:\s+at\s+([\d.]+\s*(?:[KMGT]i?B/s|(?:MB|KB|GB|B)ps)|Unknown B/s))?"
    r"(?:\s+ETA\s+([\d:]+|Unknown))?"
)
DEST_RE = re.compile(r"\[download\]\s+Destination:\s+(.+)")
MERGER_RE = re.compile(r"\[Merger\]")
SUBTITLE_WRITE_RE = re.compile(
    r"\[(?:info|download)\].*?(?:subtitle|sub).*?:\s*(.+\.(?:vtt|srt|ass|ttml|dfxp))",
    re.IGNORECASE,
)