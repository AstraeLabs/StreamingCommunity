# 25.07.26

import logging

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render

from GUI.searchapp.api import get_available_sites, get_site_categories

from .._download_infra import _get_scheduled_downloads
from .._library_paths import titleize_name
from ..models import WatchlistItem

logger = logging.getLogger(__name__)


def _duotone(text: str) -> tuple[str, str]:
    """Copy of the duotone logic from the old JS, to generate a consistent color for a given text."""
    hue = sum(ord(c) for c in (text or "?")) % 360
    return f"hsl({hue} 34% 26%)", f"hsl({hue} 40% 10%)"


def cinema_download(request: HttpRequest) -> HttpResponse:
    """Download: grid of scheduled downloads, with status and actions."""
    return render(request, "searchapp/cinema_download.html", {
        "nav_active": "download",
        "watchlist_items": _watchlist_tiles(limit=14),
    })


def _watchlist_tiles(limit: int | None = None) -> list[dict]:
    """Return a list of watchlist items with their status for display in the cinema view."""
    rows: list[dict] = []
    try:
        query = WatchlistItem.objects.all()
        for item in (query[:limit] if limit else query):
            p1, p2 = _duotone(item.name)
            rows.append({
                "id": item.id,
                "name": titleize_name(item.name),
                "site": item.source_alias,
                "poster": item.poster_url,
                "is_new": item.has_new_episodes or item.has_new_seasons,
                "is_movie": item.is_movie,
                "seasons": item.num_seasons,
                "auto": item.auto_enabled,
                "last_checked": item.auto_last_checked_at or item.last_checked_at,
                "p1": p1, "p2": p2,
            })
    except Exception:
        logger.exception("Watchlist non leggibile: si prosegue senza")
    return rows


def cinema_search(request: HttpRequest) -> HttpResponse:
    """Search: form with scope and site selection, results in a grid."""
    from ..forms import _CATEGORY_LABELS, GLOBAL_ALL_TOKEN, GLOBAL_CATEGORY_PREFIX

    categories = get_site_categories()

    sites = []
    for name in get_available_sites():
        sites.append({"name": name, "category": categories.get(name, "")})
    sites.sort(key=lambda s: s["name"])

    scopes = []
    for cat in sorted({s["category"] for s in sites if s["category"]}):
        scopes.append({
            "value": f"{GLOBAL_CATEGORY_PREFIX}{cat}",
            "label": _CATEGORY_LABELS.get(cat, cat.replace("_", " ").title()),
            "category": cat,
            "count": sum(1 for s in sites if s["category"] == cat),
        })

    return render(request, "searchapp/cinema_search.html", {
        "nav_active": "search",
        "sites": sites,
        "scopes": scopes,
        "all_token": GLOBAL_ALL_TOKEN,
    })


def cinema_watchlist(request: HttpRequest) -> HttpResponse:
    """Watchlist: grid of watchlist items with status and actions."""
    from ..watchlist_auto import _get_interval_seconds

    items = _watchlist_tiles()
    interval = _get_interval_seconds()

    return render(request, "searchapp/cinema_watchlist.html", {
        "nav_active": "watchlist",
        "items": items,
        "new_count": sum(1 for i in items if i["is_new"]),
        "auto_count": sum(1 for i in items if i["auto"]),
        "interval_minutes": int(interval // 60) if interval else 0,
    })


def _conf_text(filename: str) -> str:
    """Return the content of a config file as text, or an error message if it can't be read."""
    import os as _os

    from ._shared import _conf_dir

    try:
        with open(_os.path.join(_conf_dir(), filename), encoding="utf-8") as fh:
            return fh.read()
    except OSError as exc:
        logger.warning("Conf/%s non leggibile: %s", filename, exc)
        return f"# Non riesco a leggere {filename}: {exc}"


def cinema_system(request: HttpRequest) -> HttpResponse:
    """System: show providers, disabled sites, services, and config content."""
    import os

    from VibraVid.utils.upload.version import __version__

    categories = get_site_categories()
    providers = []
    for name in sorted(get_available_sites()):
        providers.append({"name": name, "category": categories.get(name, "")})

    disabled = [
        s.strip() for s in (os.environ.get("VIBRAVID_DISABLED_SITES") or "").split(",") if s.strip()
    ]

    services = [
        {"name": "FlareSolverr", "detail": "Cloudflare challenges",
         "on": bool(os.environ.get("FLARESOLVERR_URL"))},
        {"name": "Bypasser", "detail": "Turnstile for Amazon Music",
         "on": bool(os.environ.get("BYPASSER_URL"))},
    ]

    arr = {}
    try:
        from ..arr.arr_service import _load_arr_config

        cfg = _load_arr_config()
        arr = {
            "enabled": bool(cfg.get("enabled")),
            "sonarr": (cfg.get("sonarr") or {}).get("url") or "",
            "radarr": (cfg.get("radarr") or {}).get("url") or "",
            "seerr": bool(cfg.get("enable_seerr_webhook")),
        }
    except Exception:
        logger.exception("Config ARR non leggibile")

    try:
        queued = len(_get_scheduled_downloads())
    except Exception:
        queued = 0

    return render(request, "searchapp/cinema_system.html", {
        "nav_active": "settings",
        "providers": providers,
        "disabled_sites": disabled,
        "services": services,
        "arr": arr,
        "queued": queued,
        "app_version": __version__,
        "config_content": _conf_text("config.json"),
        "login_content": _conf_text("login.json"),
    })


__all__ = [
    "cinema_download", "cinema_search", "cinema_watchlist", "cinema_system",
]
