# 09.08.25
from __future__ import annotations

import os
import json
import logging
import asyncio
from contextlib import asynccontextmanager
from typing import Dict, Optional, Union

import ua_generator
from curl_cffi import requests
from curl_cffi.requests.impersonate import REAL_TARGET_MAP

from VibraVid.utils import config_manager


logger = logging.getLogger(__name__)
ua =  ua_generator.generate(device='desktop', browser=('chrome', 'edge'))
CONF_PROXY = config_manager.config.get_dict("REQUESTS", "proxy") or {}
USE_PROXY = bool(config_manager.config.get_bool("REQUESTS", "use_proxy"))


def _get_timeout() -> int:
    try:
        return int(config_manager.config.get_int("REQUESTS", "timeout"))
    except Exception:
        return 20


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


def get_available_browsers() -> Dict[str, str]:
    """Get the latest available browser impersonate versions."""
    browsers = dict(REAL_TARGET_MAP)
    return browsers


def get_browser_impersonate(browser: str = "chrome") -> str:
    """Get the latest available browser impersonate version from curl_cffi."""
    browser_versions = get_available_browsers()
    return browser_versions.get(browser.lower())


def create_client(*, 
    headers: Optional[Dict[str, str]] = None, cookies: Optional[Dict[str, str]] = None, timeout: Optional[Union[int, float]] = None,
    proxies: Optional[Dict[str, str]] = None, http2: bool = False, follow_redirects: bool = True, browser: str = "chrome",
):
    """
    Factory for a configured curl_cffi session.
    
    Args:
        headers: Optional custom headers
        cookies: Optional cookies to add
        timeout: Request timeout in seconds
        proxies: Optional proxy dict
        http2: Whether to use HTTP/2
        follow_redirects: Whether to follow redirects
        browser: Browser to impersonate (auto-selects latest version) e.g., 'chrome' -> 'chrome142', 'firefox' -> 'firefox144'
    
    Returns:
        Configured requests.Session() from curl_cffi
    """
    session = requests.Session()
    session.headers.update(_default_headers(headers))
    if cookies:
        session.cookies.update(cookies)
    session.timeout = timeout if timeout is not None else _get_timeout()
    proxy_value = proxies if proxies is not None else _get_proxies()
    if proxy_value:
        session.proxies = proxy_value
    session.impersonate = get_browser_impersonate(browser)
    session.allow_redirects = follow_redirects
    
    return session


class AsyncStreamResponse:
    """Wrapper for streaming responses in async context."""
    def __init__(self, response):
        self.response = response
        self.headers = response.headers
        self.status_code = response.status_code
        
    def raise_for_status(self):
        """Raise exception for bad status codes."""
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
    
    async def aiter_bytes(self, chunk_size: int = 8192):
        """Iterate over response content in chunks asynchronously."""
        for chunk in self.response.iter_content(chunk_size=chunk_size):
            yield chunk
            await asyncio.sleep(0)  # Yield control to event loop


class AsyncClient:
    """Async wrapper for curl_cffi client."""
    def __init__(self, session):
        self.session = session
    
    @asynccontextmanager
    async def stream(self, method: str, url: str, **kwargs):
        """Stream request wrapper for async context."""
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, 
            lambda: self.session.request(method, url, stream=True, **kwargs)
        )
        try:
            yield AsyncStreamResponse(response)
        finally:
            response.close()
    
    async def get(self, url: str, **kwargs):
        """Async GET request."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.session.get(url, **kwargs))
    
    async def post(self, url: str, **kwargs):
        """Async POST request."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.session.post(url, **kwargs))
    
    async def put(self, url: str, **kwargs):
        """Async PUT request."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.session.put(url, **kwargs))
    
    async def delete(self, url: str, **kwargs):
        """Async DELETE request."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.session.delete(url, **kwargs))
    
    async def patch(self, url: str, **kwargs):
        """Async PATCH request."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.session.patch(url, **kwargs))
    
    async def head(self, url: str, **kwargs):
        """Async HEAD request."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.session.head(url, **kwargs))
    
    async def request(self, method: str, url: str, **kwargs):
        """Async generic request."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: self.session.request(method, url, **kwargs))
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.session.close()


@asynccontextmanager
async def create_async_client(*, 
    headers: Optional[Dict[str, str]] = None, cookies: Optional[Dict[str, str]] = None, 
    timeout: Optional[Union[int, float]] = None, verify: Optional[bool] = None, 
    proxies: Optional[Dict[str, str]] = None, http2: bool = False, follow_redirects: bool = True,
    browser: str = "chrome",
):
    """
    Factory for an async-compatible curl_cffi session wrapper.
    
    Args:
        headers: Optional custom headers
        cookies: Optional cookies to add
        timeout: Request timeout in seconds
        verify: SSL verification
        proxies: Optional proxy dict
        http2: Whether to use HTTP/2
        follow_redirects: Whether to follow redirects
        browser: Browser to impersonate (auto-selects latest version)
    
    Returns:
        AsyncClient context manager
    """
    session = create_client(
        headers=headers, 
        cookies=cookies, 
        timeout=timeout, 
        proxies=proxies, 
        follow_redirects=follow_redirects,
        browser=browser
    )
    try:
        yield AsyncClient(session)
    finally:
        session.close()


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
        logger.info(f"Checking region availability for {site_name}...")
        location = get_my_location()
        if location.get('status') == 'fail' or 'error' in location:
            return True
            
        current_country = location.get('country_code')
        if current_country and current_country not in allowed_regions:
            logger.error(f"Site: {site_name}, unavailable outside {', '.join(allowed_regions)}.")
            return False
        
    except Exception as e:
        logger.error(f"Region check failed: {e}")
        
    return True