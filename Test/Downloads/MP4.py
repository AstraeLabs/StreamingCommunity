# 23.06.24
# ruff: noqa: E402

import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(src_path)


from VibraVid.core.downloader import MP4_Downloader


path, kill_handler = MP4_Downloader(
    url="",
    path=r".\Video\Prova.mp4"
)

thereIsError = path is None
print(thereIsError)