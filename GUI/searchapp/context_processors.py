# 06.06.25

from VibraVid.upload.version import __version__
from VibraVid.core.ui.tracker import  DownloadTracker


def version_context(request):
    """Add version to template context."""
    return {
        'app_version': __version__,
    }


def active_downloads_context(request):
    """Add active downloads count to template context."""
    tracker = DownloadTracker()
    active_downloads = tracker.get_active_downloads()
    return {
        'active_downloads_count': len(active_downloads),
    }


def arr_stack_context(request):
    """Expose whether the ARR queue entry should appear in navigation."""
    try:
        from .arr.arr_service import _load_arr_config

        cfg = _load_arr_config()
        arr_enabled = bool(cfg.get("enabled"))
        sonarr_cfg = cfg.get("sonarr", {}) or {}
        radarr_cfg = cfg.get("radarr", {}) or {}

        has_sonarr = bool(sonarr_cfg.get("url") and sonarr_cfg.get("api_key"))
        has_radarr = bool(radarr_cfg.get("url") and radarr_cfg.get("api_key"))
        has_seerr = bool(cfg.get("enable_seerr_webhook"))
        has_native_webhook = bool(
            cfg.get("enable_sonarr_webhook") or cfg.get("enable_radarr_webhook")
        )

        show_arr_stack_nav = arr_enabled and (
            has_sonarr or has_radarr or has_seerr or has_native_webhook
        )
    except Exception:
        show_arr_stack_nav = False

    return {
        "show_arr_stack_nav": show_arr_stack_nav,
    }
