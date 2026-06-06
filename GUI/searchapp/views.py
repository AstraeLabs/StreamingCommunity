# 06.06.25

import os
import re
import sys
import time
import json
import threading
import atexit
import signal
import logging
import zipfile
import shutil
import importlib
import concurrent.futures
from typing import Any, Dict, List, Optional

from django.db.models import Q
from django.shortcuts import render, redirect
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.utils import timezone


from .forms import SearchForm, DownloadForm
from .models import ArrMediaRequest, ArrProcessingQueue, WatchlistItem
from .watchlist_auto import _get_interval_seconds
from GUI.searchapp.api import get_api, get_available_sites, get_site_categories
from GUI.searchapp.api.base import Entries

from VibraVid.core.ui.tracker import  download_tracker, context_tracker
from VibraVid.utils import config_manager
from VibraVid.provider.tmdb import tmdb_client
from VibraVid.cli.run import execute_hooks


logger = logging.getLogger(__name__)

# Track recently processed webhooks to avoid duplicates (tmdbId -> timestamp)
_recent_webhooks = {}  # (source, tmdbId) -> timestamp (float)
_recent_webhooks_lock = threading.Lock()
_WEBHOOK_DEDUP_WINDOW = 300  # 5 minutes
_GLOBAL_SEARCH_TIMEOUT = 45


def _is_recent_webhook(tmdb_id, source=None, window_seconds=None, touch=True):
    """Return True if (source, tmdb_id) was processed in the last window_seconds.

    Uses (source, tmdb_id) as key so that different webhook sources
    (seerr vs sonarr vs radarr) don't block each other.
    """
    if not tmdb_id:
        return False
    if window_seconds is None:
        window_seconds = _WEBHOOK_DEDUP_WINDOW
    now = time.time()
    key = (source, str(tmdb_id))
    with _recent_webhooks_lock:
        last_time = _recent_webhooks.get(key)
        if last_time and (now - last_time) < window_seconds:
            return True
        if touch:
            _recent_webhooks[key] = now
        # Clean old entries
        old_keys = [k for k, v in _recent_webhooks.items() if now - v > window_seconds]
        for k in old_keys:
            del _recent_webhooks[k]
        return False


def _mark_native_webhook_seen(tmdb_id, source: str):
    if not tmdb_id:
        return
    _is_recent_webhook(tmdb_id, source=source, window_seconds=_WEBHOOK_DEDUP_WINDOW, touch=True)


download_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix="DownloadWorker")
scheduled_downloads: Dict[str, Dict[str, Any]] = {}
scheduled_downloads_lock = threading.Lock()
cancelled_scheduled_downloads: set[str] = set()

# ── Download concurrency limiter ──────────────────────────
_download_slot_cond = threading.Condition()
_active_downloads = 0
_max_download_slots = 1


def set_max_download_slots(n: int) -> None:
    global _max_download_slots
    _max_download_slots = max(1, n)
    with _download_slot_cond:
        _download_slot_cond.notify_all()


def _acquire_download_slot() -> None:
    global _active_downloads
    with _download_slot_cond:
        while _active_downloads >= _max_download_slots:
            _download_slot_cond.wait()
        _active_downloads += 1


def _release_download_slot() -> None:
    global _active_downloads
    with _download_slot_cond:
        _active_downloads -= 1
        _download_slot_cond.notify()


def _add_scheduled_download(download_id: str, title: str, site: str, media_type: str = "Film", season: str = None, episodes: str = None) -> None:
    with scheduled_downloads_lock:
        scheduled_downloads[download_id] = {
            "id": download_id,
            "title": title,
            "site": site,
            "type": media_type,
            "season": season,
            "episodes": episodes,
            "scheduled_at": time.time(),
        }
        cancelled_scheduled_downloads.discard(download_id)


def _remove_scheduled_download(download_id: str) -> None:
    with scheduled_downloads_lock:
        scheduled_downloads.pop(download_id, None)
        cancelled_scheduled_downloads.discard(download_id)


def _cancel_scheduled_download(download_id: str) -> None:
    with scheduled_downloads_lock:
        cancelled_scheduled_downloads.add(download_id)
        scheduled_downloads.pop(download_id, None)


def _is_scheduled_cancelled(download_id: str) -> bool:
    with scheduled_downloads_lock:
        return download_id in cancelled_scheduled_downloads


def _extract_series_base_title(raw_title: str) -> str:
    """Normalize title to a stable series base name (strip season/episode suffixes)."""
    title = str(raw_title or "").strip()
    if not title:
        return ""
    # Examples: "Show - S1", "Show - S1 E3", "Show - S01 E01-02"
    base = re.split(r"\s-\sS\d+(?:\sE[\d\-\*,]+)?", title, maxsplit=1, flags=re.IGNORECASE)[0]
    return base.strip()


def _same_series(title: str, series_base: str) -> bool:
    if not series_base:
        return False
    return _extract_series_base_title(title).casefold() == series_base.casefold()


def _get_scheduled_downloads(exclude_ids: Optional[set] = None) -> List[Dict[str, Any]]:
    exclude_ids = exclude_ids or set()
    with scheduled_downloads_lock:
        return sorted(
            (item for item in scheduled_downloads.values() if item.get("id") not in exclude_ids),
            key=lambda item: item.get("scheduled_at", 0),
        )


