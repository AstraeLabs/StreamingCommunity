# 14.08.26
# ruff: noqa: E402

import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(src_path)

from VibraVid.utils import config_manager
from VibraVid.utils import setup_logger
from VibraVid.core.downloader import DASH_Downloader
from VibraVid.core.drm.system import DRMType


setup_logger()
conf_extension = config_manager.config.get("PROCESS", "extension")

# HbbTV RefApp test stream — same "Tears of Steel, HEVC" title as
# HEVC_CENC.py, same KID/key, but the *cbcs* variant of the manifest (AES-CBC
# pattern encryption instead of AES-CTR) — exercises the cbcs decrypt path
# with HEVC specifically, distinct from the AV1_CENC.py cbcs test.
mpd_url = "https://refapp.hbbtv.org/videos/tears_of_steel_h265_v9/cbcs/manifest_ckcenc.mpd"
mpd_headers = {}
license_url = ""
license_headers = {}

license_key = "43215678123412341234123412341237:12341234123412341234123412341237"


dash_process = DASH_Downloader(
    mpd_url=mpd_url,
    mpd_headers=mpd_headers,
    license_url=license_url,
    license_headers=license_headers,
    output_path=rf".\Video\HEVC_CBCS.{conf_extension}",
    key=license_key,
    drm_preference=DRMType.CLEARKEY,
)


out_path, need_stop, error = dash_process.start()
print(f"Output path: {out_path}, Need stop: {need_stop}, error: {error}")
