# 23.06.24
# ruff: noqa: E402

import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(src_path)


from VibraVid.utils import config_manager
from VibraVid.core.downloader import HLS_Downloader


conf_extension = config_manager.config.get("PROCESS", "extension")
hls_process =  HLS_Downloader(
    m3u8_url="",
    headers={},
    output_path=fr".\Video\Prova.{conf_extension}",
)

out_path, need_stop = hls_process.start()
print("Downloaded to:", out_path, "Stopped:", need_stop)