def _enrich_active_downloads_with_series(active_downloads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Attach series_name for active TV downloads so GUI can show the parent series."""
    with scheduled_downloads_lock:
        scheduled_by_id = {k: dict(v) for k, v in scheduled_downloads.items()}

    enriched: List[Dict[str, Any]] = []
    for item in active_downloads:
        row = dict(item)
        media_type = str(row.get("type") or "").lower()

        if media_type in {"serie", "tv", "series", "anime"}:
            series_name = ""
            row_id = row.get("id")

            scheduled_info = scheduled_by_id.get(row_id)
            if scheduled_info:
                series_name = _extract_series_base_title(scheduled_info.get("title", ""))

            if not series_name:
                title = str(row.get("title") or "").strip()
                title_base = _extract_series_base_title(title)
                # Only trust title-derived series name when title contains the Sxx suffix pattern.
                if title_base and title_base != title:
                    series_name = title_base

            if series_name:
                row["series_name"] = series_name

        enriched.append(row)

    return enriched


def _prune_scheduled_downloads(_active_downloads: List[Dict[str, Any]], history: List[Dict[str, Any]]) -> None:
    history_ids = {item.get("id") for item in history if item.get("id")}
    now = time.time()
    max_age_seconds = 6 * 60 * 60

    with scheduled_downloads_lock:
        to_remove = []
        for download_id, item in scheduled_downloads.items():
            
            # Keep entries visible while not completed; remove only once they
            # reach history (completed/failed/cancelled) or become stale.
            if download_id in history_ids:
                to_remove.append(download_id)
                continue
            if now - float(item.get("scheduled_at", now)) > max_age_seconds:
                to_remove.append(download_id)

        for download_id in to_remove:
            scheduled_downloads.pop(download_id, None)
            cancelled_scheduled_downloads.discard(download_id)


def shutdown_downloads():
    """Shutdown downloads and kill processes on exit."""
    print("Shutting down downloads...")
    with scheduled_downloads_lock:
        scheduled_downloads.clear()
        cancelled_scheduled_downloads.clear()
    download_tracker.shutdown()
    download_executor.shutdown(wait=True)


def _submit_download_task(fn):
    """Submit a task to the download executor, recreating it if it was shutdown."""
    global download_executor
    try:
        return download_executor.submit(fn)
    except RuntimeError:
        # Executor has been shutdown (interpreter shutdown or explicit call). Recreate.
        try:
            download_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix="DownloadWorker")
            return download_executor.submit(fn)
        except Exception as exc:
            print(f"[Error] Could not recreate download executor: {exc}")
            raise


# Ensure downloads are shut down on exit
atexit.register(shutdown_downloads)


# Handle SIGINT and SIGTERM to shutdown properly
def signal_handler(signum, frame):
    shutdown_thread = threading.Thread(target=shutdown_downloads, daemon=True)
    shutdown_thread.start()

    print("Running post-run hooks...")
    execute_hooks('post_run')

    print("Downloads shutdown started, exiting immediately...")
    os._exit(0)


if threading.current_thread() is threading.main_thread():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)


def _media_item_to_display_dict(item: Entries, source_alias: str) -> Dict[str, Any]:
    """Convert Entries to template-friendly dictionary."""
    poster_url = item.poster if item.poster else "https://via.placeholder.com/300x450?text=Search"
    
    # Treat songs as 'movie' in the GUI so they show a direct Download button
    display_is_movie = bool(item.is_movie or (str(item.type or "").strip().lower() == 'song'))

    result = {
        'display_title': item.name,
        'display_type': item.type.capitalize(),
        'source': source_alias.capitalize(),
        'source_alias': source_alias,
        'bg_image_url': poster_url,
        'is_movie': display_is_movie,
        'year': item.year
    }

    # Ensure payload reflects the GUI behaviour (so start_download treats songs like single-item downloads)
    result['payload_json'] = json.dumps({**item.__dict__, 'is_movie': display_is_movie})
    return result


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@require_http_methods(["GET"])
def search_home(request: HttpRequest) -> HttpResponse:
    """Display search form."""
    form = SearchForm()
    return render(request, "searchapp/home.html", {"form": form})


def _resolve_global_sites(site_token: str) -> Optional[List[str]]:
    """Map a special dropdown token to a list of sites.

    Returns the target site list for a global/category search, or None when the value
    is a normal single-site identifier (the caller then does the classic search).
    """
    if site_token == "__all__":
        return get_available_sites()
    if site_token.startswith("__cat__:"):
        category = site_token.split(":", 1)[1]
        return [s for s, c in get_site_categories().items() if c == category]
    return None


def _run_global_search(query: str, sites: List[str]):
    """Search `query` across many sites in parallel.

    Returns (results, failed_sites). Each site runs in its own thread; a failure or
    timeout on one site is isolated and never blocks the others. Service searches are
    invoked via the adapters with get_onlyDatabase=True, so they never prompt.
    """
    results: List[Dict[str, Any]] = []
    failed: List[str] = []
    if not sites:
        return results, failed

    def _one(site: str):
        api = get_api(site)
        return [_media_item_to_display_dict(item, site) for item in api.search(query)]

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=min(len(sites), 8))
    future_map = {executor.submit(_one, s): s for s in sites}
    try:
        for fut in concurrent.futures.as_completed(future_map, timeout=_GLOBAL_SEARCH_TIMEOUT):
            site = future_map[fut]
            try:
                results.extend(fut.result())
            except Exception as e:
                logger.warning(f"[global_search] '{site}' failed: {e}")
                failed.append(site)
    
    except concurrent.futures.TimeoutError:
        for fut, site in future_map.items():
            if not fut.done():
                logger.warning(f"[global_search] '{site}' timed out after {_GLOBAL_SEARCH_TIMEOUT}s")
                failed.append(site)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    results.sort(key=lambda r: (str(r.get("display_title") or "").lower(), r.get("source_alias") or ""))
    return results, failed


@require_http_methods(["GET", "POST"])
def search(request: HttpRequest) -> HttpResponse:
    """Handle search requests."""
    if request.method == "POST":
        form = SearchForm(request.POST)
    else:
        query = request.GET.get('query')
        site = request.GET.get('site')
        if query and site:
            form = SearchForm({'query': query, 'site': site})
        else:
            return redirect("search_home")

    if not form.is_valid():
        messages.error(request, "Dati non validi")
        return render(request, "searchapp/home.html", {"form": form})

    site = form.cleaned_data["site"]
    query = form.cleaned_data["query"]

    # Global / per-category search across multiple services.
    global_sites = _resolve_global_sites(site)
    if global_sites is not None:
        results, failed = _run_global_search(query, global_sites)
        if failed:
            messages.warning(request, f"Alcune fonti non hanno risposto: {', '.join(failed)}")
        return render(
            request,
            "searchapp/results.html",
            {
                "form": SearchForm(initial={"site": site, "query": query}),
                "query": query,
                "download_form": DownloadForm(),
                "results": results,
                "selected_site": site,
                "is_global": True,
            },
        )

    try:
        api = get_api(site)
        media_items = api.search(query)
        results = [_media_item_to_display_dict(item, site) for item in media_items]
    except Exception as e:
        messages.error(request, f"Errore nella ricerca: {e}")
        return render(request, "searchapp/home.html", {"form": form})

    download_form = DownloadForm()
    return render(
        request,
        "searchapp/results.html",
        {
            "form": SearchForm(initial={"site": site, "query": query}),
            "query": query,
            "download_form": download_form,
            "results": results,
            "selected_site": site,
        },
    )


def _run_download_in_thread(site: str, item_payload: Dict[str, Any], season: str = None, episodes: str = None, media_type: str = "Film", output_path: str = None, audio_format: str = None) -> "concurrent.futures.Future":
    """Run download in background thread. Returns a Future for callers that need to wait.

    Args:
        output_path: If provided, tells VibraVid to download to this specific folder.
        audio_format: If provided, forwarded to providers that support format selection (e.g. Spotify).
    """
    name = item_payload.get('name', 'Unknown')
    if season and episodes:
        title = f"{name} - S{season} E{episodes}"
    elif season:
        title = f"{name} - S{season}"
    else:
        title = name

    download_id = f"{site}_{int(time.time())}_{hash(title) % 10000}"
    _add_scheduled_download(download_id, title, site, media_type, season, episodes)

    def _task():
        _acquire_download_slot()
        try:
            if _is_scheduled_cancelled(download_id):
                print("[_task] Download cancelled before start")
                _remove_scheduled_download(download_id)
                return

            # Set context for downloaders in this thread
            context_tracker.download_id = download_id
            context_tracker.site_name = site
            context_tracker.media_type = media_type
            context_tracker.is_gui = True
            context_tracker.is_cancelled_callback = _is_scheduled_cancelled
            context_tracker.season = int(season) if str(season or "").isdigit() else 0
            context_tracker.episode = int(episodes) if str(episodes or "").isdigit() else 0

            api = get_api(site)

            # Create Entries from payload
            entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
            media_item = Entries(**entries_fields)

            # audio_format override for providers that support it (e.g. Spotify)
            if audio_format:
                media_item.audio_format = audio_format

            # output_path stored in context so downstream helpers can read it
            if output_path:
                context_tracker.output_path = output_path

            print("[_task] Calling api.start_download with:")
            print(f"        season={season}, episodes={episodes}, output_path={output_path}, audio_format={audio_format}")
            try:
                api.start_download(media_item, season=season, episodes=episodes, audio_format=audio_format)
            except TypeError:
                api.start_download(media_item, season=season, episodes=episodes)

            final_state = next(
                (item for item in download_tracker.get_history() if item.get("id") == download_id),
                None,
            )
            if final_state and final_state.get("status") in {"failed", "cancelled", "timed_out"}:
                error_msg = final_state.get("error") or final_state.get("status") or "download_failed"
                raise RuntimeError(error_msg)

            print("[_task] ✓ Download completed successfully")
        except Exception as e:
            error_msg = str(e) or "Errore sconosciuto"
            print(f"[Error] Download task failed: {error_msg}")
            import traceback
            traceback.print_exc()

            try:
                _remove_scheduled_download(download_id)

                already_in_history = any(
                    item.get("id") == download_id
                    for item in download_tracker.get_history()
                )

                # Start it briefly only when nothing downstream already wrote
                # the final state to the tracker.
                if download_id not in download_tracker.downloads and not already_in_history:
                    download_tracker.start_download(download_id, title, site, media_type)

                if not already_in_history:
                    download_tracker.complete_download(download_id, success=False, error=error_msg)
            except Exception as tracker_err:
                print(f"[Error] Failed to update download tracker: {tracker_err}")
            raise
        finally:
            context_tracker.output_path = None
            context_tracker.season = 0
            context_tracker.episode = 0
            context_tracker.episode_name = None
            context_tracker.is_cancelled_callback = None
            _release_download_slot()

    return download_executor.submit(_task)


@require_http_methods(["POST"])
def series_metadata(request: HttpRequest) -> JsonResponse:
    """
    API endpoint to get series metadata (seasons/episodes).
    Returns JSON with series information.
    """
    try:
        # Parse request
        if request.content_type and "application/json" in request.content_type:
            body = json.loads(request.body.decode("utf-8"))
            source_alias = body.get("source_alias") or body.get("site")
            item_payload = body.get("item_payload") or {}
        else:
            source_alias = request.POST.get("source_alias") or request.POST.get("site")
            item_payload_raw = request.POST.get("item_payload")
            item_payload = json.loads(item_payload_raw) if item_payload_raw else {}

        if not source_alias or not item_payload:
            return JsonResponse({"error": "Parametri mancanti"}, status=400)

        # Get API instance
        api = get_api(source_alias)
        
        # Convert to Entries
        entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
        media_item = Entries(**entries_fields)
        
        # Check if it's a movie
        if media_item.is_movie:
            return JsonResponse({
                "isSeries": False,
                "seasonsCount": 0,
                "episodesPerSeason": {}
            })
        
        # Get series metadata
        seasons = api.get_series_metadata(media_item)
        
        if not seasons:
            return JsonResponse({
                "isSeries": False,
                "seasonsCount": 0,
                "episodesPerSeason": {}
            })
        
        # Build response
        episodes_per_season = {
            season.number: season.episode_count 
            for season in seasons
        }
        
        return JsonResponse({
            "isSeries": True,
            "seasonsCount": len(seasons),
            "episodesPerSeason": episodes_per_season
        })
        
    except Exception as e:
        return JsonResponse({"Error get metadata": str(e)}, status=500)


@require_http_methods(["POST"])
def start_download(request: HttpRequest) -> HttpResponse:
    """Handle download requests for movies or individual series selections."""
    form = DownloadForm(request.POST)
    if not form.is_valid():
        error_msg = f"Dati non validi: {form.errors.as_text()}"
        print(f"[Error] {error_msg}")
        messages.error(request, error_msg)
        return redirect("search_home")

    source_alias = form.cleaned_data["source_alias"]
    item_payload_raw = form.cleaned_data["item_payload"]
    season = form.cleaned_data.get("season") or None
    episode = form.cleaned_data.get("episode") or None
    audio_format = form.cleaned_data.get("audio_format") or None

    # Normalize
    if season:
        season = str(season).strip() or None
    if episode:
        episode = str(episode).strip() or None
    if audio_format:
        audio_format = str(audio_format).strip().lower() or None

    try:
        item_payload = json.loads(item_payload_raw)
    except Exception:
        messages.error(request, "Payload non valido")
        return redirect("search_home")

    # Determine media type
    item_type = str(item_payload.get("type") or "").lower()
    if item_type in ("song", "track", "music"):
        media_type = "Musica"
    elif item_type == "album":
        media_type = "Album"
    elif item_payload.get("is_movie"):
        media_type = "Film"
    else:
        media_type = "Serie"

    # Check for series episode selection
    if media_type == "Serie" and season and not episode:
        messages.error(request, "Seleziona almeno un episodio prima di scaricare!")

    # Run download
    _run_download_in_thread(source_alias, item_payload, season, episode, media_type, audio_format=audio_format)
    return redirect("download_dashboard")


@require_http_methods(["GET", "POST"])
def series_detail(request: HttpRequest) -> HttpResponse:
    """
    Show series detail page with seasons and episodes.
    Handles POST for full series, full season, or episode-specific downloads.
    """
    # --- POST: handle download requests ---
    if request.method == "POST":
        return _handle_series_download(request)
    
    # --- GET: show series detail page ---
    source_alias = request.GET.get("source_alias")
    item_payload_raw = request.GET.get("item_payload")
    
    if not source_alias or not item_payload_raw:
        messages.error(request, "Parametri mancanti.")
        return redirect("search_home")
    
    try:
        item_payload = json.loads(item_payload_raw)
        api = get_api(source_alias)
        entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
        media_item = Entries(**entries_fields)
        
        # Try to get TMDB backdrop for better background image
        backdrop_url = media_item.poster  # fallback to original poster
        if not media_item.is_movie:
            try:
                if media_item.tmdb_id:
                    backdrop = tmdb_client.get_backdrop_url('tv', int(media_item.tmdb_id), size="w1920")
                    if backdrop:
                        backdrop_url = backdrop
                
                else:
                    # Fallback to search by slug/year
                    slug = media_item.slug or tmdb_client._slugify(media_item.name)
                    year_str = str(media_item.year) if media_item.year else None
                    tmdb_result = tmdb_client.get_type_and_id_by_slug_year(slug, year_str, "tv")
                    if tmdb_result and tmdb_result.get('type') == 'tv':
                        backdrop = tmdb_client.get_backdrop_url('tv', tmdb_result['id'], size="w1920")
                        if backdrop:
                            backdrop_url = backdrop
                            
            except Exception:
                # If TMDB fails, keep original poster
                pass
        
        # Get series metadata
        seasons = api.get_series_metadata(media_item)
        
        if not seasons:
            messages.warning(request, "Impossibile caricare i dettagli delle stagioni al momento. Potrebbe essere dovuto a download attivi. Riprova tra qualche minuto.")
            seasons = []  # Allow page to load with empty seasons
        
        series_info = {
            "name": media_item.name,
            "poster": media_item.poster,        # original source poster
            "backdrop": backdrop_url,           # TMDB backdrop or fallback to poster
            "year": media_item.year,
            "source_alias": source_alias,
            "item_payload": item_payload_raw,
        }
        
        seasons_data = []
        for season in seasons:
            episodes_data = []

            # Enrich episodes with language list for better display
            for ep in season.episodes:
                ep_dict = ep.__dict__.copy()
                lang = ep_dict.get("language") or ""
                ep_dict["language_list"] = [language.strip() for language in lang.split(",") if language.strip()] if lang else []
                episodes_data.append(ep_dict)

            seasons_data.append({
                "number": season.number,
                "episode_count": season.episode_count,
                "episodes": episodes_data,
            })
        
        return render(
            request,
            "searchapp/series_detail.html",
            {
                "series": series_info,
                "seasons": seasons_data,
            }
        )
        
    except Exception as e:
        messages.error(request, f"Errore nel caricamento dei dettagli: {e}")
        return redirect("search_home")

def _handle_series_download(request: HttpRequest) -> HttpResponse:
    """Handle POST downloads from series_detail: full series, full season, or selected episodes."""
    source_alias = request.POST.get("source_alias")
    item_payload_raw = request.POST.get("item_payload")
    download_type = request.POST.get("download_type")
    season_number = request.POST.get("season_number")
    selected_episodes = request.POST.get("selected_episodes", "")

    if not all([source_alias, item_payload_raw]):
        messages.error(request, "Parametri base mancanti per il download.")
        return redirect("search_home")

    try:
        item_payload = json.loads(item_payload_raw)
    except Exception:
        messages.error(request, "Errore nel parsing dei dati.")
        return redirect("search_home")

    name = item_payload.get("name")
    media_type = (item_payload.get("type") or "tv").lower()

    # --- FULL SERIES DOWNLOAD (sequential, all seasons one after another) ---
    if download_type == "full_series":
        def _download_entire_series_task():
            try:
                api = get_api(source_alias)
                entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
                media_item = Entries(**entries_fields)
                seasons = api.get_series_metadata(media_item)

                if not seasons:
                    return

                planned_seasons = []
                for season in seasons:
                    season_num = str(season.number)
                    season_title = f"{name} - S{season_num}"
                    planned_id = f"{source_alias}_{int(time.time())}_{hash(season_title + str(season_num)) % 10000}_{season_num}"
                    planned_seasons.append((planned_id, season_num))
                    _add_scheduled_download(
                        planned_id,
                        season_title,
                        source_alias,
                        media_type,
                        season=season_num,
                        episodes="*",
                    )

                for download_id, season_num in planned_seasons:
                    if _is_scheduled_cancelled(download_id):
                        _remove_scheduled_download(download_id)
                        continue

                    # Respect the concurrency limit: acquire a slot per season and
                    # release it before moving on, so other queued downloads can run.
                    _acquire_download_slot()
                    try:
                        if _is_scheduled_cancelled(download_id):
                            _remove_scheduled_download(download_id)
                            continue

                        context_tracker.download_id = download_id
                        context_tracker.site_name = source_alias
                        context_tracker.media_type = media_type
                        context_tracker.is_gui = True
                        context_tracker.is_cancelled_callback = _is_scheduled_cancelled

                        api.start_download(media_item, season=season_num, episodes="*")
                    except Exception as e:
                        error_msg = str(e) or "Errore sconosciuto"
                        print(f"[Error] Download season {season_num}: {e}")

                        try:
                            _remove_scheduled_download(download_id)
                            if download_id not in download_tracker.downloads:
                                season_title = f"{name} - S{season_num}"
                                download_tracker.start_download(download_id, season_title, source_alias, media_type)
                            download_tracker.complete_download(download_id, success=False, error=error_msg)
                        except Exception as tracker_err:
                            print(f"[Error] Failed to update download tracker: {tracker_err}")
                    finally:
                        _release_download_slot()

            except Exception as e:
                print(f"[Error] Full series download task: {e}")

        _submit_download_task(_download_entire_series_task)

        return redirect("download_dashboard")

    # --- FULL SEASON DOWNLOAD ---
    elif download_type == "full_season":
        if not season_number:
            messages.error(request, "Numero stagione mancante.")
            return redirect("search_home")

        _run_download_in_thread(
            site=source_alias,
            item_payload=item_payload,
            season=season_number,
            episodes="*",
            media_type=media_type
        )

        return redirect("download_dashboard")

    # --- SELECTED SEASONS DOWNLOAD ---
    elif download_type == "selected_seasons":
        selected_seasons_raw = request.POST.get("selected_seasons", "")
        if not selected_seasons_raw:
            messages.error(request, "Nessuna stagione selezionata.")
            return redirect("search_home")
            
        selected_seasons = [s.strip() for s in selected_seasons_raw.split(",") if s.strip()]
        
        def _download_selected_seasons_task():
            try:
                api = get_api(source_alias)
                entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
                media_item = Entries(**entries_fields)
                
                planned_seasons = []
                for season_num in selected_seasons:
                    season_title = f"{name} - S{season_num}"
                    planned_id = f"{source_alias}_{int(time.time())}_{hash(season_title + str(season_num)) % 10000}_{season_num}"
                    planned_seasons.append((planned_id, season_num))
                    _add_scheduled_download(
                        planned_id,
                        season_title,
                        source_alias,
                        media_type,
                        season=season_num,
                        episodes="*",
                    )

                for download_id, season_num in planned_seasons:
                    if _is_scheduled_cancelled(download_id):
                        _remove_scheduled_download(download_id)
                        continue

                    # Respect the concurrency limit: acquire a slot per season and
                    # release it before moving on, so other queued downloads can run.
                    _acquire_download_slot()
                    try:
                        if _is_scheduled_cancelled(download_id):
                            _remove_scheduled_download(download_id)
                            continue

                        context_tracker.download_id = download_id
                        context_tracker.site_name = source_alias
                        context_tracker.media_type = media_type
                        context_tracker.is_gui = True
                        context_tracker.is_cancelled_callback = _is_scheduled_cancelled

                        api.start_download(media_item, season=season_num, episodes="*")
                    except Exception as e:
                        error_msg = str(e) or "Errore sconosciuto"
                        print(f"[Error] Download season {season_num}: {e}")

                        try:
                            _remove_scheduled_download(download_id)
                            if download_id not in download_tracker.downloads:
                                season_title = f"{name} - S{season_num}"
                                download_tracker.start_download(download_id, season_title, source_alias, media_type)
                            download_tracker.complete_download(download_id, success=False, error=error_msg)
                        except Exception as tracker_err:
                            print(f"[Error] Failed to update download tracker: {tracker_err}")
                    finally:
                        _release_download_slot()

            except Exception as e:
                print(f"[Error] Selected seasons download task: {e}")

        _submit_download_task(_download_selected_seasons_task)

        return redirect("download_dashboard")

    # --- SELECTED EPISODES DOWNLOAD ---
    else:
        if not season_number:
            messages.error(request, "Numero stagione mancante.")
            return redirect("search_home")

        episode_param = selected_episodes.strip() if selected_episodes else None
        print(f"[DEBUG] episode_param after strip: '{episode_param}'")
        
        if not episode_param:
            print("[ERROR] episode_param is empty/None!")
            messages.error(request, "Nessun episodio selezionato.")
            from django.urls import reverse
            url = reverse('series_detail') + f"?source_alias={source_alias}&item_payload={item_payload_raw}"
            return redirect(url)
        
        print(f"[DEBUG] ✓ Proceeding with episodes: {episode_param}")
        _run_download_in_thread(
            site=source_alias,
            item_payload=item_payload,
            season=season_number,
            episodes=episode_param,
            media_type=media_type
        )
        print(f"[DEBUG] ✓ Download thread started for S{season_number} E{episode_param}")

        return redirect("download_dashboard")


def download_dashboard(request: HttpRequest) -> HttpResponse:
    """Dashboard to view all active and completed downloads."""
    active_downloads = _enrich_active_downloads_with_series(download_tracker.get_active_downloads())
    history = download_tracker.get_history()
    _prune_scheduled_downloads(active_downloads, history)
    
    # Hide entries that are already running so they don't show in both sections.
    active_ids = {d.get("id") for d in active_downloads if d.get("id")}
    scheduled = _get_scheduled_downloads(exclude_ids=active_ids)

    return render(
        request,
        "searchapp/downloads.html",
        {
            "active_downloads": active_downloads,
            "scheduled_downloads": scheduled,
            "history": history,
            "active_count": len(active_downloads),
            "scheduled_count": len(scheduled),
        }
    )


def get_downloads_json(request: HttpRequest) -> JsonResponse:
    """API endpoint to get real-time download progress."""
    active_downloads = _enrich_active_downloads_with_series(download_tracker.get_active_downloads())
    history = download_tracker.get_history()
    _prune_scheduled_downloads(active_downloads, history)
    # Hide entries that are already running so they don't show in both sections.
    active_ids = {d.get("id") for d in active_downloads if d.get("id")}
    scheduled = _get_scheduled_downloads(exclude_ids=active_ids)

    return JsonResponse({
        "active": active_downloads,
        "scheduled": scheduled,
        "history": history
    })

@csrf_exempt
def kill_download(request: HttpRequest) -> JsonResponse:
    """API view to cancel a download."""
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            download_id = data.get("download_id")
            if download_id:
                download_tracker.request_stop(download_id)
                return JsonResponse({"status": "success"})
        
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
    
    return JsonResponse({"status": "error", "message": "Method not allowed", "status_code": 405}, status=405)


@csrf_exempt
def kill_and_clear_queue(request: HttpRequest) -> JsonResponse:
    """API view to cancel a specific download and empty the entire scheduled queue."""
    if request.method == "POST":
        try:
            data = json.loads(request.body)
            
            # 1. Kill the active process if provided
            download_id = data.get("download_id")
            series_name = data.get("series_name")
            target_site = ""
            target_series = _extract_series_base_title(series_name)
            
            if download_id:
                # Resolve target site/title from current scheduled queue first.
                with scheduled_downloads_lock:
                    info = scheduled_downloads.get(download_id)
                if info:
                    target_site = str(info.get("site") or "").strip()
                    if not target_series:
                        target_series = _extract_series_base_title(info.get("title", ""))

                # Fallback to active downloads if needed.
                if not info:
                    active_items = download_tracker.get_active_downloads()
                    active_info = next((d for d in active_items if d.get("id") == download_id), None)
                    if active_info:
                        target_site = str(active_info.get("site") or "").strip()
                        if not target_series:
                            target_series = _extract_series_base_title(active_info.get("title", ""))

                _cancel_scheduled_download(download_id)
                download_tracker.request_stop(download_id)

            # 2. Stop other active downloads for the same series (same site + same series base).
            if target_series:
                active_to_stop = []
                for item in download_tracker.get_active_downloads():
                    current_id = item.get("id")
                    if not current_id:
                        continue
                    if target_site and str(item.get("site") or "").strip() != target_site:
                        continue
                    if _same_series(item.get("title", ""), target_series):
                        active_to_stop.append(current_id)

                for current_id in active_to_stop:
                    _cancel_scheduled_download(current_id)
                    download_tracker.request_stop(current_id)
            
            # 3. Clear queued items for the same series (same site + same series base).
            with scheduled_downloads_lock:
                to_remove = []
                for d_id, d_info in scheduled_downloads.items():
                    if not target_series:
                        continue
                    if target_site and str(d_info.get("site") or "").strip() != target_site:
                        continue
                    if _same_series(d_info.get("title", ""), target_series):
                        cancelled_scheduled_downloads.add(d_id)
                        to_remove.append(d_id)
                for d_id in to_remove:
                    scheduled_downloads.pop(d_id, None)
                
            return JsonResponse({"status": "success"})
        
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=400)
            
    return JsonResponse({"status": "error", "message": "Method not allowed", "status_code": 405}, status=405)


@csrf_exempt
def clear_download_history(request: HttpRequest) -> JsonResponse:
    """API view to clear the download history."""
    if request.method == "POST":
        try:
            download_tracker.clear_history()
            return JsonResponse({"status": "success"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)
    return JsonResponse({"status": "error", "message": "Method not allowed"}, status=405)


@require_http_methods(["GET"])
def watchlist(request: HttpRequest) -> HttpResponse:
    """Display the watchlist."""
    items = WatchlistItem.objects.all()
    for item in items:
        item.season_numbers = list(range(1, item.num_seasons + 1))
    poll_interval_seconds = _get_interval_seconds()
    return render(
        request,
        "searchapp/watchlist.html",
        {"items": items, "poll_interval_seconds": poll_interval_seconds},
    )


@require_http_methods(["POST"])
def set_watchlist_polling_interval(request: HttpRequest) -> HttpResponse:
    """Update the watchlist auto-check interval for this process."""
    raw = request.POST.get("poll_interval", "")
    try:
        value = int(raw)
    except Exception:
        value = None

    allowed = {300, 900, 1800, 3600, 21600, 43200, 86400}
    if value not in allowed:
        messages.error(request, "Intervallo non valido.")
        return redirect("watchlist")

    os.environ["WATCHLIST_AUTO_INTERVAL_SECONDS"] = str(value)
    messages.success(request, "Intervallo di controllo aggiornato.")
    return redirect("watchlist")


@require_http_methods(["POST"])
def add_to_watchlist(request: HttpRequest) -> HttpResponse:
    """Add a media item to the watchlist."""
    source_alias = request.POST.get("source_alias")
    item_payload_raw = request.POST.get("item_payload")
    search_query = request.POST.get("search_query")
    search_site = request.POST.get("search_site")
    
    if not source_alias or not item_payload_raw:
        messages.error(request, "Parametri mancanti per la watchlist.")
        return redirect('search_home')
    
    try:
        item_payload = json.loads(item_payload_raw)
        name = item_payload.get("name")
        poster = item_payload.get("poster")
        tmdb_id = item_payload.get("tmdb_id")
        is_movie = _to_bool(item_payload.get("is_movie"))
        
        # Check if already in watchlist
        existing = WatchlistItem.objects.filter(name=name, source_alias=source_alias).first()
        
        if existing:
            messages.info(request, f"'{name}' è già nella watchlist.")
        else:
            item = WatchlistItem.objects.create(
                name=name,
                source_alias=source_alias,
                item_payload=item_payload_raw,
                is_movie=is_movie,
                poster_url=poster,
                tmdb_id=tmdb_id,
                num_seasons=0,
                last_season_episodes=0
            )
            
            # Update metadata in background to keep GUI fast
            def _bg_update():
                _update_single_item(item)
            
            threading.Thread(target=_bg_update, daemon=True).start()
            
    except Exception as e:
        messages.error(request, f"Errore durante l'aggiunta alla watchlist: {e}")
    
    # Redirect back to search results if we have the params
    if search_query and search_site:
        from django.urls import reverse
        return redirect(f"{reverse('search')}?site={search_site}&query={search_query}")
        
    return redirect(request.META.get('HTTP_REFERER', 'search_home'))


@require_http_methods(["POST"])
def remove_from_watchlist(request: HttpRequest, item_id: int) -> HttpResponse:
    """Remove an item from the watchlist."""
    try:
        item = WatchlistItem.objects.get(id=item_id)
        name = item.name
        item.delete()
        messages.success(request, f"'{name}' rimosso dalla watchlist.")
    except WatchlistItem.DoesNotExist:
        messages.error(request, "Elemento non trovato.")
    
    return redirect("watchlist")


@require_http_methods(["POST"])
def clear_watchlist(request: HttpRequest) -> HttpResponse:
    """Remove all items from the watchlist."""
    WatchlistItem.objects.all().delete()
    messages.success(request, "Watchlist svuotata.")
    return redirect("watchlist")


@require_http_methods(["POST"])
def update_watchlist_auto(request: HttpRequest, item_id: int) -> HttpResponse:
    """Update auto-download settings for a watchlist item."""
    try:
        item = WatchlistItem.objects.get(id=item_id)
    except WatchlistItem.DoesNotExist:
        messages.error(request, "Elemento non trovato.")
        return redirect("watchlist")

    if item.is_movie:
        if item.auto_enabled or item.auto_season:
            item.auto_enabled = False
            item.auto_season = None
            item.auto_last_episode_count = 0
            item.auto_last_downloaded_at = None
            item.save(
                update_fields=[
                    "auto_enabled",
                    "auto_season",
                    "auto_last_episode_count",
                    "auto_last_downloaded_at",
                ]
            )
        messages.error(request, "Auto-download non disponibile per i film.")
        return redirect("watchlist")

    auto_enabled = request.POST.get("auto_enabled") == "on"
    auto_season_raw = request.POST.get("auto_season")
    auto_season = None
    if auto_season_raw:
        try:
            auto_season = int(auto_season_raw)
        except Exception:
            auto_season = None

    if auto_enabled and not auto_season:
        messages.error(request, "Seleziona una stagione per l'auto-download.")
        return redirect("watchlist")

    if item.auto_season != auto_season:
        item.auto_last_episode_count = 0
        item.auto_last_downloaded_at = None

    item.auto_enabled = auto_enabled
    item.auto_season = auto_season if auto_enabled else None

    if not auto_enabled:
        item.auto_last_episode_count = 0

    item.save()
    messages.success(request, "Impostazioni auto-download aggiornate.")
    return redirect("watchlist")


def _update_single_item(item: WatchlistItem) -> bool:
    """Internal helper to update a single watchlist item."""
    try:
        if item.is_movie:
            item.last_checked_at = timezone.now()
            item.has_new_seasons = False
            item.has_new_episodes = False
            item.save(update_fields=["last_checked_at", "has_new_seasons", "has_new_episodes"])
            return False

        api = get_api(item.source_alias)
        item_payload = json.loads(item.item_payload)
        entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
        media_item = Entries(**entries_fields)

        if media_item.is_movie:
            item.is_movie = True
            item.last_checked_at = timezone.now()
            item.has_new_seasons = False
            item.has_new_episodes = False
            item.save(update_fields=["is_movie", "last_checked_at", "has_new_seasons", "has_new_episodes"])
            return False

        seasons = api.get_series_metadata(media_item)
        
        if not seasons:
            return False
            
        current_num_seasons = len(seasons)
        last_season = seasons[-1]
        current_last_season_episodes = last_season.episode_count
        
        changed = False

        # If item has 0 seasons (first add), just set the initial values without marking as "new"
        if item.num_seasons == 0:
            item.num_seasons = current_num_seasons
            item.last_season_episodes = current_last_season_episodes
            changed = True
        else:
            if current_num_seasons > item.num_seasons:
                item.has_new_seasons = True
                item.num_seasons = current_num_seasons
                changed = True
            
            if current_last_season_episodes > item.last_season_episodes:
                item.has_new_episodes = True
                item.last_season_episodes = current_last_season_episodes
                changed = True
            
        item.last_checked_at = timezone.now()
        item.save()
        return changed
    except Exception as e:
        print(f"Error updating {item.name}: {e}")
        return False


@require_http_methods(["POST"])
def update_watchlist_item(request: HttpRequest, item_id: int) -> HttpResponse:
    """Update a specific watchlist item."""
    try:
        item = WatchlistItem.objects.get(id=item_id)
        threading.Thread(target=_update_single_item, args=(item,), daemon=True).start()
        messages.info(request, f"Aggiornamento per '{item.name}' avviato in background.")
    except WatchlistItem.DoesNotExist:
        messages.error(request, "Elemento non trovato.")
    
    return redirect("watchlist")


@require_http_methods(["POST"])
def update_all_watchlist(request: HttpRequest) -> HttpResponse:
    """Update all items in the watchlist."""
    items = WatchlistItem.objects.all()
    
    def _update_all():
        for item in items:
            _update_single_item(item)
            
    threading.Thread(target=_update_all, daemon=True).start()
    messages.info(request, "Aggiornamento globale avviato in background. Ricarica tra qualche istante.")
    return redirect("watchlist")


@require_http_methods(["POST"])
def run_watchlist_auto_now(request: HttpRequest) -> HttpResponse:
    """Trigger the auto-download scan immediately."""
    from .watchlist_auto import run_watchlist_auto_once

    threading.Thread(target=run_watchlist_auto_once, daemon=True).start()
    messages.info(request, "Auto-download avviato subito in background.")
    return redirect("watchlist")


def watchlist_status(request: HttpRequest) -> JsonResponse:
    """API endpoint to check if any watchlist item was updated recently."""
    last_update = WatchlistItem.objects.order_by('-last_checked_at').first()
    if last_update:
        return JsonResponse({
            "last_checked": last_update.last_checked_at.timestamp(),
            "items_count": WatchlistItem.objects.count()
        })
    return JsonResponse({"last_checked": 0, "items_count": 0})


@require_http_methods(["POST"])
@csrf_exempt
def upload_service_zip(request: HttpRequest) -> JsonResponse:
    """
    Handle ZIP file upload to install a new service plugin.
    Extracts the ZIP into VibraVid/services/, validates the structure,
    and reloads the service registry. A uploaded service will only appear in
    the GUI dropdown if a matching static stub exists in
    GUI/searchapp/api/<service_name>.py — uploaded plugins are expected to
    ship that stub themselves (no auto-generation: explicit > implicit).
    """
    uploaded = request.FILES.get("service_zip")
    if not uploaded:
        return JsonResponse({"success": False, "error": "Nessun file ZIP caricato."}, status=400)

    if not uploaded.name.lower().endswith(".zip"):
        return JsonResponse({"success": False, "error": "Il file deve essere un archivio .zip"}, status=400)

    # Determine services directory (VibraVid/services)
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))  # project root
    services_dir = os.path.join(base_dir, "VibraVid", "services")

    if not os.path.isdir(services_dir):
        return JsonResponse({"success": False, "error": f"Directory dei servizi non trovata: {services_dir}"}, status=500)

    import tempfile
    tmp_dir = tempfile.mkdtemp(prefix="vv_service_upload_")
    errors = []
    installed_services = []

    try:
        # Save uploaded ZIP to temp
        zip_path = os.path.join(tmp_dir, uploaded.name)
        with open(zip_path, "wb") as f:
            for chunk in uploaded.chunks():
                f.write(chunk)

        # Extract
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp_dir)
        except zipfile.BadZipFile:
            return JsonResponse({"success": False, "error": "File ZIP non valido o corrotto."}, status=400)

        # Find service directories (folders with __init__.py)
        extracted_items = [d for d in os.listdir(tmp_dir) if os.path.isdir(os.path.join(tmp_dir, d))]

        service_folders = []
        for item in extracted_items:
            item_path = os.path.join(tmp_dir, item)
            init_file = os.path.join(item_path, "__init__.py")
            if os.path.isfile(init_file):
                service_folders.append(item)
            else:
                # Check one level deeper (ZIP might have a wrapper dir)
                for sub in os.listdir(item_path):
                    sub_path = os.path.join(item_path, sub)
                    if os.path.isdir(sub_path) and os.path.isfile(os.path.join(sub_path, "__init__.py")):
                        service_folders.append(os.path.join(item, sub))

        if not service_folders:
            return JsonResponse({
                "success": False,
                "error": "Nessun servizio valido trovato nello ZIP. Ogni servizio deve contenere __init__.py con 'indice' e '_useFor'."
            }, status=400)

        import ast as _ast

        for svc_rel in service_folders:
            svc_path = os.path.join(tmp_dir, svc_rel)
            svc_name = os.path.basename(svc_rel).lower()
            init_path = os.path.join(svc_path, "__init__.py")

            # Refuse to overwrite reserved infrastructure directories.
            # Without this guard, a malicious or malformed ZIP could replace
            # _base/ (the shared loader/manager code every service depends on)
            # and silently break every other service in the registry.
            if svc_name.startswith("_") or svc_name in {"base", "_base"}:
                errors.append(f"'{svc_name}': nome riservato, non può essere installato come servizio")
                continue

            # Reject plugins that contain Python files with syntax errors.
            # Without this check a broken upload silently lands on disk and
            # explodes only when the user runs a search, producing
            # confusing errors like "unterminated string literal at line 616"
            # from a file no one knows how to find.
            syntax_error = None
            for root, _, files in os.walk(svc_path):
                for fname in files:
                    if not fname.endswith(".py"):
                        continue
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as fh:
                            src = fh.read()
                        _ast.parse(src, filename=fname)
                    except SyntaxError as se:
                        rel = os.path.relpath(fpath, svc_path)
                        syntax_error = f"{rel}:{se.lineno}: {se.msg}"
                        break
                    except Exception as ex:
                        rel = os.path.relpath(fpath, svc_path)
                        syntax_error = f"{rel}: impossibile leggere ({ex})"
                        break
                if syntax_error:
                    break
            if syntax_error:
                errors.append(f"'{svc_name}': errore di sintassi nel plugin → {syntax_error}")
                continue

            # Validate __init__.py has required declarations
            try:
                with open(init_path, "r", encoding="utf-8") as f:
                    content = f.read()

                has_indice = False
                has_usefor = False
                for line in content.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("indice =") or stripped.startswith("indice="):
                        has_indice = True
                    if stripped.startswith("_useFor =") or stripped.startswith("_useFor="):
                        has_usefor = True

                if not has_indice:
                    errors.append(f"'{svc_name}': manca la dichiarazione 'indice' in __init__.py")
                    continue
                if not has_usefor:
                    errors.append(f"'{svc_name}': manca la dichiarazione '_useFor' in __init__.py")
                    continue

            except Exception as e:
                errors.append(f"'{svc_name}': errore nella lettura di __init__.py: {e}")
                continue

            # Check for conflicts
            dest_path = os.path.join(services_dir, svc_name)
            if os.path.exists(dest_path):
                shutil.rmtree(dest_path)

            # Copy service to services directory
            shutil.copytree(svc_path, dest_path)
            installed_services.append(svc_name)

            # Note: the GUI dropdown lists services that have a matching
            # static stub in GUI/searchapp/api/<svc_name>.py. Uploaded
            # plugins are expected to ship their own stub — we do not
            # auto-generate one (explicit is better than implicit).

        # Reload the service registries
        if installed_services:
            try:
                # Drop cached VibraVid.services.<name> modules for newly-installed services
                # so subsequent imports pick up the freshly-extracted files.
                for svc in installed_services:
                    prefix = f"VibraVid.services.{svc}"
                    for mod_name in [m for m in sys.modules if m == prefix or m.startswith(prefix + ".")]:
                        del sys.modules[mod_name]

                # Reload CLI service loader
                from VibraVid.services._base import site_loader
                importlib.reload(site_loader)
            except Exception as e:
                errors.append(f"Reload CLI services: {e}")

            try:
                # Drop any GUI API modules from sys.modules so previously-failed imports
                # (e.g. primevideo with a missing service backend) are retried fresh.
                for mod_name in [m for m in sys.modules if m.startswith("GUI.searchapp.api.") and not m.endswith(".base")]:
                    del sys.modules[mod_name]

                # Reload GUI API registry. Do NOT pre-clear _API_REGISTRY here:
                # _initialize_registry() now updates atomically and refuses to
                # shrink the registry if a reload encounters errors, which
                # protects existing services from being lost on a partial reload.
                from GUI.searchapp import api as gui_api_module
                gui_api_module._INITIALIZED = False
                gui_api_module._initialize_registry()
            except Exception as e:
                errors.append(f"Reload GUI API registry: {e}")

        # Report what the dropdown actually contains right now so the user can
        # verify their upload landed and which services failed to load.
        try:
            from GUI.searchapp import api as gui_api_module
            available_sites = sorted(gui_api_module.get_available_sites())
            load_errors_list = gui_api_module.get_load_errors()
        except Exception:
            available_sites = []
            load_errors_list = []

        result = {
            "success": len(installed_services) > 0,
            "installed": installed_services,
            "errors": errors,
            "available_sites_now": available_sites,
            "load_errors": load_errors_list,
            "message": f"Installati {len(installed_services)} servizi: {', '.join(installed_services)}" if installed_services else "Nessun servizio installato."
        }
        return JsonResponse(result, status=200 if installed_services else 400)

    except Exception as e:
        return JsonResponse({"success": False, "error": f"Errore durante l'installazione: {e}"}, status=500)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def settings_editor(request: HttpRequest) -> HttpResponse:
    conf_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "Conf")
    config_path = os.path.join(conf_dir, "config.json")
    login_path = os.path.join(conf_dir, "login.json")
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config_content = f.read()
    except Exception as e:
        config_content = f"# Errore nella lettura del file: {e}"
    
    try:
        with open(login_path, 'r', encoding='utf-8') as f:
            login_content = f.read()
    except Exception as e:
        login_content = f"# Errore nella lettura del file: {e}"
    
    return render(request, "searchapp/settings_editor.html", {
        "config_content": config_content,
        "login_content": login_content,
    })


@require_http_methods(["POST"])
@csrf_exempt
def save_settings(request: HttpRequest) -> JsonResponse:
    try:
        data = json.loads(request.body.decode('utf-8'))
        file_type = data.get('file_type')  # 'config' or 'login'
        content = data.get('content', '').strip()
        
        if not file_type or not content:
            return JsonResponse({
                "success": False,
                "error": "Parametri mancanti"
            }, status=400)
        
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            return JsonResponse({
                "success": False,
                "error": f"JSON non valido: {str(e)}"
            }, status=400)
        
        conf_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "Conf")
        if file_type == 'config':
            file_path = os.path.join(conf_dir, "config.json")
        elif file_type == 'login':
            file_path = os.path.join(conf_dir, "login.json")
        else:
            return JsonResponse({
                "success": False,
                "error": "Tipo di file non valido"
            }, status=400)
        
        backup_path = file_path + ".backup"
        if os.path.exists(file_path):
            try:
                import shutil
                shutil.copy2(file_path, backup_path)
            except Exception as e:
                print(f"Backup failed: {e}")
        
        with open(file_path, 'w', encoding='utf-8') as f:
            formatted = json.dumps(json.loads(content), indent=4, ensure_ascii=False)
            f.write(formatted)

        # Apply the download concurrency limit immediately, without a restart.
        if file_type == 'config':
            try:
                new_cfg = json.loads(content)
                slots = int(new_cfg.get("ARR", {}).get("max_concurrent_downloads", 1) or 1)
                set_max_download_slots(slots)
            except Exception as exc:
                print(f"[Downloads] Failed to apply max concurrent slots: {exc}")

        return JsonResponse({
            "success": True,
            "message": f"{file_type}.json salvato con successo"
        })
    
    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": f"Errore nel salvataggio: {str(e)}"
        }, status=500)


# ─────────────────────────────────────────────────────
# ARR Integration Views
# ─────────────────────────────────────────────────────

@require_http_methods(["GET"])
def arr_stack(request: HttpRequest) -> HttpResponse:
    """Display VibraVid's internal queue for media accepted from ARR."""
    from .arr.arr_service import _load_arr_config

    cfg = _load_arr_config()
    status_filter = request.GET.get("status", "all")
    source_filter = request.GET.get("source", "all")
    sync_filter = request.GET.get("sync", "all")
    query = request.GET.get("q", "").strip()

    queue_entries = ArrProcessingQueue.objects.select_related("media_request").order_by("-enqueued_at")
    if status_filter != "all":
        queue_entries = queue_entries.filter(media_request__status=status_filter)
    if source_filter != "all":
        queue_entries = queue_entries.filter(media_request__arr_source=source_filter)
    if sync_filter != "all":
        queue_entries = queue_entries.filter(media_request__sync_source=sync_filter)
    if query:
        queue_entries = queue_entries.filter(
            Q(media_request__title__icontains=query)
            | Q(dedup_key__icontains=query)
            | Q(media_request__provider__icontains=query)
            | Q(media_request__tmdb_id__icontains=query)
            | Q(media_request__imdb_id__icontains=query)
            | Q(media_request__tvdb_id__icontains=query)
        )

    status_counts = {
        status: ArrMediaRequest.objects.filter(status=status).count()
        for status, _ in ArrMediaRequest.Status.choices
    }
    active_count = ArrProcessingQueue.objects.filter(completed_at__isnull=True).count()
    total_count = ArrProcessingQueue.objects.count()
    filtered_count = queue_entries.count()

    return render(request, "searchapp/arr_stack.html", {
        "arr_enabled": cfg.get("enabled", False),
        "polling_enabled": cfg.get("enable_polling", False),
        "entries": queue_entries[:300],
        "shown_count": min(filtered_count, 300),
        "filtered_count": filtered_count,
        "total_count": total_count,
        "active_count": active_count,
        "status_counts": status_counts,
        "status_choices": ArrMediaRequest.Status.choices,
        "sync_choices": ArrMediaRequest.SyncSource.choices,
        "filters": {
            "q": request.GET.get("q", "").strip(),
            "status": request.GET.get("status", "all"),
            "source": request.GET.get("source", "all"),
            "sync": request.GET.get("sync", "all"),
        },
    })


@csrf_exempt
@require_http_methods(["POST"])
def seerr_webhook(request: HttpRequest) -> JsonResponse:
    """
    Seerr/Overseerr webhook endpoint.
    POST /api/arr/webhook/seerr/

    Validates X-Webhook-Token, logs the event, and triggers immediate sync.
    """
    try:
        from .arr.arr_service import _load_arr_config, trigger_webhook_sync
        from .models import ArrWebhookEvent

        # ── Log incoming request ──
        logger.info("=" * 60)
        logger.info("[SEERR WEBHOOK] Received request")
        logger.info(f"[SEERR WEBHOOK] Headers: {dict(request.headers)}")
        logger.info(f"[SEERR WEBHOOK] Method: {request.method}")
        logger.info(f"[SEERR WEBHOOK] Content-Type: {request.content_type}")
        logger.info(f"[SEERR WEBHOOK] Body (raw): {request.body[:2000]}")
        logger.info("=" * 60)

        cfg = _load_arr_config()
        logger.info(f"[SEERR WEBHOOK] ARR enabled: {cfg.get('enabled')}")
        logger.info(f"[SEERR WEBHOOK] Seerr webhook enabled: {cfg.get('enable_seerr_webhook')}")

        if not cfg.get("enabled"):
            logger.warning("[SEERR WEBHOOK] ARR services are disabled, ignoring webhook")
            return JsonResponse({"status": "disabled", "message": "ARR services are disabled"}, status=200)

        if not cfg.get("enable_seerr_webhook"):
            logger.warning("[SEERR WEBHOOK] Seerr webhook is disabled, ignoring")
            return JsonResponse({"status": "disabled", "message": "Seerr webhook is disabled"}, status=200)

        # Validate webhook token
        expected_secret = cfg.get("seerr", {}).get("webhook_secret", "")
        if expected_secret:
            token = request.headers.get("X-Webhook-Token", "")
            logger.info(f"[SEERR WEBHOOK] Validating token: {'present' if token else 'missing'}")
            if token != expected_secret:
                logger.warning("[SEERR WEBHOOK] Invalid webhook token")
                return JsonResponse({"status": "error", "message": "Invalid webhook token"}, status=403)

        # Parse payload
        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            logger.error("[SEERR WEBHOOK] Invalid JSON in request body")
            return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

        logger.info(f"[SEERR WEBHOOK] Parsed payload: {json.dumps(payload, indent=2, ensure_ascii=False)[:1000]}")

        # Detect event type
        notification_type = payload.get("notification_type", "").upper()
        logger.info(f"[SEERR WEBHOOK] notification_type: {notification_type}")

        # Accept multiple variants: MEDIA_APPROVED, MEDIA_AUTO_APPROVED, MEDIA_PENDING, TEST_NOTIFICATION
        if notification_type in ("MEDIA_APPROVED", "MEDIA_AUTO_APPROVED", "MEDIA_PENDING", "TEST_NOTIFICATION"):
            event_type = notification_type
        else:
            event_type = "UNKNOWN"
            logger.warning(f"[SEERR WEBHOOK] Unknown notification_type: {notification_type}")

        # Log the event
        media = payload.get("media", {}) or {}
        media_type = str(media.get("media_type", "")).lower() or None
        tmdb_id = media.get("tmdbId")
        tmdb_id_str = str(tmdb_id) if tmdb_id is not None else None

        webhook_event = ArrWebhookEvent.objects.create(
            event_type=event_type,
            source="seerr",
            media_type=media_type,
            tmdb_id=tmdb_id_str,
            raw_payload=payload,
            processed=False,
        )
        logger.info(f"[SEERR WEBHOOK] Created ArrWebhookEvent id={webhook_event.id}, event_type={event_type}")

        # Handle test notification
        if event_type == "TEST_NOTIFICATION":
            webhook_event.processed = True
            webhook_event.save(update_fields=["processed"])
            webhook_event.event_type = "TEST"
            webhook_event.save(update_fields=["event_type"])
            logger.info("[SEERR WEBHOOK] Test notification processed successfully")
            return JsonResponse({"status": "ok", "message": "Test notification received"})

        # Handle media events
        if event_type in ("MEDIA_APPROVED", "MEDIA_AUTO_APPROVED", "MEDIA_PENDING"):
            logger.info(f"[SEERR WEBHOOK] Media: {media.get('title')} (tmdbId={media.get('tmdbId')}, type={media.get('media_type')})")
            webhook_priority_enabled = cfg.get("webhook_priority_enabled", True)
            native_window = int(cfg.get("native_webhook_priority_window_seconds", 120))
            fallback_delay = int(cfg.get("seerr_fallback_delay_seconds", 20))

            def _async_sync():
                try:
                    from django.db import close_old_connections
                    close_old_connections()

                    if webhook_priority_enabled and tmdb_id_str and media_type in {"tv", "movie"}:
                        preferred_source = "sonarr" if media_type == "tv" else "radarr"
                        if _is_recent_webhook(tmdb_id_str, source=preferred_source, window_seconds=native_window, touch=False):
                            webhook_event.processed = True
                            webhook_event.ignored_by_priority = True
                            webhook_event.save(update_fields=["processed", "ignored_by_priority"])
                            logger.info(
                                f"[SEERR WEBHOOK ASYNC] Skipped by priority; native {preferred_source} webhook already handled tmdbId={tmdb_id_str}"
                            )
                            return

                        if fallback_delay > 0:
                            time.sleep(fallback_delay)
                            if _is_recent_webhook(tmdb_id_str, source=preferred_source, window_seconds=native_window, touch=False):
                                webhook_event.processed = True
                                webhook_event.ignored_by_priority = True
                                webhook_event.save(update_fields=["processed", "ignored_by_priority"])
                                logger.info(
                                    f"[SEERR WEBHOOK ASYNC] Skipped after fallback delay; native {preferred_source} webhook arrived for tmdbId={tmdb_id_str}"
                                )
                                return

                    logger.info(f"[SEERR WEBHOOK ASYNC] Starting sync for event {webhook_event.id}")
                    count = trigger_webhook_sync(payload)
                    webhook_event.processed = True
                    webhook_event.save(update_fields=["processed"])
                    logger.info(f"[SEERR WEBHOOK ASYNC] Sync complete: {count} items enqueued")
                except Exception as exc:
                    webhook_event.error = str(exc)
                    webhook_event.save(update_fields=["error"])
                    logger.error(f"[SEERR WEBHOOK ASYNC] Sync error: {exc}", exc_info=True)

            threading.Thread(target=_async_sync, daemon=True).start()
            logger.info(f"[SEERR WEBHOOK] Started async sync thread for {event_type}")
            return JsonResponse({"status": "ok", "message": f"Processing {event_type}"})

        logger.info(f"[SEERR WEBHOOK] Event {event_type} acknowledged but not processed")
        return JsonResponse({"status": "ok", "message": f"Event {event_type} acknowledged"})

    except Exception as exc:
        logger.error(f"[SEERR WEBHOOK] Unexpected error: {exc}", exc_info=True)
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def sonarr_webhook(request: HttpRequest) -> JsonResponse:
    """
    Sonarr native webhook endpoint.
    POST /api/arr/webhook/sonarr/

    Validates X-Webhook-Token, logs the event, and triggers immediate targeted sync.
    """
    try:
        from .arr.arr_service import _load_arr_config, trigger_sonarr_webhook_sync
        from .models import ArrWebhookEvent

        # ── Log incoming request ──
        logger.info("=" * 60)
        logger.info("[SONARR WEBHOOK] Received request")
        logger.info(f"[SONARR WEBHOOK] Headers: {dict(request.headers)}")
        logger.info(f"[SONARR WEBHOOK] Body (raw): {request.body[:2000]}")
        logger.info("=" * 60)

        cfg = _load_arr_config()
        logger.info(f"[SONARR WEBHOOK] ARR enabled: {cfg.get('enabled')}")
        logger.info(f"[SONARR WEBHOOK] Sonarr webhook enabled: {cfg.get('enable_sonarr_webhook')}")

        if not cfg.get("enabled"):
            logger.warning("[SONARR WEBHOOK] ARR services are disabled, ignoring")
            return JsonResponse({"status": "disabled", "message": "ARR services are disabled"}, status=200)

        if not cfg.get("enable_sonarr_webhook"):
            logger.warning("[SONARR WEBHOOK] Sonarr webhook is disabled, ignoring")
            return JsonResponse({"status": "disabled", "message": "Sonarr webhook is disabled"}, status=200)

        expected_secret = cfg.get("sonarr_webhook", {}).get("webhook_secret", "")
        if expected_secret:
            token = request.headers.get("X-Webhook-Token", "")
            logger.info(f"[SONARR WEBHOOK] Validating token: {'present' if token else 'missing'}")
            if token != expected_secret:
                logger.warning("[SONARR WEBHOOK] Invalid webhook token")
                return JsonResponse({"status": "error", "message": "Invalid webhook token"}, status=403)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            logger.error("[SONARR WEBHOOK] Invalid JSON in request body")
            return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

        logger.info(f"[SONARR WEBHOOK] Parsed payload: {json.dumps(payload, indent=2, ensure_ascii=False)[:1000]}")

        event_type = payload.get("eventType", "UNKNOWN").upper()
        logger.info(f"[SONARR WEBHOOK] eventType: {event_type}")

        series_data = payload.get("series", {}) or {}
        tmdb_id = series_data.get("tmdbId") or series_data.get("tvdbId")
        tmdb_id_str = str(tmdb_id) if tmdb_id is not None else None
        _mark_native_webhook_seen(tmdb_id_str, "sonarr")

        webhook_event = ArrWebhookEvent.objects.create(
            event_type=event_type,
            source="sonarr",
            media_type="tv",
            tmdb_id=tmdb_id_str,
            arr_item_id=series_data.get("id"),
            raw_payload=payload,
            processed=False,
        )
        logger.info(f"[SONARR WEBHOOK] Created ArrWebhookEvent id={webhook_event.id}")

        if event_type == "TEST":
            payload_str = json.dumps(payload, indent=2, ensure_ascii=False)
            logger.info(f"[SONARR WEBHOOK TEST] Payload:\n{payload_str}")
            webhook_event.processed = True
            webhook_event.save(update_fields=["processed"])
            return JsonResponse({
                "status": "ok",
                "message": "Test notification received",
                "payload_preview": payload,
            })

        # Handle events that should trigger sync
        # - DOWNLOAD/GRAB: Sonarr has downloaded episodes -> sync those episodes
        # - SeriesAdd: Series was added to Sonarr (usually after Seerr approval) -> sync missing episodes
        if event_type in ("DOWNLOAD", "GRAB", "SERIESADD"):
            series_data = payload.get("series", {})
            episodes = payload.get("episodes", [])
            logger.info(f"[SONARR WEBHOOK] Series: {series_data.get('title')} (id={series_data.get('id')})")
            logger.info(f"[SONARR WEBHOOK] Episodes count: {len(episodes)}")
            logger.info(f"[SONARR WEBHOOK] Triggering sync for event {event_type}")

            def _async_sync():
                try:
                    from django.db import close_old_connections
                    close_old_connections()
                    logger.info(f"[SONARR WEBHOOK ASYNC] Starting sync for event {webhook_event.id} (type={event_type})")
                    if event_type == "SERIESADD":
                        # For SeriesAdd, use trigger_sonarr_webhook_sync which is
                        # designed for Sonarr native webhooks and can find the series by ID
                        count = trigger_sonarr_webhook_sync(payload)
                    else:
                        count = trigger_sonarr_webhook_sync(payload)
                    webhook_event.processed = True
                    webhook_event.save(update_fields=["processed"])
                    logger.info(f"[SONARR WEBHOOK ASYNC] Sync complete: {count} items enqueued")
                except Exception as exc:
                    webhook_event.error = str(exc)
                    webhook_event.save(update_fields=["error"])
                    logger.error(f"[SONARR WEBHOOK ASYNC] Sync error: {exc}", exc_info=True)

            threading.Thread(target=_async_sync, daemon=True).start()
            logger.info(f"[SONARR WEBHOOK] Started async sync thread for {event_type}")
            return JsonResponse({"status": "ok", "message": f"Processing Sonarr event {event_type}"})

        elif event_type == "SERIESDELETE":
            logger.info("[SONARR WEBHOOK] Series deleted event ignored (no sync needed)")
            webhook_event.processed = True
            webhook_event.save(update_fields=["processed"])
            return JsonResponse({"status": "ok", "message": "SeriesDelete acknowledged, no action needed"})

        logger.info(f"[SONARR WEBHOOK] Event {event_type} acknowledged but not processed")
        return JsonResponse({"status": "ok", "message": f"Event {event_type} acknowledged"})

    except Exception as exc:
        logger.error(f"[SONARR WEBHOOK] Unexpected error: {exc}", exc_info=True)
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def radarr_webhook(request: HttpRequest) -> JsonResponse:
    """
    Radarr native webhook endpoint.
    POST /api/arr/webhook/radarr/

    Validates X-Webhook-Token, logs the event, and triggers immediate targeted sync.
    """
    try:
        from .arr.arr_service import _load_arr_config, trigger_radarr_webhook_sync
        from .models import ArrWebhookEvent

        # ── Log incoming request ──
        logger.info("=" * 60)
        logger.info("[RADARR WEBHOOK] Received request")
        logger.info(f"[RADARR WEBHOOK] Headers: {dict(request.headers)}")
        logger.info(f"[RADARR WEBHOOK] Body (raw): {request.body[:2000]}")
        logger.info("=" * 60)

        cfg = _load_arr_config()
        logger.info(f"[RADARR WEBHOOK] ARR enabled: {cfg.get('enabled')}")
        logger.info(f"[RADARR WEBHOOK] Radarr webhook enabled: {cfg.get('enable_radarr_webhook')}")

        if not cfg.get("enabled"):
            logger.warning("[RADARR WEBHOOK] ARR services are disabled, ignoring")
            return JsonResponse({"status": "disabled", "message": "ARR services are disabled"}, status=200)

        if not cfg.get("enable_radarr_webhook"):
            logger.warning("[RADARR WEBHOOK] Radarr webhook is disabled, ignoring")
            return JsonResponse({"status": "disabled", "message": "Radarr webhook is disabled"}, status=200)

        expected_secret = cfg.get("radarr_webhook", {}).get("webhook_secret", "")
        if expected_secret:
            token = request.headers.get("X-Webhook-Token", "")
            logger.info(f"[RADARR WEBHOOK] Validating token: {'present' if token else 'missing'}")
            if token != expected_secret:
                logger.warning("[RADARR WEBHOOK] Invalid webhook token")
                return JsonResponse({"status": "error", "message": "Invalid webhook token"}, status=403)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except json.JSONDecodeError:
            logger.error("[RADARR WEBHOOK] Invalid JSON in request body")
            return JsonResponse({"status": "error", "message": "Invalid JSON"}, status=400)

        logger.info(f"[RADARR WEBHOOK] Parsed payload: {json.dumps(payload, indent=2, ensure_ascii=False)[:1000]}")

        event_type = payload.get("eventType", "UNKNOWN").upper()
        logger.info(f"[RADARR WEBHOOK] eventType: {event_type}")

        movie_data = payload.get("movie", {}) or {}
        tmdb_id = movie_data.get("tmdbId")
        tmdb_id_str = str(tmdb_id) if tmdb_id is not None else None
        _mark_native_webhook_seen(tmdb_id_str, "radarr")

        webhook_event = ArrWebhookEvent.objects.create(
            event_type=event_type,
            source="radarr",
            media_type="movie",
            tmdb_id=tmdb_id_str,
            arr_item_id=movie_data.get("id"),
            raw_payload=payload,
            processed=False,
        )
        logger.info(f"[RADARR WEBHOOK] Created ArrWebhookEvent id={webhook_event.id}")

        if event_type == "TEST":
            payload_str = json.dumps(payload, indent=2, ensure_ascii=False)
            logger.info(f"[RADARR WEBHOOK TEST] Payload:\n{payload_str}")
            webhook_event.processed = True
            webhook_event.save(update_fields=["processed"])
            return JsonResponse({
                "status": "ok",
                "message": "Test notification received",
                "payload_preview": payload,
            })

        movie_data = payload.get("movie", {})
        logger.info(f"[RADARR WEBHOOK] Movie: {movie_data.get('title')} (id={movie_data.get('id')})")

        def _async_sync():
            try:
                from django.db import close_old_connections
                close_old_connections()
                logger.info(f"[RADARR WEBHOOK ASYNC] Starting sync for event {webhook_event.id}")
                count = trigger_radarr_webhook_sync(payload)
                webhook_event.processed = True
                webhook_event.save(update_fields=["processed"])
                logger.info(f"[RADARR WEBHOOK ASYNC] Sync complete: {count} items enqueued")
            except Exception as exc:
                webhook_event.error = str(exc)
                webhook_event.save(update_fields=["error"])
                logger.error(f"[RADARR WEBHOOK ASYNC] Sync error: {exc}", exc_info=True)

        threading.Thread(target=_async_sync, daemon=True).start()
        logger.info(f"[RADARR WEBHOOK] Started async sync thread for {event_type}")
        return JsonResponse({"status": "ok", "message": f"Processing Radarr event {event_type}"})

    except Exception as exc:
        logger.error(f"[RADARR WEBHOOK] Unexpected error: {exc}", exc_info=True)
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)


