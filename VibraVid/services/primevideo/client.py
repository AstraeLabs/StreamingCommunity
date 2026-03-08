# 07.03.26

from rich.console import Console

from VibraVid.utils.http_client import create_client, get_headers


console = Console()


BASE_URL = "https://www.primevideo.com"
SPA_VERSION = "1.0.120445.0"
_COOKIES = {
    "i18n-prefs":  "EUR",
    "lc-main-av":  "it_IT",
    "av-timezone": "Europe/Rome",
}


class AmazonClient:
    def __init__(self):
        self.base_url = BASE_URL

    def _cookie_str(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in _COOKIES.items())

    def _common_headers(self) -> dict:
        return get_headers()

    def get_spa_headers(self, cid: str) -> dict:
        """Headers required for SPA (WebSPA) JSON API calls."""
        return {
            "accept": "application/json",
            "x-requested-with": "WebSPA",
            "x-purpose": "navigation",
            "x-amzn-client-ttl-seconds": "58.999",
            "referer": f"{self.base_url}/detail/{cid}/",
        }

    def get_json(self, url: str, extra_headers: dict = None) -> dict:
        """Perform a GET request and return parsed JSON response."""
        headers = {**self._common_headers(), **(extra_headers or {}), "cookie": self._cookie_str()}
        try:
            response = create_client(headers=headers).get(url, timeout=20)
            return response.json()
        except Exception as e:
            console.print(f"[red]Error fetching JSON from {url}: {e}")
            raise


# Singleton
_client: AmazonClient = None

def _get_client() -> AmazonClient:
    global _client
    if _client is None:
        _client = AmazonClient()
    return _client

def get_spa_headers(cid: str) -> dict:
    return _get_client().get_spa_headers(cid)

def get_json(url: str, extra_headers: dict = None) -> dict:
    return _get_client().get_json(url, extra_headers)