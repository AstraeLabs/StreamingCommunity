# 06.06.25

import json
import logging

from django.contrib import messages
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from GUI.searchapp.api import get_api
from GUI.searchapp.api.base import Entries

from ..forms import DownloadForm
from ._shared import _run_download_in_thread

logger = logging.getLogger(__name__)


@require_http_methods(["POST"])
def series_metadata(request: HttpRequest) -> JsonResponse:
    """Return series metadata including seasons and episode counts."""
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
        error_msg = f"Invalid data: {form.errors.as_text()}"
        logger.error(error_msg)
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
    except (ValueError, TypeError):
        messages.error(request, "Invalid payload")
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
        messages.error(request, "Select at least one episode before downloading!")

    # Run download
    _run_download_in_thread(source_alias, item_payload, season, episode, media_type, audio_format=audio_format)
    return redirect("download_dashboard")

__all__ = ['series_metadata', 'start_download']
