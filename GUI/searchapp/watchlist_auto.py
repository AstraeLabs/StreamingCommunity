# 12.02.26

import json
import os
import sys
import threading
import time
from typing import Optional

from django.db import close_old_connections
from django.utils import timezone

from .api import get_api
from .api.base import Entries
from .models import WatchlistItem


DEFAULT_INTERVAL_SECONDS = 14400

_loop_started = False
_loop_lock    = threading.Lock()


def _get_interval_seconds() -> int:
    raw = os.environ.get("WATCHLIST_AUTO_INTERVAL_SECONDS", "")
    try:
        value = int(raw)
        if value > 0:
            return value
    except Exception:
        pass
    return DEFAULT_INTERVAL_SECONDS


def _get_season_episode_count(seasons, season_number: int) -> Optional[int]:
    for season in seasons:
        if int(season.number) == int(season_number):
            return season.episode_count
    return None


def _should_start_loop() -> bool:
    return any(cmd in sys.argv for cmd in ("runserver", "runserver_plus"))


def _process_item(item: WatchlistItem, force: bool = False) -> None:
    """Process a single watchlist item for auto-download.
    
    Args:
        item: The watchlist item to process.
        force: If True, download even if episode count hasn't changed.
    """
    try:
        print(f"[WatchlistAuto] Processing '{item.name}' (id={item.id}, season={item.auto_season}, last_count={item.auto_last_episode_count})")

        api = get_api(item.source_alias)
        item_payload = json.loads(item.item_payload)
        entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
        media_item = Entries(**entries_fields)

        if media_item.is_movie or item.is_movie:
            print(f"[WatchlistAuto] '{item.name}': movies are not eligible for auto-download")
            return

        seasons = api.get_series_metadata(media_item)

        if not seasons:
            print(f"[WatchlistAuto] '{item.name}': no seasons returned from API")
            return

        season_number = item.auto_season
        if not season_number:
            print(f"[WatchlistAuto] '{item.name}': no auto_season set")
            return

        current_count = _get_season_episode_count(seasons, season_number)
        if current_count is None:
            print(f"[WatchlistAuto] '{item.name}': season {season_number} not found in metadata")
            return

        print(f"[WatchlistAuto] '{item.name}': season {season_number} has {current_count} episodes (stored: {item.auto_last_episode_count})")

        now = timezone.now()

        # Always download the selected season — the downloader itself skips already-downloaded files
        if True:
            from .views import _run_download_in_thread

            media_type = (item_payload.get("type") or "tv").lower()
            print(f"[WatchlistAuto] '{item.name}': starting download S{season_number} episodes=* media_type={media_type}")
            _run_download_in_thread(
                site=item.source_alias,
                item_payload=item_payload,
                season=str(season_number),
                episodes="*",
                media_type=media_type,
            )
            item.auto_last_episode_count = current_count
            item.auto_last_downloaded_at = now
            item.has_new_episodes = True

        item.auto_last_checked_at = now
        item.last_checked_at = now
        item.save(
            update_fields=[
                "auto_last_episode_count",
                "auto_last_checked_at",
                "auto_last_downloaded_at",
                "has_new_episodes",
                "last_checked_at",
            ]
        )
    except Exception as exc:
        import traceback
        print(f"[WatchlistAuto] Error processing {item.id} '{item.name}': {exc}")
        traceback.print_exc()


def _auto_loop(interval_seconds: int) -> None:
    while True:
        try:
            close_old_connections()
            items = list(
                WatchlistItem.objects.filter(auto_enabled=True)
                .exclude(auto_season__isnull=True)
                .exclude(is_movie=True)
            )
            print(f"[WatchlistAuto] Periodic check: {len(items)} items with auto-download enabled")
            for item in items:
                _process_item(item)
        except Exception as exc:
            print(f"[WatchlistAuto] Loop error: {exc}")
        time.sleep(_get_interval_seconds())


def run_watchlist_auto_once(force: bool = True) -> None:
    """Run auto-download scan once. force=True downloads even without new episodes."""
    try:
        close_old_connections()
        items = list(
            WatchlistItem.objects.filter(auto_enabled=True)
            .exclude(auto_season__isnull=True)
            .exclude(is_movie=True)
        )
        print(f"[WatchlistAuto] Manual trigger: {len(items)} items with auto-download enabled (force={force})")
        if not items:
            print("[WatchlistAuto] No items have auto-download enabled with a season selected.")
            return
        for item in items:
            _process_item(item, force=force)
    except Exception as exc:
        import traceback
        print(f"[WatchlistAuto] One-shot error: {exc}")
        traceback.print_exc()


def start_watchlist_auto_loop() -> None:
    global _loop_started
    if not _should_start_loop():
        return
    # Guard against Django autoreloader calling AppConfig.ready() twice
    # in the same process, which would start duplicate background threads.
    with _loop_lock:
        if _loop_started:
            print("[WatchlistAuto] Loop already running in this process, skipping duplicate start.")
            return
        _loop_started = True

    interval_seconds = _get_interval_seconds()
    thread = threading.Thread(
        target=_auto_loop,
        args=(interval_seconds,),
        daemon=True,
        name="WatchlistAutoLoop",
    )
    thread.start()
