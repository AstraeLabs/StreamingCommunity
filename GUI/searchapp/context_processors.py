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