@csrf_exempt
@require_http_methods(["GET"])
def arr_status(request: HttpRequest) -> JsonResponse:
    """
    ARR status endpoint.
    GET /api/arr/status/

    Returns current ARR configuration state and recent activity.
    """
    try:
        from .arr.arr_service import _load_arr_config
        from .models import ArrMediaRequest, ArrWebhookEvent

        cfg = _load_arr_config()

        # Recent counts
        pending_count = ArrMediaRequest.objects.filter(status="pending").count()
        downloading_count = ArrMediaRequest.objects.filter(status="downloading").count()
        completed_count = ArrMediaRequest.objects.filter(status="completed").count()
        failed_count = ArrMediaRequest.objects.filter(status="failed").count()
        webhook_count = ArrWebhookEvent.objects.count()

        # Latest test payloads per source
        def _latest_test(source_hint):
            qs = ArrWebhookEvent.objects.filter(
                event_type__iexact="test"
            ).order_by("-id")[:20]
            for ev in qs:
                p = ev.raw_payload or {}
                if source_hint == "sonarr" and "series" in p:
                    return p
                if source_hint == "radarr" and "movie" in p:
                    return p
                if source_hint == "seerr" and "notification_type" in p:
                    return p
            return None

        return JsonResponse({
            "enabled": cfg.get("enabled", False),
            "polling_enabled": cfg.get("enable_polling", False),
            "webhook_enabled": cfg.get("enable_seerr_webhook", False),
            "sonarr_webhook_enabled": cfg.get("enable_sonarr_webhook", False),
            "radarr_webhook_enabled": cfg.get("enable_radarr_webhook", False),
            "max_concurrent_downloads": cfg.get("max_concurrent_downloads", 1),
            "webhook_priority_enabled": cfg.get("webhook_priority_enabled", True),
            "native_webhook_priority_window_seconds": cfg.get("native_webhook_priority_window_seconds", 120),
            "seerr_fallback_delay_seconds": cfg.get("seerr_fallback_delay_seconds", 20),
            "polling_interval": cfg.get("polling_interval", 300),
            "full_resync_interval": cfg.get("full_resync_interval", 21600),
            "sonarr_configured": bool(cfg.get("sonarr", {}).get("url")),
            "radarr_configured": bool(cfg.get("radarr", {}).get("url")),
            "last_sonarr_test_payload": _latest_test("sonarr"),
            "last_radarr_test_payload": _latest_test("radarr"),
            "last_seerr_test_payload": _latest_test("seerr"),
            "stats": {
                "pending": pending_count,
                "downloading": downloading_count,
                "completed": completed_count,
                "failed": failed_count,
                "total_webhooks": webhook_count,
            },
        })
    except Exception as exc:
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)


