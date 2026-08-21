# 23.06.24
# ruff: noqa: E402

import os
import sys
import time

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(src_path)


from VibraVid.utils import setup_logger
from VibraVid.core.downloader import MP4_Downloader

t0 = time.monotonic()
setup_logger()
path, kill_handler, error = MP4_Downloader(
    url="", 
    path=r".\Video\MP.mp4", 
    headers={},
    key=None
)

thereIsError = path is None or error is not None
print(f"out={path} need_stop={kill_handler} error={error} elapsed={time.monotonic() - t0:.2f}s")
