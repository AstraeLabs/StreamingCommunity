# 26.11.2025

import json
import re
import uuid
from urllib.parse import quote, urlencode

from VibraVid.utils.http_client import create_client, get_headers, get_userAgent

BOOT_URL = "https://boot.pluto.tv/v4/start"
STITCHER_HLS = "https://cfd-v4-service-channel-stitcher-use1-1.prd.pluto.tv"
APP_NAME = "web"
APP_VERSION = "9.4.0-9ca51ca10c3047fbafa7297708f146243146d125"
_pluto_api = None

HUB_GRAPHQL_URL = "https://pluto.tv/api/tn/hubs/graphql/"
HUB_SEARCH_PERSISTED_HASH = "47c3a615095a089a051062d724c32c021b45ac51785371373ef88dfa59ce3a05"
_SERIES_ID_RE = re.compile(r"/series/([a-f0-9]{24})/")
_SHOW_SLUG_RE = re.compile(r"^/shows/([^/]+)/?$")


def hub_search(query: str) -> list[dict]:
    """Search Pluto TV's web hub GraphQL endpoint (same backend used by pluto.tv/search)."""
    extensions = json.dumps({"tnPersistedDocumentHash": HUB_SEARCH_PERSISTED_HASH}, separators=(",", ":"))
    variables = json.dumps({"query": query}, separators=(",", ":"))
    url = f"{HUB_GRAPHQL_URL}?extensions={quote(extensions)}&variables={quote(variables)}&operationName=GetVODContent"

    headers = get_headers()
    headers["apollo-require-preflight"] = "true"

    response = create_client(headers=headers).get(url)
    response.raise_for_status()
    data = response.json()

    results = []
    for item in data.get("data", {}).get("searchVODContent") or []:
        content_type = item.get("contentType")
        item_id = item.get("id")
        slug = None

        if content_type == "show":
            for url_field in ("thumb", "thumbLandscape", "hero", "heroCompact"):
                match = _SERIES_ID_RE.search(item.get(url_field) or "")
                if match:
                    item_id = match.group(1)
                    break

            slug_match = _SHOW_SLUG_RE.match(item.get("href") or "")
            if slug_match:
                slug = slug_match.group(1)

        results.append(
            {
                "id": item_id,
                "name": item.get("title"),
                "type": "tv" if content_type == "show" else content_type,
                "slug": slug,
            }
        )

    return results


