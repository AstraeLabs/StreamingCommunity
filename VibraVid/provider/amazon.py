# 17.07.26

import json
import logging
import random
import re
import string
import threading
import time

from rich.console import Console

from VibraVid.utils import disk_cache
from VibraVid.utils.http_client import create_client

console = Console()
logger = logging.getLogger(__name__)


BASE_URL = "https://music.amazon.com"
SKILL_URL = "https://eu.mesk.skill.music.a2z.com/api"
_CONFIG_TTL_SECONDS = 8 * 60
_CACHE_SERVICE = "amazon_provider"
_CACHE_NAME = "config"

ENDPOINTS = {
    "config": f"{BASE_URL}/config.json",
    "global_search": f"{SKILL_URL}/showSearch",
    "search_songs": f"{SKILL_URL}/searchCatalogTracks",
    "search_albums": f"{SKILL_URL}/searchCatalogAlbums",
    "search_artists": f"{SKILL_URL}/searchCatalogArtists",
    "track_info": f"{SKILL_URL}/cosmicTrack/displayCatalogTrack",
    "album_info": f"{SKILL_URL}/showCatalogAlbum",
    "artist_info": f"{SKILL_URL}/explore/v1/showCatalogArtist",
}
_TRANSPORT_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "text/plain;charset=UTF-8",
    "origin": BASE_URL,
    "referer": f"{BASE_URL}/",
}


def _clean_image_url(url):
    """Strip Amazon's crop/size query params so images come back at full quality."""
    if not url:
        return None
    return re.sub(r"(/I/[A-Za-z0-9\-]+).*?(\.[^.]+)$", r"\1\2", url)