@csrf_exempt
@require_http_methods(["POST"])
def arr_trigger_sync(request: HttpRequest) -> JsonResponse:
    """
    Manually trigger ARR sync.
    POST /api/arr/trigger-sync/
    """
    try:
        from .arr.arr_service import _load_arr_config, trigger_polling_sync

        cfg = _load_arr_config()
        if not cfg.get("enabled"):
            return JsonResponse({"status": "disabled", "message": "ARR services are disabled"})

        def _async_sync():
            try:
                from django.db import close_old_connections
                close_old_connections()
                count = trigger_polling_sync(full_resync=True)
                print(f"[ARR] Manual sync complete: {count} items enqueued")
            except Exception as exc:
                print(f"[ARR] Manual sync error: {exc}")

        threading.Thread(target=_async_sync, daemon=True).start()
        return JsonResponse({"status": "ok", "message": "Sync triggered in background"})

    except Exception as exc:
        return JsonResponse({"status": "error", "message": str(exc)}, status=500)


@require_http_methods(["GET"])
def registry_status(request: HttpRequest) -> JsonResponse:
    """
    Diagnostic endpoint: report what the GUI service registry currently knows.

    Returns the list of loaded service IDs, any import errors from the last
    initialization, the services that exist on disk under VibraVid/services/,
    and which static GUI api stubs exist. Hit /api/registry-status/ in a browser
    when the dropdown looks wrong to find out why.
    """
    from GUI.searchapp import api as gui_api_module

    api_dir = os.path.dirname(gui_api_module.__file__)
    static_stubs = sorted(
        f[:-3] for f in os.listdir(api_dir)
        if f.endswith(".py") and f not in ("base.py", "__init__.py")
    )

    services_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "VibraVid", "services")
    services_on_disk = []
    if os.path.isdir(services_dir):
        for entry in sorted(os.listdir(services_dir)):
            full = os.path.join(services_dir, entry)
            if entry.startswith("_") or entry.startswith("."):
                continue
            if os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
                services_on_disk.append(entry.lower())

    loaded = sorted(gui_api_module.get_available_sites())
    missing_from_dropdown = sorted(set(services_on_disk) - set(loaded))

    return JsonResponse({
        "loaded_in_dropdown": loaded,
        "services_on_disk": services_on_disk,
        "static_gui_stubs": static_stubs,
        "missing_from_dropdown": missing_from_dropdown,
        "load_errors": gui_api_module.get_load_errors(),
        "db_dir": os.environ.get("DJANGO_DB_DIR", "<unset>"),
        "hint": (
            "Se 'loaded_in_dropdown' contiene solo mostraguarda ma "
            "'services_on_disk' ne contiene di più, guarda 'load_errors' "
            "per vedere perché gli altri non caricano."
        ),
    })


