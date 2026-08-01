# 23.06.24
# ruff: noqa: E402


import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(src_path)


from VibraVid.utils import config_manager
from VibraVid.utils import setup_logger
from VibraVid.core.downloader import HLS_Downloader
from VibraVid.core.drm.system import DRMType


setup_logger()
conf_extension = config_manager.config.get("PROCESS", "extension")


m3u8_url = ""
m3u8_headers = {}


hls_process =  HLS_Downloader(
    m3u8_url=m3u8_url,
    headers=m3u8_headers,
    output_path=fr".\Video\HLS.{conf_extension}",
    key=None
)


out_path, need_stop, error = hls_process.start()
print("Downloaded to:", out_path, "Stopped:", need_stop, "Error:", error)
