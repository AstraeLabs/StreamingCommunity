# 29.07.25
# ruff: noqa: E402

import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(src_path)


from VibraVid.utils import config_manager
from VibraVid.utils import setup_logger
from VibraVid.core.downloader.ism import ISM_Downloader
from VibraVid.core.drm.system import DRMType


setup_logger()
conf_extension = config_manager.config.get("PROCESS", "extension")


ism_url = ""
ism_headers = {}
license_url = ""
license_headers = {}
license_key = None


dash_process = ISM_Downloader(
    ism_url=ism_url,
    headers=ism_headers,
    license_url=license_url,
    license_headers=license_headers,
    key=license_key,
    output_path=rf".\Video\ISM.{conf_extension}",
    drm_preference=DRMType.WIDEVINE
)


out_path, need_stop, error = dash_process.start()
print(f"Output path: {out_path}, Need stop: {need_stop}, error: {error}")
