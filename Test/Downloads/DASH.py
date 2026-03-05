# 29.07.25
# ruff: noqa: E402

import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(src_path)


from VibraVid.utils import config_manager
from VibraVid.core.downloader import DASH_Downloader


conf_extension = config_manager.config.get("PROCESS", "extension")
mpd_url = ''
mpd_headers = {}
license_url = ''
license_headers = {}
license_key = None

dash_process = DASH_Downloader(
    mpd_url=mpd_url,
    mpd_headers=mpd_headers,
    license_url=license_url,
    license_headers=license_headers,
    output_path=fr".\Video\Prova.{conf_extension}",
    key=license_key,
    drm_preference="widevine"
)

out_path, need_stop = dash_process.start()
print(f"Output path: {out_path}, Need stop: {need_stop}")