# 10.04.26

import logging
import time
from typing import Optional, Tuple, Dict


logger = logging.getLogger("http_fallback")


def _should_use_requests_fallback(error_str: str) -> bool:
    """Check if error warrants fallback to requests library"""
    return any(x in error_str.lower() for x in [
        "protocol_error",
        "stream",
        "not closed",
        "(92)",  # curl error 92
        "timed out",
        "timeout",
        "(28)",   # curl timeout error
    ])


def download_with_requests_fallback(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 10, max_retries: int = 5) -> Tuple[bool, bytes, str]:
    """Download using requests library as fallback for curl_cffi HTTP/2 errors"""
    if headers is None:
        headers = {}
    
    last_error = ""
    logger.debug("[Attempt 1] Trying requests library (HTTP/1.1 stable)")
    
    try:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        
        # Configure retry strategy for transient errors
        session = requests.Session()
        
        # Retry logic: 504, timeout, connection errors
        retry_strategy = Retry(
            total=max_retries - 1,  # additional retries beyond this function
            backoff_factor=1,       # exponential backoff
            status_forcelist=[504, 503, 502],  # retry on these status codes
            allowed_methods=["GET"],
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Add default headers
        session.headers.update(headers)
        
        # Disable keep-alive for better connection handling 
        session.headers["Connection"] = "close"
        
        try:
            logger.debug(f"  → Requesting {url[:60]}... with requests")
            response = session.get(
                url,
                timeout=timeout,
                stream=False,
                allow_redirects=True,
            )
            
            response.raise_for_status()
            
            if response.status_code in [200, 206]:
                data = response.content
                logger.info(f"  ✓ requests SUCCESS: {len(data)} bytes (HTTP {response.status_code})")
                return True, data, f"HTTP {response.status_code} (requests library)"
            
        except requests.exceptions.Timeout:
            logger.debug(f"  ⚠ requests timeout after {timeout}s")
            last_error = "Timeout (requests)"
        
        except requests.exceptions.ConnectionError as e:
            logger.debug(f"  ⚠ requests connection error: {str(e)[:80]}")
            last_error = f"Connection error (requests): {str(e)[:50]}"
        
        except requests.exceptions.HTTPError as e:
            status = getattr(e.response, 'status_code', '?')
            logger.debug(f"  ⚠ requests HTTP {status}: {str(e)[:80]}")
            last_error = f"HTTP {status} (requests)"
        
        except Exception as e:
            error_str = str(e)
            logger.debug(f"  ⚠ requests exception: {type(e).__name__}: {error_str[:80]}")
            last_error = f"{type(e).__name__}: {error_str[:50]}"
    
    except ImportError:
        logger.warning("requests library not available - cannot fallback")
        return False, b"", "requests not installed"
    
    except Exception as e:
        logger.error(f"Session setup failed: {type(e).__name__}: {str(e)[:80]}")
        last_error = str(e)
    
    if "timeout" in last_error.lower() or "connection" in last_error.lower():
        for attempt in range(2, max_retries + 1):
            wait_time = min(2 ** (attempt - 2), 60)  # 1s, 2s, 4s, 8s, 16s, 32s, 60s
            logger.info(f"  [Retry {attempt}/{max_retries}] Waiting {wait_time}s...")
            time.sleep(wait_time)
            
            try:
                import requests
                
                session = requests.Session()
                session.headers.update(headers)
                session.headers["Connection"] = "close"
                
                response = session.get(
                    url,
                    timeout=timeout,
                    stream=False,
                    allow_redirects=True,
                )
                response.raise_for_status()
                
                if response.status_code in [200, 206]:
                    data = response.content
                    logger.info(f"  ✓ requests SUCCESS after retry {attempt}: {len(data)} bytes")
                    return True, data, f"HTTP {response.status_code} (after retry)"
            
            except Exception as e:
                logger.debug(f"  Retry {attempt} failed: {type(e).__name__}")
                last_error = str(e)
    
    logger.error(f"Download failed: {last_error}")
    return False, b"", last_error

_original_get = None
_fallback_active = False


def patch_curl_cffi_with_requests_fallback():
    """Monkey-patch curl_cffi.Session.get() to use requests as fallback"""
    global _original_get, _fallback_active
    
    try:
        from curl_cffi.requests import Session
        
        if _original_get is None:
            _original_get = Session.get
            
            def _patched_get(self, url, **kwargs):
                """
                Wrapper that falls back to requests on HTTP/2 errors
                """
                try:
                    # PRIMARY: try curl_cffi (faster)
                    response = _original_get(self, url, **kwargs)
                    return response
                
                except Exception as exc:
                    error_str = str(exc)
                    
                    # Check if it's an HTTP/2 error worth falling back
                    if not _should_use_requests_fallback(error_str):
                        raise  # Re-raise non-HTTP/2 errors
                    
                    logger.warning(
                        f"HTTP/2/{type(exc).__name__} error, "
                        f"falling back to requests library: {error_str[:60]}"
                    )
                    
                    # FALLBACK: use requests library
                    headers = dict(self.headers) if hasattr(self, 'headers') else {}
                    timeout = kwargs.get('timeout', 10)
                    
                    success, data, msg = download_with_requests_fallback(
                        url,
                        headers=headers,
                        timeout=timeout,
                        max_retries=5,
                    )
                    
                    if success:
                        # Create mock response object that mimics curl_cffi response
                        class MockResponse:
                            def __init__(self, content):
                                self.content = content
                                self.status_code = 200
                                self.headers = {}
                                self.text = content.decode('utf-8', errors='replace')
                            
                            def raise_for_status(self):
                                pass
                            
                            def iter_content(self, chunk_size=8192):
                                for i in range(0, len(self.content), chunk_size):
                                    yield self.content[i:i+chunk_size]
                        
                        logger.info(f"  → Fallback SUCCESS: {len(data)} bytes via requests")
                        return MockResponse(data)
                    else:
                        # Fallback also failed - re-raise original exception
                        logger.error(f"  → Fallback FAILED: {msg}")
                        raise
            
            Session.get = _patched_get
            _fallback_active = True
            logger.info("✓ curl_cffi.Session.get() patched with requests fallback")
    
    except ImportError:
        logger.warning("curl_cffi not available - patching skipped")
    except Exception as exc:
        logger.error(f"Failed to patch curl_cffi: {exc}")


def is_fallback_active() -> bool:
    """Check if the patch is currently active"""
    return _fallback_active