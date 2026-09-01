from pathlib import Path

from VibraVid.core.direct_download.adapter import _resolve_direct_download_root
from VibraVid.utils import config_manager


def test_resolve_direct_download_root_uses_my_downloader_folder(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    def fake_get(section, key, *args, **kwargs):
        if section == "OUTPUT" and key == "root_path":
            return "Video"
        return kwargs.get("default")

    monkeypatch.setattr(config_manager.config, "get", fake_get)

    result = _resolve_direct_download_root()

    assert result == tmp_path / "Video" / "MyDownloader"
    assert result.exists()
