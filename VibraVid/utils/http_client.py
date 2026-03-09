# 09.08.25
from __future__ import annotations

import os
import json

import httpx
import ua_generator
from curl_cffi import requests
from typing import Dict, Optional, Union

from VibraVid.utils import config_manager


ua =  ua_generator.generate(device='desktop', browser=('chrome', 'edge'))
CONF_PROXY = config_manager.config.get_dict("REQUESTS", "proxy") or {}
USE_PROXY = bool(config_manager.config.get_bool("REQUESTS", "use_proxy"))


def _get_timeout() -> int:
    try:
        return int(config_manager.config.get_int("REQUESTS", "timeout"))
    except Exception:
        return 20


def _get_verify() -> bool:
    try:
        return bool(config_manager.config.get_bool("REQUESTS", "verify"))
    except Exception:
        return True


def _get_proxies() -> Optional[Dict[str, str]]:
    """Return proxies dict if `USE_PROXY` is true and proxy config is present, else None."""
    if not USE_PROXY:
        return None

    try:
        proxies = CONF_PROXY if isinstance(CONF_PROXY, dict) else config_manager.config.get_dict("REQUESTS", "proxy")
        if not isinstance(proxies, dict):
            return None
        # Normalize empty strings
        cleaned: Dict[str, str] = {}
        for scheme, url in proxies.items():
            if isinstance(url, str) and url.strip():
                cleaned[scheme] = url.strip()
        return cleaned or None
    except Exception:
        return None


def _default_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {"User-Agent": get_userAgent()}
    if extra:
        headers.update(extra)
    return headers


def create_client(*, headers: Optional[Dict[str, str]] = None, cookies: Optional[Dict[str, str]] = None, timeout: Optional[Union[int, float]] = None,
    verify: Optional[bool] = None, proxies: Optional[Dict[str, str]] = None, http2: bool = False, follow_redirects: bool = True,
) -> httpx.Client:
    """Factory for a configured httpx.Client."""
    proxy_value = proxies if proxies is not None else _get_proxies()
    client_kwargs = dict(
        headers=_default_headers(headers),
        cookies=cookies,
        timeout=timeout if timeout is not None else _get_timeout(),
        verify=_get_verify() if verify is None else verify,
        follow_redirects=follow_redirects,
        http2=http2,
    )

    if proxy_value:
        # Try new-style 'proxies' kwarg first
        try:
            return httpx.Client(**client_kwargs, proxies=proxy_value)
        except TypeError:
            # Older httpx may require a single proxy URL via 'proxy'
            single_proxy = None
            if isinstance(proxy_value, dict):
                single_proxy = proxy_value.get("https") or proxy_value.get("http")
            elif isinstance(proxy_value, str):
                single_proxy = proxy_value

            # If we have a single proxy URL, try passing as 'proxy'
            if single_proxy:
                return httpx.Client(**client_kwargs, proxy=single_proxy)
            raise
    else:
        return httpx.Client(**client_kwargs)


def create_async_client(*, headers: Optional[Dict[str, str]] = None, cookies: Optional[Dict[str, str]] = None,
    timeout: Optional[Union[int, float]] = None, verify: Optional[bool] = None, proxies: Optional[Dict[str, str]] = None,
    http2: bool = False, follow_redirects: bool = True,
) -> httpx.AsyncClient:
    """Factory for a configured httpx.AsyncClient."""
    proxy_value = proxies if proxies is not None else _get_proxies()
    client_kwargs = dict(
        headers=_default_headers(headers),
        cookies=cookies,
        timeout=timeout if timeout is not None else _get_timeout(),
        verify=_get_verify() if verify is None else verify,
        follow_redirects=follow_redirects,
        http2=http2,
    )

    if proxy_value:
        try:
            return httpx.AsyncClient(**client_kwargs, proxies=proxy_value)
        except TypeError:
            single_proxy = None
            if isinstance(proxy_value, dict):
                single_proxy = proxy_value.get("https") or proxy_value.get("http")
            elif isinstance(proxy_value, str):
                single_proxy = proxy_value

            if single_proxy:
                return httpx.AsyncClient(**client_kwargs, proxy=single_proxy)
            raise
    else:
        return httpx.AsyncClient(**client_kwargs)


def create_client_curl(*, headers: Optional[Dict[str, str]] = None, cookies: Optional[Dict[str, str]] = None,
    timeout: Optional[Union[int, float]] = None, verify: Optional[bool] = None, proxies: Optional[Dict[str, str]] = None,
    impersonate: str = "chrome142", allow_redirects: bool = True,
):
    """Factory for a configured curl_cffi session."""
    session = requests.Session()
    session.headers.update(_default_headers(headers))
    if cookies:
        session.cookies.update(cookies)
    session.timeout = timeout if timeout is not None else _get_timeout()
    session.verify = _get_verify() if verify is None else verify
    proxy_value = proxies if proxies is not None else _get_proxies()
    if proxy_value:
        session.proxies = proxy_value
    session.impersonate = impersonate
    session.allow_redirects = allow_redirects
    
    return session


def get_userAgent() -> str:
    user_agent =  ua_generator.generate().text
    return user_agent


def get_headers() -> dict:
    return ua.headers.get()


def get_local_ip():
    """Get the local IP address of the machine without making external requests."""
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # doesn't even have to be reachable
        s.connect(('8.8.8.8', 1))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    
    except Exception:
        try:
            import socket
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return '127.0.0.1'


def get_my_location():
    cache_dir = os.path.join(os.getcwd(), ".cache")
    cache_file = os.path.join(cache_dir, "ip.json")
    local_ip = get_local_ip()
    
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
                if cached_data.get('local_ip') == local_ip:
                    return cached_data
        except Exception:
            pass

    try:
        url = 'http://ip-api.com/json/?fields=status,country,countryCode,city,query'
        response = create_client(headers=get_headers()).get(url, timeout=4)
        data = response.json()
        
        if data.get('status') == 'success':
            location = {'country': data['country'], 'country_code': data['countryCode'], 'city': data['city'], 'ip': data['query'], 'local_ip': local_ip}

            # Save to cache
            try:
                if not os.path.exists(cache_dir):
                    os.makedirs(cache_dir, exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(location, f, indent=4)
            except Exception:
                pass
            return location
        
        # Fallback to local IP if API fails
        return {'status': 'fail', 'country_code': 'XX', 'ip': local_ip, 'local_ip': local_ip}
    
    except Exception as e:
        return {'status': 'fail', 'country_code': 'XX', 'ip': local_ip, 'local_ip': local_ip, 'error': str(e)}


def check_region_availability(allowed_regions: list, site_name: str) -> bool:
    try:
        location = get_my_location()
        if location.get('status') == 'fail' or 'error' in location:
            return True
            
        current_country = location.get('country_code')
        if current_country and current_country not in allowed_regions:
            print(f"Site: {site_name}, unavailable outside {', '.join(allowed_regions)}.")
            return False    
    except Exception:
        pass
        
    return True