# 23.06.24
# ruff: noqa: E402

import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(src_path)


from VibraVid.utils import config_manager
from VibraVid.core.downloader import HLS_Downloader

conf_extension = config_manager.config.get("PROCESS", "extension")

other_tracks = [
    {
        "type": "video:DV",
        "url": "",
        "language": "und",
        "name": "2160p DV",
    },
    {
        "type": "audio",
        "url": "",
        "language": "en",
        "name": "English E-AC3",
        "extension": "m4a",
        "default": True,
    },
    {
        "type": "subtitle",
        "url": "",
        "language": "en",
        "name": "English-Forced",
        "extension": "vtt",
        "forced": True,
    },
]

main_hdr10_track = ""

hls_process = HLS_Downloader(
    m3u8_url=main_hdr10_track,
    headers={},
    output_path=fr".\Video\Prova_Hybrid.{conf_extension}",
    key="kid:key|kid:key",
    other_tracks=other_tracks
)

out_path, need_stop = hls_process.start()
print(f"Downloaded to: {out_path}, Stopped: {need_stop}")
