# 12.06.26

import os
import time
import logging
from typing import Optional, Tuple

from VibraVid.utils.config import config_manager
from VibraVid.utils import internet_manager
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.ui.bar_manager import DownloadBarManager

from VibraVid.utils.vault_upload.store import upload_vault

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    try:
        return config_manager.config.get_bool("HOOKS", "db_store")
    except Exception:
        return True

def _can_upload() -> bool:
    try:
        token = config_manager.config.get_dict("HOOKS", "db_info", default={}).get("token", "")
        return bool(token and str(token).strip())
    except Exception:
        return False

def _skip_if_cached() -> bool:
    try:
        return bool(config_manager.config.get_dict("HOOKS", "db_info", default={}).get("skip_if_cached", False))
    except Exception:
        return False

def _meta() -> Tuple[Optional[str], str, int, int]:
    title = context_tracker.title
    media_type = context_tracker.media_type or "Film"
    season = context_tracker.season or 0
    episode = context_tracker.episode or 0
    if season or episode:
        media_type = "TV"
    
    return title, media_type, season, episode

def _run_with_bar(label: str, run):
    if getattr(context_tracker, "is_gui", False):
        return run(None)

    bm = DownloadBarManager()
    start = time.time()
    with bm:
        def on_progress(done, total=None):
            elapsed = time.time() - start
            speed = done / elapsed if elapsed > 0 else 0
            pct = (done / total * 100) if total else 0
            bm.handle_progress_line({
                "task_key": "uploaddb",
                "label": label,
                "pct": pct,
                "speed": internet_manager.format_transfer_speed(speed) if speed > 0 else "0Bps",
                "size": (f"{internet_manager.format_file_size(done)}/{internet_manager.format_file_size(total) if total else '?'}"),
                "segments": f"{int(pct)}/100",
            })
        result = run(on_progress)
        bm.finish_all_tasks()
        return result

def try_fetch(output_path: str) -> bool:
    if not _enabled() or not output_path:
        return False
    
    try:
        if not upload_vault:
            return False
        
        title, media_type, season, episode = _meta()
        if not title:
            return False
        
        hit = upload_vault.search(title, media_type, season or None, episode or None)
        if not hit:
            return False

        got = _run_with_bar("[green]Cache[/green] [cyan]Downloading[/cyan]", lambda cb: upload_vault.download(hit["xh"], output_path, on_progress=cb),)
        if got:
            logger.info(f"upload-store hit: {title} S{season}E{episode} -> {output_path}")
            return True

        upload_vault.report_error(hit["xh"])
        return False
    except Exception as e:
        logger.debug(f"upload-store fetch skipped: {e}")
        return False

def is_cached() -> bool:
    if not _enabled() or not _skip_if_cached():
        return False

    try:
        if not upload_vault:
            return False

        title, media_type, season, episode = _meta()
        if not title:
            return False

        return bool(upload_vault.search(title, media_type, season or None, episode or None))
    except Exception as e:
        logger.debug(f"upload-store cache check skipped: {e}")
        return False

def upload_after(output_path: str) -> None:
    if not _enabled() or not output_path:
        return

    if not _can_upload():
        logger.debug("upload-store: no token configured, download-only mode (upload skipped)")
        return

    title, media_type, season, episode = _meta()
    if not title:
        return
    
    try:
        if not upload_vault or not os.path.isfile(output_path):
            return
        
        print()
        category = "live" if str(media_type).lower() == "live" else None
        _run_with_bar("[green]Cache[/green] [cyan]Uploading[/cyan]", lambda cb: upload_vault.upload(
                output_path, title=title, media_type=media_type, category=category,
                season=season or None, episode=episode or None, on_progress=cb,
            ),
        )
    except Exception as e:
        logger.debug(f"upload-store upload skipped: {e}")