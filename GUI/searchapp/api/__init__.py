# 06.06.25

import os
import importlib
from typing import Dict, List

from .base import BaseStreamingAPI


_API_REGISTRY: Dict[str, type] = {}
_INITIALIZED = False
_PREFERRED_ORDER = [
    'streamingcommunity', 
    'guardaserie', 
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
    'mostraguarda'
]


def _initialize_registry():
    global _INITIALIZED
    if _INITIALIZED:
        return
        
    package_dir = os.path.dirname(__file__)
    api_files = [
        f[:-3] for f in os.listdir(package_dir) 
        if f.endswith('.py') and f not in ('base.py', '__init__.py')
    ]
    
    # Use preferred order first, then any remaining files
    sorted_files = [f for f in _PREFERRED_ORDER if f in api_files]
    sorted_files.extend([f for f in api_files if f not in _PREFERRED_ORDER])
    
    for idx, module_name in enumerate(sorted_files):
        try:
            module = importlib.import_module(f'.{module_name}', package=__package__)
            
            # Find the API class in the module
            for name, obj in module.__dict__.items():
                if (isinstance(obj, type) and 
                    issubclass(obj, BaseStreamingAPI) and 
                    obj is not BaseStreamingAPI):
                    
                    # Add _indice to the class
                    obj._indice = idx
                    _API_REGISTRY[module_name] = obj
                    break
        except Exception as e:
            print(f"[Warning] Could not load API '{module_name}': {e}")
    
    if not _API_REGISTRY:
        print("[CRITICAL] No streaming APIs could be loaded! Check that all dependencies are installed (pip install -r requirements.txt).")
    else:
        print(f"[Info] Loaded {len(_API_REGISTRY)} streaming APIs: {', '.join(_API_REGISTRY.keys())}")
    
    _INITIALIZED = True

_initialize_registry()


def get_available_sites() -> List[str]:
    """
    Get list of all available streaming sites.
    
    Returns:
        List of site identifiers
    """
    return list(_API_REGISTRY.keys())


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
]