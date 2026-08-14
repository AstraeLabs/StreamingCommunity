# 14.08.26
# ruff: noqa: E402

import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.append(src_path)

from VibraVid.utils import config_manager
from VibraVid.utils import setup_logger
from VibraVid.core.downloader import DASH_Downloader
from VibraVid.core.drm.system import DRMType


setup_logger()
conf_extension = config_manager.config.get("PROCESS", "extension")

# Axinom "Tears of Steel" test vector, raw single-key ClearKey CENC — one of the
# assets Shaka Player's own demo app uses (demo/common/assets.js, "ClearKey with
# raw single key"). Manifest itself carries no license URL, just the CENC PSSH;
# the kid:key pair below is published alongside it, so no license server round-trip.
mpd_url = "https://media.axprod.net/TestVectors/Dash/protected_dash_1080p_h264_singlekey/manifest.mpd"
mpd_headers = {}
license_url = ""
license_headers = {}
license_key = "4060a865887842679cbf91ae5bae1e72:fc35340837310cc0fb53de97e22a69e0"


dash_process = DASH_Downloader(
    mpd_url=mpd_url,
    mpd_headers=mpd_headers,
    license_url=license_url,
    license_headers=license_headers,
    output_path=rf".\Video\DASH_CLEARKEY.{conf_extension}",
    key=license_key,
    drm_preference=DRMType.CLEARKEY,
)


out_path, need_stop, error = dash_process.start()
print(f"Output path: {out_path}, Need stop: {need_stop}, error: {error}")
