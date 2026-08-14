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

# Shaka Player demo asset "Angel One (multicodec, multilingual, ClearKey server)"
# (github.com/shaka-project/shaka-player demo/common/assets.js). Manifest
# ContentProtection is urn:uuid:1077efec-c0b2-4d02-ace3-3c1e52e2fb4b
# (org.w3.clearkey) — real ClearKey DRM signalling, not Widevine/PlayReady.
mpd_url = "https://storage.googleapis.com/shaka-demo-assets/angel-one-clearkey/dash.mpd"
mpd_headers = {}
license_url = ""
license_headers = {}

# cwip-shaka-proxy's "license server" just echoes kid=key back in its URL query
# string (https://cwip-shaka-proxy.appspot.com/clearkey?_u3wDe7erb7v8Lqt8A3QDQ=ABEiM0RVZneImaq7zN3u_w),
# base64url -> hex: kid feedf00deedeadbeeff0baadf00dd00d (== the manifest's
# cenc:default_KID feedf00d-eede-adbe-eff0-baadf00dd00d), key 00112233445566778899aabbccddeeff.
license_key = "feedf00deedeadbeeff0baadf00dd00d:00112233445566778899aabbccddeeff"


dash_process = DASH_Downloader(
    mpd_url=mpd_url,
    mpd_headers=mpd_headers,
    license_url=license_url,
    license_headers=license_headers,
    output_path=rf".\Video\CLEARKEY.{conf_extension}",
    key=license_key,
    drm_preference=DRMType.CLEARKEY,
)


out_path, need_stop, error = dash_process.start()
print(f"Output path: {out_path}, Need stop: {need_stop}, error: {error}")
