# 14.08.26
# ruff: noqa: E402

import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.append(src_path)

from VibraVid.utils import config_manager
from VibraVid.utils import setup_logger
from VibraVid.core.downloader import HLS_Downloader


setup_logger()
conf_extension = config_manager.config.get("PROCESS", "extension")

# Shaka Player demo asset "Angel One (HLS, MP4, SAMPLE-AES-CTR, multi-key)"
# (github.com/shaka-project/shaka-player demo/common/assets.js). Video and
# audio are two SEPARATE CENC tracks with two DIFFERENT keys — a case the
# single-key DASH clearkey tests (CLEARKEY.py, DASH_CLEARKEY.py) don't cover:
# resolving the right key to the right track by KID.
#
# The manifest's #EXT-X-KEY only carries a raw key via a data: URI (no KID
# — HLS's KEYFORMAT="identity" convention), e.g. for video:
#   #EXT-X-KEY:METHOD=SAMPLE-AES-CTR,URI="data:text/plain;base64,q7onHovPVSu9LoakNKml2Q==",KEYFORMAT="identity"
m3u8_url = "https://storage.googleapis.com/shaka-demo-assets/angel-one-sample-aes-ctr-multiple-key/manifest.m3u8"
m3u8_headers = {}

keys = (
    "a4631a153a443df9eed0593043db7519:a4631a153a443df9eed0593043db7519,"  # audio
    "abba271e8bcf552bbd2e86a434a9a5d9:abba271e8bcf552bbd2e86a434a9a5d9"  # video
)


hls_process = HLS_Downloader(
    m3u8_url=m3u8_url,
    headers=m3u8_headers,
    output_path=rf".\Video\HLS_MULTIKEY.{conf_extension}",
    key=keys,
    max_segments=1000,
)


out_path, need_stop, error = hls_process.start()
print("Downloaded to:", out_path, "Stopped:", need_stop, "Error:", error)
