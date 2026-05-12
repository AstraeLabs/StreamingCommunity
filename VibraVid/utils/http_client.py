# 09.08.25

import asyncio
import functools
import json
import logging
import os
from contextlib import asynccontextmanager, contextmanager

from typing import Dict, Optional, Union

import ua_generator
from curl_cffi import requests
from curl_cffi.const import CurlHttpVersion
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

        # Normalize — drop empty strings
        cleaned: Dict[str, str] = {scheme: url.strip() for scheme, url in proxies.items() if isinstance(url, str) and url.strip()}
        return cleaned or None
    except Exception:
        return None


def get_proxy_url() -> Optional[str]:
    """Return a single proxy URL string suitable for passing to the C# download binary."""
    proxies = _get_proxies()
    if not proxies:
        return None
    return proxies.get("https") or proxies.get("http") or next(iter(proxies.values()), None)


def _default_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    headers = {}

    if not extra or "user-agent" not in {k.lower() for k in extra.keys()}:
        headers["User-Agent"] = get_userAgent()
        logger.info(f"Using User-Agent: {headers['User-Agent']}")

    if extra:
        headers.update(extra)
    
    return headers


def get_available_browsers() -> Dict[str, str]:
    """Get the latest available browser impersonate versions."""
    return dict(REAL_TARGET_MAP)


def get_browser_impersonate(browser: str = "chrome") -> Optional[str]:
    """Get the latest available browser impersonate version from curl_cffi."""
    available = get_available_browsers()
    result = available.get(browser.lower())
    if result is None:
        logger.warning(f"Browser '{browser}' not found in impersonate map, falling back to 'chrome'.")
        result = available.get("chrome")
    return result


def create_client(
    *,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    timeout: Optional[Union[int, float]] = None,
    verify: Optional[bool] = None,
    proxies: Optional[Dict[str, str]] = None,
    http2: bool = False,
    follow_redirects: bool = True,
    browser: Optional[str] = "chrome",
) -> requests.Session:
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

    if http2:
        session.http_version = CurlHttpVersion.V2TLS

    if browser:
        impersonate = get_browser_impersonate(browser)
        if impersonate:
            session.impersonate = impersonate

    session.allow_redirects = follow_redirects

    return session


@contextmanager
def open_client(
    *,
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    timeout: Optional[Union[int, float]] = None,
    verify: Optional[bool] = None,
    proxies: Optional[Dict[str, str]] = None,
    http2: bool = False,
    follow_redirects: bool = True,
    browser: Optional[str] = "chrome",
):
    """Context-manager wrapper around :func:`create_client`"""
    session = create_client(
        headers=headers,
        cookies=cookies,
        timeout=timeout,
        verify=verify,
        proxies=proxies,
        http2=http2,
        follow_redirects=follow_redirects,
        browser=browser,
    )
    try:
        yield session
    finally:
        session.close()


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
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(
            None,
            functools.partial(self.session.request, method, url, stream=True, **kwargs),
        )
        try:
            yield AsyncStreamResponse(response)
        finally:
            response.close()

    async def get(self, url: str, **kwargs):
        loop = asyncio.get_running_loop()
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
    """Context-manager factory for an async-compatible curl_cffi session wrapper."""
    session = requests.Session()
    session.headers.update(_default_headers(headers))

    if cookies:
        session.cookies.update(cookies)

    session.timeout = timeout if timeout is not None else _get_timeout()
    session.verify = _get_verify() if verify is None else verify

    proxy_value = proxies if proxies is not None else _get_proxies()
    if proxy_value:
        session.proxies = proxy_value

    if http2:
        session.http_version = CurlHttpVersion.V2TLS

    if browser:
        impersonate = get_browser_impersonate(browser)
        if impersonate:
            session.impersonate = impersonate

    session.allow_redirects = follow_redirects

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

        with open_client(headers=get_headers()) as c:
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
        logger.error("Region check failed: %s", e)

    return True