def _duration_to_seconds(duration_string):
    """Convert 'MM:SS' / 'HH:MM:SS' / plain-seconds text into an int."""
    if not duration_string:
        return 0

    text = duration_string.strip()

    try:
        if re.fullmatch(r"\d+:\d+:\d+", text):
            hours, minutes, seconds = (int(p) for p in text.split(":"))
            return hours * 3600 + minutes * 60 + seconds

        if re.fullmatch(r"\d+:\d+", text):
            first, second = (int(p) for p in text.split(":"))
            if first >= 60:
                return (first // 60) * 3600 + (first % 60) * 60 + second
            return first * 60 + second

        if re.fullmatch(r"\d+", text):
            return int(text)
    except ValueError:
        pass

    return 0


def _extract_release_date(text):
    """Pull a date like 'APR 03 2013' out of the album header tertiary text."""
    if not text:
        return None
    match = re.search(r"([A-Z]{3}\s+\d{1,2}\s+\d{4})", text, re.IGNORECASE)
    return match.group(1) if match else None


def _extract_songs_count(text):
    if not text:
        return None
    match = re.search(r"(\d+)\s*SONGS?", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _extract_duration_from_text(text):
    """Pull a total duration (in seconds) out of text like '54 MINUTES'."""
    if not text:
        return None

    match = re.search(r"(\d+)\s*HOURS?\s*AND\s*(\d+)\s*MINUTES?", text, re.IGNORECASE)
    if match:
        return int(match.group(1)) * 3600 + int(match.group(2)) * 60

    match = re.search(r"(\d+)\s*MINUTES?\s*AND\s*(\d+)\s*SECONDS?", text, re.IGNORECASE)
    if match:
        return int(match.group(1)) * 60 + int(match.group(2))

    match = re.search(r"(\d+)\s*MINUTES?", text, re.IGNORECASE)
    if match:
        return int(match.group(1)) * 60

    return None


class AmazonMusicClient:
    _config_lock = threading.Lock()
    _config_cache: dict | None = None
    _config_fetched_at: float = 0.0

    def __init__(self):
        self.base_url = BASE_URL

    def _fetch_config(self):
        """Fetch music.amazon.com's public config.json (device/session/csrf bootstrap)."""
        with type(self)._config_lock:
            now = time.time()
            if type(self)._config_cache is not None and now - type(self)._config_fetched_at < _CONFIG_TTL_SECONDS:
                return type(self)._config_cache

            cached = disk_cache.load(_CACHE_SERVICE, _CACHE_NAME)
            if disk_cache.is_fresh(cached):
                type(self)._config_cache = cached["config"]
                type(self)._config_fetched_at = cached["fetched_at"]
                return type(self)._config_cache

            with create_client(headers=_TRANSPORT_HEADERS) as client:
                response = client.get(ENDPOINTS["config"], timeout=8)
            response.raise_for_status()
            config = response.json()

            type(self)._config_cache = config
            type(self)._config_fetched_at = now
            disk_cache.save(
                _CACHE_SERVICE,
                _CACHE_NAME,
                {
                    "config": config,
                    "fetched_at": now,
                    "expiry": now + _CONFIG_TTL_SECONDS,
                },
            )
            return config

    def _build_amazon_headers(self, config, page_url=""):
        csrf = config.get("csrf") or {}
        request_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=13))

        return {
            "x-amzn-authentication": json.dumps(
                {
                    "interface": "ClientAuthenticationInterface.v1_0.ClientTokenElement",
                    "accessToken": config.get("accessToken", ""),
                }
            ),
            "x-amzn-device-model": "WEBPLAYER",
            "x-amzn-device-width": "1920",
            "x-amzn-device-family": "WebPlayer",
            "x-amzn-device-id": config.get("deviceId", ""),
            "x-amzn-user-agent": "Mozilla/5.0",
            "x-amzn-session-id": config.get("sessionId", ""),
            "x-amzn-device-height": "1080",
            "x-amzn-request-id": request_id,
            "x-amzn-device-language": "en_US",
            "x-amzn-currency-of-preference": "USD",
            "x-amzn-os-version": "1.0",
            "x-amzn-application-version": config.get("version", ""),
            "x-amzn-device-time-zone": "Europe/Rome",
            "x-amzn-timestamp": str(int(time.time() * 1000)),
            "x-amzn-csrf": json.dumps(
                {
                    "interface": "CSRFInterface.v1_0.CSRFHeaderElement",
                    "token": csrf.get("token", ""),
                    "timestamp": str(csrf.get("ts", "")),
                    "rndNonce": str(csrf.get("rnd", "")),
                }
            ),
            "x-amzn-music-domain": "music.amazon.com",
            "x-amzn-referer": "music.amazon.com",
            "x-amzn-affiliate-tags": "",
            "x-amzn-ref-marker": "",
            "x-amzn-page-url": page_url,
            "x-amzn-weblab-id-overrides": "",
            "x-amzn-video-player-token": "",
            "x-amzn-feature-flags": "hd-supported,uhd-supported",
            "x-amzn-has-profile-id": "",
            "x-amzn-age-band": "",
        }

    def _post(self, url, body, page_url="", timeout=15):
        config = self._fetch_config()
        amzn_headers = self._build_amazon_headers(config, page_url)

        payload = dict(body)
        payload["headers"] = json.dumps(amzn_headers)

        with create_client(headers=_TRANSPORT_HEADERS) as client:
            response = client.post(url, data=json.dumps(payload), timeout=timeout)
        response.raise_for_status()
        return response.json()

    def _user_hash(self):
        return json.dumps({"level": "LIBRARY_MEMBER"})

    def search_songs(self, query: str, limit: int = 10) -> list:
        """Search tracks by free-text query. Returns basic metadata (no duration/ISRC)."""
        try:
            data = self._post(
                ENDPOINTS["search_songs"],
                {"keyword": query, "userHash": self._user_hash()},
                page_url=f"{BASE_URL}/search/{query}/songs",
            )
        except Exception as e:
            console.log(f"[red]Amazon Music: songs search failed for '{query}': {e}[/red]")
            return []

        items = (((data.get("methods") or [{}])[0].get("template") or {}).get("widgets") or [{}])[0].get("items") or []
        if limit and limit > 0:
            items = items[:limit]

        songs = []
        for item in items:
            storage_key = ((item.get("iconButton") or {}).get("observer") or {}).get("storageKey")
            if not storage_key or ":" not in storage_key:
                continue
            album_id, song_id = storage_key.split(":", 1)
            if not song_id:
                continue

            secondary_link = (item.get("secondaryLink") or {}).get("deeplink")
            artist_id = (
                secondary_link.split("/artists/")[1].split("/")[0]
                if secondary_link and "/artists/" in secondary_link
                else ""
            )

            context_options = (item.get("contextMenu") or {}).get("options") or [{}]
            album_template = ((context_options[0].get("onItemSelected") or [{}, {}])[1] or {}).get("template") or {}
            album_url = (((album_template.get("templateData") or {}).get("seoHead") or {}).get("link") or [{}])[0].get(
                "href"
            ) or f"{BASE_URL}/albums/{album_id}"

            songs.append(
                {
                    "id": song_id,
                    "title": (item.get("primaryText") or {}).get("text") or "Unknown Title",
                    "url": f"{BASE_URL}/tracks/{song_id}",
                    "image": _clean_image_url(item.get("image")),
                    "isrc": None,
                    "artist": {
                        "id": artist_id,
                        "name": item.get("secondaryText") or "Unknown Artist",
                        "url": f"{BASE_URL}{secondary_link}" if secondary_link else None,
                    },
                    "album": {
                        "id": album_id,
                        "name": album_template.get("headerText", {}).get("text") or "Unknown Album",
                        "url": album_url,
                    },
                }
            )

        return songs

    def search_albums(self, query: str) -> list:
        """Search albums by free-text query."""
        try:
            data = self._post(
                ENDPOINTS["search_albums"],
                {"keyword": query, "userHash": self._user_hash()},
                page_url=f"{BASE_URL}/search/{query}/albums",
            )
        except Exception as e:
            console.log(f"[red]Amazon Music: albums search failed for '{query}': {e}[/red]")
            return []

        items = (((data.get("methods") or [{}])[0].get("template") or {}).get("widgets") or [{}])[0].get("items") or []

        albums = []
        for item in items:
            album_id = ((item.get("iconButton") or {}).get("observer") or {}).get("storageKey")
            if not album_id:
                link = (item.get("primaryLink") or {}).get("deeplink") or ""
                if "/albums/" in link:
                    album_id = link.split("/albums/")[1].split("/")[0]
            if not album_id:
                continue

            secondary_link = (item.get("secondaryLink") or {}).get("deeplink")
            artist_id = (
                secondary_link.split("/artists/")[1].split("/")[0]
                if secondary_link and "/artists/" in secondary_link
                else None
            )

            albums.append(
                {
                    "id": album_id,
                    "name": (item.get("primaryText") or {}).get("text") or "Unknown Album",
                    "url": f"{BASE_URL}/albums/{album_id}",
                    "image": _clean_image_url(item.get("image")),
                    "artist": {
                        "id": artist_id,
                        "name": item.get("secondaryText") or "Unknown Artist",
                        "url": f"{BASE_URL}{secondary_link}" if secondary_link else None,
                    },
                }
            )

        return albums

    def search_artists(self, query: str) -> list:
        """Search artists by free-text query."""
        try:
            data = self._post(
                ENDPOINTS["search_artists"],
                {"keyword": query, "userHash": self._user_hash()},
                page_url=f"{BASE_URL}/search/{query}/artists",
            )
        except Exception as e:
            console.log(f"[red]Amazon Music: artists search failed for '{query}': {e}[/red]")
            return []

        items = (((data.get("methods") or [{}])[0].get("template") or {}).get("widgets") or [{}])[0].get("items") or []

        artists = []
        for item in items:
            artist_id = ((item.get("iconButton") or {}).get("observer") or {}).get("storageKey")
            if not artist_id:
                continue

            artists.append(
                {
                    "id": artist_id,
                    "name": (item.get("primaryText") or {}).get("text") or "Unknown Artist",
                    "url": f"{BASE_URL}/artists/{artist_id}",
                    "image": _clean_image_url(item.get("image")),
                }
            )

        return artists

    def search(self, query: str) -> dict:
        """Global search: returns songs, albums and artists in one call."""
        return {
            "songs": self.search_songs(query),
            "albums": self.search_albums(query),
            "artists": self.search_artists(query),
        }

    def get_track(self, track_id: str) -> dict | None:
        """Get full metadata (incl. ISRC + duration) for a single track by ID."""
        try:
            data = self._post(
                ENDPOINTS["track_info"],
                {"id": track_id, "userHash": self._user_hash()},
                page_url=f"{BASE_URL}/tracks/{track_id}",
            )
        except Exception as e:
            console.log(f"[red]Amazon Music: track lookup failed for '{track_id}': {e}[/red]")
            return None

        methods = data.get("methods") or []
        if not methods or "template" not in methods[0]:
            logger.info(f"Amazon Music: track '{track_id}' not found or no longer available")
            return None

        template = methods[0]["template"]
        widgets = template.get("widgets") or []
        tracklist_widget = next((w for w in widgets if "album tracklist" in (w.get("header") or "").lower()), None)
        if not tracklist_widget:
            return None

        track_item = None
        for item in tracklist_widget.get("items") or []:
            deeplink = (item.get("primaryTextLink") or {}).get("deeplink")
            if deeplink and deeplink.split("/tracks/")[-1] == track_id:
                track_item = item
                break

        if not track_item:
            return None

        context_options = (template.get("contextMenu") or {}).get("options") or []
        album_template = {}
        if len(context_options) > 1:
            selected = context_options[1].get("onItemSelected") or []
            if len(selected) > 1:
                album_template = selected[1].get("template") or {}

        album_id = None
        template_data = album_template.get("templateData") or {}
        if template_data.get("deeplink"):
            album_id = template_data["deeplink"].split("/albums/")[-1]

        header_link = (template.get("headerPrimaryTextLink") or {}).get("deeplink")
        artist_id = (
            header_link.split("/artists/")[1].split("/")[0] if header_link and "/artists/" in header_link else None
        )

        isrc = None
        seo_scripts = ((template.get("templateData") or {}).get("seoHead") or {}).get("script") or []
        for script in seo_scripts:
            try:
                json_ld = json.loads(script.get("innerHTML") or "{}")
                if json_ld.get("isrcCode"):
                    isrc = json_ld["isrcCode"]
                    break
            except (json.JSONDecodeError, TypeError):
                continue

        return {
            "id": track_id,
            "title": (template.get("headerText") or {}).get("text") or "Unknown Title",
            "url": f"{BASE_URL}/tracks/{track_id}",
            "image": template.get("headerImage"),
            "duration": _duration_to_seconds(track_item.get("secondaryText3")),
            "isrc": isrc,
            "album": {
                "id": album_id,
                "name": album_template.get("headerText", {}).get("text"),
                "url": (((template_data.get("seoHead") or {}).get("link") or [{}])[0]).get("href"),
            },
            "artist": {
                "id": artist_id,
                "name": template.get("headerPrimaryText"),
                "url": f"{BASE_URL}{header_link}" if header_link else None,
            },
        }

    def get_album(self, album_id: str) -> dict | None:
        """Get full album metadata + tracklist by ID."""
        try:
            data = self._post(
                ENDPOINTS["album_info"],
                {"id": album_id, "userHash": self._user_hash()},
                page_url=f"{BASE_URL}/albums/{album_id}",
            )
        except Exception as e:
            console.log(f"[red]Amazon Music: album lookup failed for '{album_id}': {e}[/red]")
            return None

        methods = data.get("methods") or []
        if not methods or "template" not in methods[0]:
            logger.info(f"Amazon Music: album '{album_id}' not found or service error")
            return None

        album = methods[0]["template"]
        if album.get("interface", "").find("DialogTemplate") != -1 and album.get("header") == "Service error":
            logger.info(f"Amazon Music: album '{album_id}' returned a service error")
            return None

        header_link = (album.get("headerPrimaryTextLink") or {}).get("deeplink")
        artist_id = (
            header_link.split("/artists/")[1].split("/")[0] if header_link and "/artists/" in header_link else None
        )

        songs = []
        widgets = album.get("widgets") or []
        if widgets and widgets[0].get("items"):
            for item in widgets[0]["items"]:
                track_link = (item.get("primaryTextLink") or {}).get("deeplink")
                track_id = track_link.split("/tracks/")[-1] if track_link else None

                item_artist_link = (item.get("secondaryText2Link") or {}).get("deeplink")
                item_artist_id = (
                    item_artist_link.split("/artists/")[1].split("/")[0]
                    if item_artist_link and "/artists/" in item_artist_link
                    else artist_id
                )
                artist_url = (
                    f"{BASE_URL}{item_artist_link}"
                    if item_artist_link
                    else (f"{BASE_URL}{header_link}" if header_link else None)
                )

                songs.append(
                    {
                        "id": track_id,
                        "name": item.get("primaryText"),
                        "url": f"{BASE_URL}/tracks/{track_id}" if track_id else None,
                        "image": album.get("headerImage"),
                        "duration": _duration_to_seconds(item.get("secondaryText3")),
                        "isrc": None,
                        "album": {
                            "id": album_id,
                            "name": (album.get("headerText") or {}).get("text"),
                            "url": f"{BASE_URL}/albums/{album_id}",
                        },
                        "artist": {
                            "id": item_artist_id,
                            "name": item.get("secondaryText2") or album.get("headerPrimaryText"),
                            "url": artist_url,
                        },
                    }
                )

        header_tertiary = album.get("headerTertiaryText") or ""

        return {
            "id": album_id,
            "name": (album.get("headerText") or {}).get("text"),
            "url": f"{BASE_URL}/albums/{album_id}",
            "image": album.get("headerImage"),
            "total_songs": _extract_songs_count(header_tertiary),
            "total_duration": _extract_duration_from_text(header_tertiary),
            "release_date": _extract_release_date(header_tertiary),
            "artist": {
                "id": artist_id,
                "name": album.get("headerPrimaryText"),
                "url": f"{BASE_URL}{header_link}" if header_link else None,
            },
            "songs": songs,
        }

    def get_artist(self, artist_id: str) -> dict | None:
        """Get artist info + top songs (album/duration lookups are skipped for speed)."""
        try:
            data = self._post(
                ENDPOINTS["artist_info"],
                {"id": artist_id, "userHash": self._user_hash()},
                page_url=f"{BASE_URL}/artists/{artist_id}",
            )
        except Exception as e:
            console.log(f"[red]Amazon Music: artist lookup failed for '{artist_id}': {e}[/red]")
            return None

        methods = data.get("methods") or []
        if not methods or "template" not in methods[0]:
            logger.info(f"Amazon Music: artist '{artist_id}' not found or service error")
            return None

        artist = methods[0]["template"]
        widgets = artist.get("widgets") or []
        top_songs_widget = next((w for w in widgets if "top songs" in (w.get("header") or "").lower()), None)

        top_songs = []
        for item in (top_songs_widget or {}).get("items") or []:
            storage_key = ((item.get("iconButton") or {}).get("observer") or {}).get("storageKey")
            if not storage_key or ":" not in storage_key:
                continue
            album_id, track_id = storage_key.split(":", 1)

            title = (item.get("primaryText") or {}).get("text") or ""
            title = re.sub(r"^\d+\.\s*", "", title) or None

            context_options = (item.get("contextMenu") or {}).get("options") or [{}, {}]
            album_template = ((context_options[0].get("onItemSelected") or [{}, {}])[1] or {}).get("template") or {}

            top_songs.append(
                {
                    "id": track_id,
                    "title": title,
                    "url": f"{BASE_URL}/tracks/{track_id}",
                    "image": _clean_image_url(item.get("image")),
                    "isrc": None,
                    "album": {
                        "id": album_id,
                        "name": album_template.get("headerText", {}).get("text"),
                        "url": f"{BASE_URL}/albums/{album_id}",
                    },
                    "artist": {
                        "id": artist_id,
                        "name": item.get("secondaryText"),
                        "url": None,
                    },
                }
            )

        return {
            "id": artist_id,
            "name": (artist.get("headerText") or {}).get("text"),
            "url": f"{BASE_URL}/artists/{artist_id}",
            "image": _clean_image_url(artist.get("backgroundImage")),
            "top_songs": top_songs,
        }


# Instance
amazon_music = AmazonMusicClient()
