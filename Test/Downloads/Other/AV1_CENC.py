# 14.08.26
# ruff: noqa: E402

import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
sys.path.append(src_path)

from VibraVid.utils import config_manager, setup_logger
from VibraVid.core.downloader import MP4_Downloader


setup_logger()
conf_extension = config_manager.config.get("PROCESS", "extension")


URL = "https://s3.amazonaws.com/download.opencontent.netflix.com/AV1/cmaf/cosmos-10b-24fps/cosmos_687kbps_540p.mp4"

# KID/KEY from the issue, base64url -> hex:
#   KeyID AAAAAATz9TEAAAAAAAAAAA -> 0000000004f3f5310000000000000000... (32 hex chars)
#   Key   PeAIZjJZLz3-yAIwrj152A -> 3de0086632592f3dfec80230ae3d79d8
KEY = "0000000004f3f5310000000000000000:3de0086632592f3dfec80230ae3d79d8"

path, kill_handler, error = MP4_Downloader(
    url=URL,
    path=rf".\Video\AV1_CENC.{conf_extension}",
    headers_={},
    key=KEY,
)

thereIsError = path is None or error is not None
print("Output:", path, "Stopped:", kill_handler, "Error:", error)
print(thereIsError)
