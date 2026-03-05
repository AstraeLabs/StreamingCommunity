# 29.07.25
# ruff: noqa: E402

import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(src_path)


from VibraVid.utils import config_manager
from VibraVid.core.downloader import ISM_Downloader


conf_extension = config_manager.config.get("PROCESS", "extension")
ism_url = 'https://test.playready.microsoft.com/media/profficialsite/tearsofsteel_4k.ism.smoothstreaming/manifest'
ism_headers = {}
license_url = 'http://test.playready.microsoft.com/service/rightsmanager.asmx?cfg=(persist:false,sl:150)'
license_headers = {}
license_key = None

ism_process = ISM_Downloader(
    ism_url=ism_url,
    ism_headers=ism_headers,
    license_url=license_url,
    license_headers=license_headers,
    output_path=fr".\Video\Prova.{conf_extension}",
    key=license_key,
    drm_preference="playready"
)

out_path, need_stop = ism_process.start()
print(f"Output path: {out_path}, Need stop: {need_stop}")