class PlutoAPI:
    def __init__(self):
        self.device_id = str(uuid.uuid4())
        self.user_agent = get_userAgent()
        self.session_data = None
        self._initialize()

    def _get_base_params(self, regione="IT"):
        """Get base parameters for boot requests"""
        return {
            "appName": APP_NAME,
            "appVersion": APP_VERSION,
            "clientID": self.device_id,
            "clientModelNumber": "1.0.0",
            "deviceDNT": "false",
            "deviceMake": "chrome",
            "deviceModel": "web",
            "deviceType": "web",
            "deviceVersion": "129.0.0.0",
            "marketingRegion": regione,
            "serverSideAds": "false",
            "sid": str(uuid.uuid4()),
            "userId": "",
        }

    def _initialize(self):
        """Initialize session with boot endpoint"""
        params = self._get_base_params()

        try:
            response = create_client(headers=get_headers()).get(BOOT_URL, params=params)
            response.raise_for_status()
            data = response.json()

            jwt = data.get("sessionToken", "")
            if not jwt:
                raise RuntimeError("JWT not found in boot response")

            self.session_data = {
                "jwt": jwt,
                "sid": params["sid"],
                "client_id": self.device_id,
                "regione": "IT",
                "vcc_id": "",
                "vpc_id": "",
            }

        except Exception as e:
            raise RuntimeError(f"Failed to initialize session: {e}") from e

    def get_session_for_content(self, content_ids: dict):
        """Get session data with content-specific parameters for vcc_id/vpc_id"""
        regione = content_ids.get("regione", "IT")
        params = self._get_base_params(regione)

        # Add content-specific params
        if "series_id" in content_ids:
            params["seriesIDs"] = content_ids["series_id"]
        if "episode_id" in content_ids:
            params["episodeSlugs"] = content_ids["episode_id"]
        if "movie_id" in content_ids:
            params["episodeSlugs"] = content_ids["movie_id"]

        try:
            response = create_client(headers=get_headers()).get(BOOT_URL, params=params)
            response.raise_for_status()
            data = response.json()

            jwt = data.get("sessionToken", "")
            vcc_id = (
                self._extract_from_data("vcc_id", data)
                or self._extract_from_data("vccId", data)
                or self._extract_from_data("contentCategoryId", data)
                or ""
            )
            vpc_id = (
                self._extract_from_data("vpc_id", data)
                or self._extract_from_data("vpcId", data)
                or self._extract_from_data("playlistContentId", data)
                or self._extract_from_data("contentId", data)
                or ""
            )

            # Also check in VOD episodes for vpc_id
            vod_episodes = self._extract_from_data("episodes", data) or []
            if isinstance(vod_episodes, list) and vod_episodes and not vpc_id:
                ep = vod_episodes[0]
                if isinstance(ep, dict):
                    vpc_id = ep.get("series", {}).get("_id", "") if isinstance(ep.get("series"), dict) else ""

            return {
                "jwt": jwt,
                "sid": params["sid"],
                "vcc_id": vcc_id,
                "vpc_id": vpc_id,
                "client_id": self.device_id,
                "regione": regione,
            }

        except Exception as e:
            raise RuntimeError(f"Failed to get session for content: {e}") from e

    def _extract_from_data(self, key, data):
        """Extract value from nested dict/list"""
        if isinstance(data, dict):
            if key in data:
                return data[key]
            for v in data.values():
                r = self._extract_from_data(key, v)
                if r is not None:
                    return r
        elif isinstance(data, list):
            for item in data:
                r = self._extract_from_data(key, item)
                if r is not None:
                    return r
        return None

    def get_request_headers(self):
        headers = get_headers()
        if self.session_data:
            headers["authorization"] = f"Bearer {self.session_data['jwt']}"
        return headers

    def get_cookies(self):
        return {}


def get_api():
    """Get or create Pluto API instance"""
    global _pluto_api
    if _pluto_api is None:
        _pluto_api = PlutoAPI()
    return _pluto_api


def get_playback_url_episode(episode_id: str, content_ids: dict = None):
    """Get the playback M3U8 URL for a given episode ID."""
    api = get_api()
    session = api.get_session_for_content(content_ids or {"episode_id": episode_id, "regione": "IT"})

    params = {
        "advertisingId": "",
        "appName": APP_NAME,
        "appVersion": APP_VERSION,
        "app_name": APP_NAME,
        "clientDeviceType": "0",
        "clientID": session["client_id"],
        "clientModelNumber": "1.0.0",
        "country": session["regione"],
        "deviceDNT": "false",
        "deviceId": session["client_id"],
        "deviceLat": "45.47",
        "deviceLon": "9.19",
        "deviceMake": "chrome",
        "deviceModel": "web",
        "deviceType": "web",
        "deviceVersion": "129.0.0.0",
        "marketingRegion": session["regione"],
        "serverSideAds": "false",
        "sessionID": session["sid"],
        "sid": session["sid"],
        "userId": "",
        "jwt": session["jwt"],
        "masterJWTPassthrough": "true",
        "includeExtendedEvents": "true",
    }

    if session.get("vcc_id"):
        params["vcc_id"] = session["vcc_id"]
    if session.get("vpc_id"):
        params["vpc_id"] = session["vpc_id"]

    qs = urlencode(params)
    return f"{STITCHER_HLS}/v2/stitch/hls/episode/{episode_id}/master.m3u8?{qs}"
