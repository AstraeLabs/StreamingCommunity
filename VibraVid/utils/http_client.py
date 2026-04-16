# 09.08.25

import asyncio
import functools
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Dict, Optional, Union

import ua_generator
from curl_cffi import requests
from curl_cffi.requests.impersonate import REAL_TARGET_MAP

from VibraVid.utils import config_manager


logger = logging.getLogger(__name__)
ua = ua_generator.generate(device="desktop", browser=("chrome", "edge"))
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

        # Normalize — drop empty strings
        cleaned: Dict[str, str] = {scheme: url.strip() for scheme, url in proxies.items() if isinstance(url, str) and url.strip()}
        return cleaned or None
    except Exception:
        return None


def get_proxy_url() -> Optional[str]:
    """Return a single proxy URL string suitable for passing to the C# download binary"""
    proxies = _get_proxies()
    if not proxies:
        return None
    return proxies.get("https") or proxies.get("http") or next(iter(proxies.values()), None)


def _default_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {"User-Agent": get_userAgent()}
    if extra:
        headers.update(extra)
    return headers


def get_available_browsers() -> Dict[str, str]:
    """Get the latest available browser impersonate versions."""
    return dict(REAL_TARGET_MAP)


def get_browser_impersonate(browser: str = "chrome") -> str:
    """Get the latest available browser impersonate version from curl_cffi."""
    return get_available_browsers().get(browser.lower())


def create_client(
    *,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    timeout: Optional[Union[int, float]] = None,
    proxies: Optional[Dict[str, str]] = None,
    http2: bool = False,
    follow_redirects: bool = True,
    browser: Optional[str] = "chrome",
):
    """
    Factory for a configured curl_cffi session."""
    session = requests.Session()
    session.headers.update(_default_headers(headers))
    if cookies:
        session.cookies.update(cookies)

    session.timeout = timeout if timeout is not None else _get_timeout()
    proxy_value = proxies if proxies is not None else _get_proxies()
    if proxy_value:
        session.proxies = proxy_value

    if browser:
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
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    async def aiter_bytes(self, chunk_size: int = 8192):
        """Iterate over response content in chunks asynchronously."""
        for chunk in self.response.iter_content(chunk_size=chunk_size):
            yield chunk
            await asyncio.sleep(0)


class AsyncClient:
    """Async wrapper for curl_cffi client."""
    def __init__(self, session):
        self.session = session

    @asynccontextmanager
    async def stream(self, method: str, url: str, **kwargs):
        """Stream request wrapper for async context."""
        loop = asyncio.get_running_loop()  # FIX #7
        response = await loop.run_in_executor(
            None,
            functools.partial(self.session.request, method, url, stream=True, **kwargs),  # FIX #9
        )
        try:
            yield AsyncStreamResponse(response)
        finally:
            response.close()

    async def get(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()  # FIX #7
        return await loop.run_in_executor(None, functools.partial(self.session.get, url, **kwargs))

    async def post(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.session.post, url, **kwargs))

    async def put(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.session.put, url, **kwargs))

    async def delete(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.session.delete, url, **kwargs))

    async def patch(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.session.patch, url, **kwargs))

    async def head(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.session.head, url, **kwargs))

    async def request(self, method: str, url: str, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(self.session.request, method, url, **kwargs))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.session.close()


@asynccontextmanager
async def create_async_client(
    *,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    timeout: Optional[Union[int, float]] = None,
    verify: Optional[bool] = None,
    proxies: Optional[Dict[str, str]] = None,
    http2: bool = False,
    follow_redirects: bool = True,
    browser: str = "chrome",
):
    """
    Factory for an async-compatible curl_cffi session wrapper."""
    session = create_client(
        headers=headers,
        cookies=cookies,
        timeout=timeout,
        proxies=proxies,
        follow_redirects=follow_redirects,
        browser=browser,
    )
    try:
        yield AsyncClient(session)
    finally:
        session.close()


def get_userAgent() -> str:
    return ua_generator.generate().text


def get_headers() -> dict:
    return ua.headers.get()


def get_my_location() -> dict:
    cache_dir = os.path.join(config_manager.base_path, ".cache")
    cache_file = os.path.join(cache_dir, "ip.json")

    try:
        url = "http://ip-api.com/json/?fields=status,country,countryCode,city,query"

        with create_client(headers=get_headers()) as c:
            response = c.get(url, timeout=4)

        data = response.json()

        if data.get("status") == "success":
            location = {
                "country": data["country"],
                "country_code": data["countryCode"],
                "city": data["city"],
                "ip": data["query"],
            }

            try:
                os.makedirs(cache_dir, exist_ok=True)
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(location, f, indent=4)
                logger.info(f"Location data cached to {cache_file}")
            except Exception as e:
                logger.warning(f"Could not cache location data: {e}")

            return location

        return {"status": "fail", "country_code": "XX", "ip": "0.0.0.0"}

    except Exception as e:
        return {"status": "fail", "country_code": "XX", "ip": "0.0.0.0", "error": str(e)}


def check_region_availability(allowed_regions: list, site_name: str) -> bool:
    try:
        logger.info(f"Checking region availability for {site_name}...")
        location = get_my_location()
        if location.get("status") == "fail" or "error" in location:
            logger.warning(f"Region check skipped or failed for {site_name}: {location.get('error', 'Unknown error')}")
            return True

        current_country = location.get("country_code")
        logger.info(f"Current detected region: {current_country}")

        if current_country and current_country not in allowed_regions:
            print(f"Site: {site_name} is not available in your region ({current_country}).")
            logger.error(f"Site: {site_name}, unavailable outside {', '.join(allowed_regions)}.")
            return False

        logger.info(f"Region check passed for {site_name} ({current_country})")

    except Exception as e:
        logger.error(f"Region check failed: {e}")

    return True