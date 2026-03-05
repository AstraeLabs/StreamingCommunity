# 25.06.20
# ruff: noqa: E402

import os
import sys

src_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.append(src_path)


from VibraVid.core.downloader import MEGA_Downloader

mega = MEGA_Downloader(
    choose_files=True
)

output_path = mega.download_url(
    url="",
    dest_path=r".\Video\Movie\Prova.mp4",
)
print(output_path)