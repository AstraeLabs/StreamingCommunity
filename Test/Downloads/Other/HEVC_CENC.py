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

# HbbTV RefApp test stream (refapp.hbbtv.org/videos/) — "Tears of Steel, HEVC"
# (hvc1), scheme cenc, KID=1237. ContentProtection uses the legacy
# pre-standardization ClearKey system ID urn:uuid:e2719d58-a985-b3c9-781a-b030af78d30e
# ("ClearKey v0.1", still used by real-world HbbTV content) rather than the
# finalized org.w3.clearkey (1077efec-...) CLEARKEY.py/WEBM.py
mpd_url = "https://refapp.hbbtv.org/videos/tears_of_steel_h265_v9/cenc/manifest_ckcenc.mpd"
mpd_headers = {}
license_url = ""
license_headers = {}

# keys_microsofttest.json ("Test1237"): kid.0/key.0, algid=CENC.
license_key = "43215678123412341234123412341237:12341234123412341234123412341237"


dash_process = DASH_Downloader(
    mpd_url=mpd_url,
    mpd_headers=mpd_headers,
    license_url=license_url,
    license_headers=license_headers,
    output_path=rf".\Video\HEVC_CENC.{conf_extension}",
    key=license_key,
    drm_preference=DRMType.CLEARKEY,
)


out_path, need_stop, error = dash_process.start()
print(f"Output path: {out_path}, Need stop: {need_stop}, error: {error}")
