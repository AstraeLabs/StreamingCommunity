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

# Shaka Player demo asset "ClearKey with raw multiple keys" (Axinom test
# vector, demo/common/assets.js) — DASH/MP4/CENC with THREE distinct KIDs
# (one per quality tier), unlike CLEARKEY.py's single-key asset. Exercises
# per-track KID -> key resolution across a real multi-key manifest.
mpd_url = "https://media.axprod.net/TestVectors/MultiKey/Dash_h264_1080p_cenc/manifest.mpd"
mpd_headers = {}
license_url = ""
license_headers = {}

license_key = (
    "426d1a3278fd4f22873068db3974dda9:36bd3359241d4ba6f9cba62c1e041e01,"
    "9dc8e80acbfa41c3984fb6043440391a:495a038c79dd5af4290f0850435832e5,"
    "41baa59969054fc0a8c6355dcd1ab39f:02ee51601e6cd506846de4468f22ad7f"
)


dash_process = DASH_Downloader(
    mpd_url=mpd_url,
    mpd_headers=mpd_headers,
    license_url=license_url,
    license_headers=license_headers,
    output_path=rf".\Video\DASH_MULTIKEY.{conf_extension}",
    key=license_key,
    drm_preference=DRMType.CLEARKEY,
)


out_path, need_stop, error = dash_process.start()
print(f"Output path: {out_path}, Need stop: {need_stop}, error: {error}")