@require_http_methods(["POST"])
def reload_config(request: HttpRequest) -> JsonResponse:
    try:
        file_type = None
        if request.content_type and "application/json" in request.content_type:
            try:
                data = json.loads(request.body.decode("utf-8"))
                file_type = data.get("file_type")
            except Exception:
                file_type = None

        if file_type == "login":
            config_manager.reload_login_only()
            message = "Login ricaricato"
        elif file_type == "config":
            config_manager.reload_config_only()
            message = "Config ricaricata"
        else:
            config_manager.reload()
            message = "Config ricaricata"
        return JsonResponse({
            "success": True,
            "message": message
        })
    except Exception as e:
        return JsonResponse({
            "success": False,
            "error": f"Errore nel reload: {str(e)}"
        }, status=500)


# ── In-app update check ──────────────────────────────────────────────────────

_update_check_cache: dict = {}  # {timestamp: float, result: dict}
_UPDATE_CHECK_TTL = 3600  # re-fetch GitHub releases at most once per hour


@require_http_methods(["GET"])
def check_updates(request: HttpRequest) -> JsonResponse:
    """Return current and latest version info, with a 1-hour in-memory cache."""
    import urllib.request
    from VibraVid.upload.version import __version__

    cached = _update_check_cache
    if cached and time.monotonic() - cached.get("ts", 0) < _UPDATE_CHECK_TTL:
        return JsonResponse(cached["result"])

    try:
        api_url = "https://api.github.com/repos/AstraeLabs/VibraVid/releases/latest"
        req = urllib.request.Request(api_url, headers={"User-Agent": "VibraVid-update-check/1"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        latest = data.get("tag_name", "").lstrip("v")
        html_url = data.get("html_url", "")
    except Exception as exc:
        logger.warning(f"Update check failed: {exc}")
        return JsonResponse({"error": str(exc)}, status=502)

    def _ver_tuple(v):
        try:
            return tuple(int(x) for x in v.split("."))
        except Exception:
            return (0,)

    update_available = _ver_tuple(latest) > _ver_tuple(__version__)
    result = {
        "current": __version__,
        "latest": latest,
        "update_available": update_available,
        "html_url": html_url,
    }
    _update_check_cache["ts"] = time.monotonic()
    _update_check_cache["result"] = result
    return JsonResponse(result)


@require_http_methods(["POST"])
def trigger_update(request: HttpRequest) -> JsonResponse:
    """Write a sentinel file that a host-side update script can watch.

    The container itself cannot run docker compose — it writes a marker file
    to /app/data/.update_requested so an external script (docker/scripts/nas-update.sh)
    can detect it and perform the actual image pull + container recreation.
    """
    sentinel = os.path.join(os.environ.get("DJANGO_DB_DIR", "/app/data"), ".update_requested")
    try:
        with open(sentinel, "w") as f:
            f.write(str(time.time()))
        logger.info(f"Update sentinel written: {sentinel}")
        return JsonResponse({"success": True, "sentinel": sentinel})
    except Exception as exc:
        logger.error(f"Failed to write update sentinel: {exc}")
        return JsonResponse({"success": False, "error": str(exc)}, status=500)
