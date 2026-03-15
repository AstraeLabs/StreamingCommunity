# 06.06.25

import os
import time
import json
import threading
import atexit
import signal
import concurrent.futures
from typing import Any, Dict, List

from django.shortcuts import render, redirect
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.utils import timezone

from .forms import SearchForm, DownloadForm
from .models import WatchlistItem
from .watchlist_auto import _get_interval_seconds
from GUI.searchapp.api import get_api
from GUI.searchapp.api.base import Entries

from VibraVid.source.style.tracker import  download_tracker, context_tracker
from VibraVid.utils.tmdb_client import tmdb_client
from VibraVid.cli.run import execute_hooks


download_executor = concurrent.futures.ThreadPoolExecutor(max_workers=10, thread_name_prefix="DownloadWorker")
scheduled_downloads: Dict[str, Dict[str, Any]] = {}
scheduled_downloads_lock = threading.Lock()
cancelled_scheduled_downloads: set[str] = set()


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
        if download_id in scheduled_downloads:
            cancelled_scheduled_downloads.add(download_id)
        scheduled_downloads.pop(download_id, None)


def _is_scheduled_cancelled(download_id: str) -> bool:
    with scheduled_downloads_lock:
        return download_id in cancelled_scheduled_downloads


def _get_scheduled_downloads() -> List[Dict[str, Any]]:
    with scheduled_downloads_lock:
        return sorted(
            list(scheduled_downloads.values()),
            key=lambda item: item.get("scheduled_at", 0),
        )


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
    result = {
        'display_title': item.name,
        'display_type': item.type.capitalize(),
        'source': source_alias.capitalize(),
        'source_alias': source_alias,
        'bg_image_url': poster_url,
        'is_movie': item.is_movie,
        'year': item.year
    }
    result['payload_json'] = json.dumps({**item.__dict__, 'is_movie': item.is_movie})
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


def _run_download_in_thread(site: str, item_payload: Dict[str, Any], season: str = None, episodes: str = None, media_type: str = "Film") -> None:
    """Run download in background thread."""
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
        try:
            if _is_scheduled_cancelled(download_id):
                _remove_scheduled_download(download_id)
                return

            # Set context for downloaders in this thread
            context_tracker.download_id = download_id
            context_tracker.site_name = site
            context_tracker.media_type = media_type
            context_tracker.is_gui = True
            
            api = get_api(site)
            
            # Create Entries from payload
            entries_fields = {k: v for k, v in item_payload.items() if k in Entries.__dataclass_fields__}
            media_item = Entries(**entries_fields)
            
            # Start download
            api.start_download(media_item, season=season, episodes=episodes)
        except Exception as e:
            error_msg = str(e) or "Errore sconosciuto"
            print(f"[Error] Download task failed: {error_msg}")
            import traceback
            traceback.print_exc()

            try:
                _remove_scheduled_download(download_id)
                
                # start it briefly just to mark it as failed in the history.
                if download_id not in download_tracker.downloads:
                    download_tracker.start_download(download_id, title, site, media_type)
                
                download_tracker.complete_download(download_id, success=False, error=error_msg)
            except Exception as tracker_err:
                print(f"[Error] Failed to update download tracker: {tracker_err}")

    download_executor.submit(_task)


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

    # Normalize
    if season:
        season = str(season).strip() or None
    if episode:
        episode = str(episode).strip() or None

    try:
        item_payload = json.loads(item_payload_raw)
    except Exception:
        messages.error(request, "Payload non valido")
        return redirect("search_home")

    # Determine media type
    media_type = "Film" if item_payload.get("is_movie") else "Serie"

    # Check for series episode selection
    if media_type == "Serie" and season and not episode:
        messages.error(request, "Seleziona almeno un episodio prima di scaricare!")

    # Run download
    _run_download_in_thread(source_alias, item_payload, season, episode, media_type)
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
            seasons_data.append({
                "number": season.number,
                "episode_count": season.episode_count,
                "episodes": [ep.__dict__ for ep in season.episodes],
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
                    try:
                        if _is_scheduled_cancelled(download_id):
                            _remove_scheduled_download(download_id)
                            continue

                        context_tracker.download_id = download_id
                        context_tracker.site_name = source_alias
                        context_tracker.media_type = media_type
                        context_tracker.is_gui = True

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

            except Exception as e:
                print(f"[Error] Full series download task: {e}")

        download_executor.submit(_download_entire_series_task)

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

    # --- SELECTED EPISODES DOWNLOAD ---
    else:
        if not season_number:
            messages.error(request, "Numero stagione mancante.")
            return redirect("search_home")

        episode_param = selected_episodes.strip() if selected_episodes else None
        if not episode_param:
            messages.error(request, "Nessun episodio selezionato.")
            from django.urls import reverse
            url = reverse('series_detail') + f"?source_alias={source_alias}&item_payload={item_payload_raw}"
            return redirect(url)

        _run_download_in_thread(
            site=source_alias,
            item_payload=item_payload,
            season=season_number,
            episodes=episode_param,
            media_type=media_type
        )

        return redirect("download_dashboard")


def download_dashboard(request: HttpRequest) -> HttpResponse:
    """Dashboard to view all active and completed downloads."""
    active_downloads = download_tracker.get_active_downloads()
    history = download_tracker.get_history()
    _prune_scheduled_downloads(active_downloads, history)
    scheduled = _get_scheduled_downloads()
    
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
    active_downloads = download_tracker.get_active_downloads()
    history = download_tracker.get_history()
    _prune_scheduled_downloads(active_downloads, history)
    scheduled = _get_scheduled_downloads()
    
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
                _cancel_scheduled_download(download_id)
                download_tracker.request_stop(download_id)
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