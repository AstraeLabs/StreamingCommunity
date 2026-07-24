# 06.06.25

import os
import importlib
from typing import Dict, List

from .base import BaseStreamingAPI


def _is_hidden(module_name: str) -> bool:
    """Whether the underlying service module declares `_hide = True`."""
    for candidate in (f"VibraVid.services.{module_name}", module_name):
        try:
            service_module = importlib.import_module(candidate)
        except Exception:
            continue
        return bool(getattr(service_module, "_hide", False))
    return False


_API_REGISTRY: Dict[str, type] = {}
_LOAD_ERRORS: List[str] = []
_INITIALIZED = False
_PREFERRED_ORDER = [
    'streamingcommunity',
    'animeunity',
    'animeworld',
    'crunchyroll',
    'primevideo',
    'mediasetinfinity',
    'raiplay',
    'discoveryplus',
    'discovery',
    'dmax',
    'nove',
    'realtime',
    'homegardentv',
    'foodnetwork',
    'tubitv',
    'cinezo',
    'altadefinzione',
    'eurostreaming',
    'amazon_music'
]
_OPTIONAL_EXTERNAL = {
    'primevideo'
}
_SITE_CATEGORIES: Dict[str, str] = {}


def _initialize_registry():
    global _INITIALIZED
    if _INITIALIZED:
        return

    package_dir = os.path.dirname(__file__)
    api_files = [
        f[:-3] for f in os.listdir(package_dir)
        if f.endswith('.py') and f not in ('base.py', '__init__.py', 'generic.py')
    ]

    # Use preferred order first, then any remaining files
    sorted_files = [f for f in _PREFERRED_ORDER if f in api_files]
    sorted_files.extend([f for f in api_files if f not in _PREFERRED_ORDER])

    # Build into a local dict first; only commit to _API_REGISTRY if we end up
    # with at least as many services as we already had. Without this, a bad
    # reload (e.g. one module raising during import) can silently shrink the
    # registry and the dropdown loses services that were working before.
    previous_count = len(_API_REGISTRY)
    new_registry: Dict[str, type] = {}
    load_errors: List[str] = []
    _LOAD_ERRORS.clear()

    for idx, module_name in enumerate(sorted_files):
        if _is_hidden(module_name):
            continue

        try:
            module = importlib.import_module(f'.{module_name}', package=__package__)
            api_cls = None
            for name, obj in module.__dict__.items():
                if (isinstance(obj, type) and
                    issubclass(obj, BaseStreamingAPI) and
                    obj is not BaseStreamingAPI and
                    obj.__module__ == module.__name__):
                    api_cls = obj
                    break

            if api_cls is None:
                raise RuntimeError("no BaseStreamingAPI subclass found in module")
            
            api_cls._indice = idx
            new_registry[module_name] = api_cls
        except Exception as e:
            if module_name in _OPTIONAL_EXTERNAL and isinstance(e, ModuleNotFoundError):
                print(f"[Info] '{module_name}' non disponibile in questo checkout (servizio esterno)")
                continue

            err = f"{module_name}: {type(e).__name__}: {e}"
            load_errors.append(err)
            print(f"[Warning] Could not load API '{module_name}': {e}")

    # Commit: if new load found at least as many as before, replace; otherwise
    # only ADD newly-discovered services so we never lose ones that were already
    # working.
    if len(new_registry) >= previous_count:
        _API_REGISTRY.clear()
        _API_REGISTRY.update(new_registry)
    else:
        for k, v in new_registry.items():
            _API_REGISTRY[k] = v
        print(f"[Warning] Reload produced fewer APIs ({len(new_registry)}) than before ({previous_count}); kept old entries to avoid losing services.")

    _LOAD_ERRORS.extend(load_errors)

    if not _API_REGISTRY:
        print("[CRITICAL] No streaming APIs could be loaded! Check that all dependencies are installed (pip install -r requirements.txt).")
        if load_errors:
            print("[CRITICAL] Load errors:")
            for err in load_errors:
                print(f"  - {err}")
    else:
        if load_errors:
            print(f"[Warning] {len(load_errors)} API(s) failed to load:")
            for err in load_errors:
                print(f"  - {err}")

    _INITIALIZED = True


def get_load_errors() -> List[str]:
    """Return the list of import errors from the last _initialize_registry() call."""
    return list(_LOAD_ERRORS)


_initialize_registry()


def get_available_sites() -> List[str]:
    """
    Get list of all available streaming sites.

    Returns:
        List of site identifiers
    """
    return list(_API_REGISTRY.keys())


def get_site_categories() -> Dict[str, str]:
    """
    Map each available GUI site to its content category (the service's ``_useFor``, e.g. 'Film_Serie', 'Anime', 'Serie', 'song').
    """
    global _SITE_CATEGORIES
    if _SITE_CATEGORIES:
        return dict(_SITE_CATEGORIES)

    try:
        from VibraVid.services._base.site_loader import load_search_functions
        fns = load_search_functions()
    except Exception:
        return {}

    result: Dict[str, str] = {}
    for alias, lazy in fns.items():
        site = alias[: -len("_search")] if alias.endswith("_search") else alias
        if site not in _API_REGISTRY:
            continue
        try:
            category = lazy.use_for
        except Exception:
            continue
        if category:
            result[site] = category

    _SITE_CATEGORIES = result
    return dict(result)


def reset_site_categories_cache() -> None:
    """Clear the cached site->category map so the next get_site_categories() call recomputes it.

    Needed after a hot reload of the registry (e.g. GUI service upload): without this
    the category grouping keeps referring to services that existed before the reload.
    """
    global _SITE_CATEGORIES
    _SITE_CATEGORIES = {}


def get_api(site: str) -> BaseStreamingAPI:
    """
    Get API instance for specified site.

    Args:
        site: Site identifier (e.g., 'streamingcommunity', 'animeunity', 'mostraguarda')

    Returns:
        API instance

    Raises:
        ValueError: If site is not supported
    """
    site_lower = site.lower().strip()

    if site_lower not in _API_REGISTRY:
        available = ', '.join(_API_REGISTRY.keys())
        raise ValueError(f"Site '{site}' not supported. Available sites: {available}")

    api_class = _API_REGISTRY[site_lower]
    return api_class()


def is_site_available(site: str) -> bool:
    """
    Check if a site is available.

    Args:
        site: Site identifier

    Returns:
        True if site is available
    """
    return site.lower().strip() in _API_REGISTRY


__all__ = [
    'get_available_sites',
    'get_api',
    'is_site_available',
    'get_load_errors',
    'reset_site_categories_cache',
]
