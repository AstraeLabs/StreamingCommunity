# 29.07.26

"""Thin adapter between the TUI and the existing VibraVid service layer.

Everything exposed here is blocking I/O and must be called from Textual
worker threads (@work(thread=True)), never from the UI thread. The goal is
zero changes to VibraVid/services and VibraVid/cli: the TUI reuses the
Django-free adapter registry in GUI/searchapp/api (search / get_series_metadata
/ start_download), the same uniform interface the web GUI is built on.
"""

import contextlib
import io
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Iterator, List, NamedTuple, Optional, Tuple

from VibraVid.services._base import load_search_functions

logger = logging.getLogger(__name__)


class SiteInfo(NamedTuple):
    """Static site metadata for the catalog screens."""

    name: str
    indice: int
    use_for: str
    source: str


_sites_cache: Optional[List[SiteInfo]] = None


def list_sites(refresh: bool = False) -> List[SiteInfo]:
    """Return visible site modules sorted by index.

    Reads only the lazy-loader metadata, so no site module gets imported.
    """
    global _sites_cache
    if _sites_cache is not None and not refresh:
        return _sites_cache

    search_functions = load_search_functions()
    sites = [
        SiteInfo(
            name=func.module_name,
            indice=func.indice,
            use_for=(func.use_for or "").lower(),
            source=func.source,
        )
        for func in search_functions.values()
        if not getattr(func, "hide", False)
    ]
    sites.sort(key=lambda s: (s.indice, s.name))
    logger.debug("TUI bridge loaded %d sites", len(sites))
    _sites_cache = sites
    return sites


def sites_by_category() -> Dict[str, List[SiteInfo]]:
    """Group visible sites by their _useFor category, preserving index order."""
    grouped: Dict[str, List[SiteInfo]] = {}
    for site in list_sites():
        grouped.setdefault(site.use_for or "other", []).append(site)
    return grouped


# ── stdio capture ─────────────────────────────────────────────────────────
# Service code (and the GUI adapters) print to stdout/stderr through rich or
# plain print(); inside a running Textual app that would corrupt the display.
# Textual's driver renders through sys.__stderr__, so replacing the dynamic
# sys.stdout/sys.stderr with a thread-aware proxy is safe.

class _ThreadStdProxy:
    """sys.stdout/sys.stderr replacement with per-thread capture buffers."""

    def __init__(self, stream_name: str) -> None:
        self._stream_name = stream_name
        self._local = threading.local()

    def write(self, data) -> int:
        data = str(data)
        capture = getattr(self._local, "capture", None)
        if capture is not None:
            return capture.write(data)
        if data.strip():
            logger.getChild("stdio").debug("[%s] %s", self._stream_name, data.rstrip())
        return len(data)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


def install_stdio_proxy() -> None:
    """Install the thread-aware proxies (idempotent)."""
    if not isinstance(sys.stdout, _ThreadStdProxy):
        sys.stdout = _ThreadStdProxy("stdout")
    if not isinstance(sys.stderr, _ThreadStdProxy):
        sys.stderr = _ThreadStdProxy("stderr")


@contextlib.contextmanager
def capture_stdio() -> Iterator[Tuple[io.StringIO, io.StringIO]]:
    """Capture stdout/stderr of the CURRENT thread while running service code."""
    out_buf, err_buf = io.StringIO(), io.StringIO()
    out_local = getattr(sys.stdout, "_local", None)
    err_local = getattr(sys.stderr, "_local", None)
    if out_local is None or err_local is None:
        yield out_buf, err_buf  # proxy not installed (tests): passthrough
        return

    prev_out = getattr(out_local, "capture", None)
    prev_err = getattr(err_local, "capture", None)
    out_local.capture, err_local.capture = out_buf, err_buf
    try:
        yield out_buf, err_buf
    finally:
        out_local.capture, err_local.capture = prev_out, prev_err
        for name, buf in (("stdout", out_buf), ("stderr", err_buf)):
            text = buf.getvalue()
            if text.strip():
                logger.getChild("stdio").debug("[captured %s]\n%s", name, text.rstrip())


# ── GUI adapter layer (Django-free registry) ──────────────────────────────

def preload_registry() -> None:
    """Import the GUI adapter registry early; its import prints warnings."""
    with capture_stdio():
        try:
            import GUI.searchapp.api  # noqa: F401
        except Exception:
            logger.exception("GUI adapter registry failed to load")


def adapter_available(site: str) -> bool:
    """Whether a GUI adapter exists for the site (else: queue-only fallback)."""
    from GUI.searchapp import api
    return api.is_site_available(site)


def available_adapters() -> List[str]:
    from GUI.searchapp import api
    return api.get_available_sites()


def _get_api(site: str):
    from GUI.searchapp import api
    return api.get_api(site)


def search_titles(site: str, query: str) -> list:
    """Run a catalog search on one site; returns a list of GUI Entries."""
    with capture_stdio():
        return _get_api(site).search(query)


def search_global(query: str, sites: Optional[List[str]] = None, max_workers: int = 6) -> Tuple[Dict[str, list], Dict[str, str]]:
    """Search every available adapter in parallel.

    Returns (results, errors): results maps site -> [Entries], errors maps
    site -> message for adapters that failed (offline, login required, ...).
    """
    available = sites if sites is not None else available_adapters()
    results: Dict[str, list] = {}
    errors: Dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(search_titles, site, query): site for site in available}
        for future in as_completed(futures):
            site = futures[future]
            try:
                items = future.result()
                if items:
                    results[site] = items
            except Exception as e:
                logger.debug("global search failed on %s: %s", site, e)
                errors[site] = str(e)
    return results, errors


def get_seasons(site: str, item) -> Optional[list]:
    """Return List[Season] for a series/album item, or None when not applicable."""
    with capture_stdio():
        return _get_api(site).get_series_metadata(item)


def start_download(site: str, item, season: Optional[str] = None, episodes: Optional[str] = None) -> bool:
    """Start a download via the GUI adapter layer.

    This reuses the same path as the web GUI: the adapter's start_download()
    method, which calls the service's search() with direct_item + selections.
    The download_tracker singleton is updated automatically by the service.
    """
    with capture_stdio():
        return _get_api(site).start_download(item, season=season, episodes=episodes)
