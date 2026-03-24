# 29.12.25

import time
import os
import logging
import json
import base64
from typing import Tuple, List, Dict, Optional

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client_curl, get_userAgent


logger = logging.getLogger(__name__)
PUBLIC_TOKEN = "bm9haWhkZXZtXzZpeWcwYThsMHE6"
BASE_URL = "https://www.crunchyroll.com"
API_BETA_BASE_URL = "https://beta-api.crunchyroll.com"
PLAY_SERVICE_URL = "https://cr-play-service.prd.crunchyrollsvc.com"


class CrunchyrollClient:
    def __init__(self, locale: str = "it-IT", **kwargs) -> None:
        self.device_id = config_manager.login.get('crunchyroll', 'device_id')
        self.etp_rt = config_manager.login.get('crunchyroll', 'etp_rt')
        self.locale = locale

        self.web_base_url = BASE_URL
        self.api_base_url = self._resolve_api_base_url()
        self.play_service_url = PLAY_SERVICE_URL
        self.token_cache_path = self._resolve_token_cache_path()
        self.token_cache_enabled = True
        self.user_agent = None
        
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.account_id: Optional[str] = None
        self.expires_at: float = 0.0

        # Load cached tokens
        cache_data = self._load_token_cache()
        if not self.user_agent:
            cached_ua = cache_data.get("user_agent") if isinstance(cache_data, dict) else None
            self.user_agent = cached_ua if isinstance(cached_ua, str) and cached_ua.strip() else get_userAgent()
        
        self.session = create_client_curl(headers=self._get_headers(), cookies=self._get_cookies())

    @staticmethod
    def _resolve_api_base_url() -> str:
        """Determine the correct API base URL - defaults to beta API."""
        return API_BETA_BASE_URL

    @staticmethod
    def _resolve_token_cache_path() -> str:
        """Resolve absolute path for token cache file - always enabled."""
        base_dir = os.getcwd()
        path = os.path.join(base_dir, ".cache", "crunchyroll_token.json")
        return path

    @staticmethod
    def _jwt_exp(token: Optional[str]) -> Optional[int]:
        """Extract expiration timestamp from JWT token payload."""
        if not isinstance(token, str) or token.count(".") < 2:
            return None
        
        try:
            payload_b64 = token.split(".", 2)[1]
            padding = "=" * (-len(payload_b64) % 4)
            payload = base64.urlsafe_b64decode(payload_b64 + padding).decode("utf-8", errors="replace")
            obj = json.loads(payload)
            exp = obj.get("exp")

            if isinstance(exp, int):
                return exp
            if isinstance(exp, str) and exp.isdigit():
                return int(exp)
            
        except Exception:
            pass
        return None

    def _set_expires_at(self, *, expires_in: Optional[int] = None) -> None:
        """Set token expiration time from JWT or expires_in value."""
        exp = self._jwt_exp(self.access_token)
        if isinstance(exp, int) and exp > 0:
            self.expires_at = float(exp - 60)
            return
        
        if expires_in is None:
            self.expires_at = 0.0
            return
        
        self.expires_at = time.time() + max(0, int(expires_in) - 60)

    def _load_token_cache(self) -> Dict:
        """Load cached authentication tokens from file if available."""
        if not self.token_cache_path:
            return {}
        
        try:
            if not os.path.exists(self.token_cache_path):
                return {}
            
            with open(self.token_cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            if not isinstance(data, dict):
                return {}

            cached_device_id = data.get("device_id")
            if self.device_id and isinstance(cached_device_id, str) and cached_device_id != self.device_id:
                return {}

            access = data.get("access_token")
            refresh = data.get("refresh_token")
            if isinstance(access, str) and access:
                self.access_token = access
            if isinstance(refresh, str) and refresh:
                self.refresh_token = refresh

            account_id = data.get("account_id")
            if isinstance(account_id, str) and account_id:
                self.account_id = account_id

            try:
                self.expires_at = float(data.get("expires_at") or 0.0)
            except Exception:
                self.expires_at = 0.0

            return data
        except Exception as e:
            logger.error(f"Token cache load failed: {e}")
            return {}

    def _save_token_cache(self) -> None:
        """Save current authentication tokens to cache file."""
        if not self.token_cache_path:
            return
        
        try:
            cache_dir = os.path.dirname(self.token_cache_path)
            if cache_dir:
                os.makedirs(cache_dir, exist_ok=True)
            
            payload = {
                "device_id": self.device_id,
                "account_id": self.account_id,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_at": self.expires_at,
                "user_agent": self.user_agent,
                "api_base_url": self.api_base_url,
                "saved_at": time.time(),
            }

            with open(self.token_cache_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)

        except Exception as e:
            logger.error(f"Token cache save failed: {e}")

    def _get_headers(self) -> Dict:
        """Generate HTTP headers for API requests including authorization."""
        headers = {
            'user-agent': self.user_agent or get_userAgent(),
            'accept': 'application/json, text/plain, */*',
            'origin': self.web_base_url,
            'referer': f'{self.web_base_url}/',
            'accept-language': f'{self.locale.replace("_", "-")},en-US;q=0.8,en;q=0.7',
        }
        if self.access_token:
            headers['authorization'] = f'Bearer {self.access_token}'
            
        return headers

    def _get_cookies(self) -> Dict:
        """Generate cookies for API requests including device_id and etp_rt."""
        cookies = {'device_id': self.device_id}
        if self.etp_rt:
            cookies['etp_rt'] = self.etp_rt
        return cookies

    def start(self) -> bool:
        """Authenticate using etp_rt cookie - single attempt."""
        headers = self._get_headers()
        headers['authorization'] = f'Basic {PUBLIC_TOKEN}'
        headers['content-type'] = 'application/x-www-form-urlencoded'
        
        data = {
            'device_id': self.device_id,
            'device_type': 'Chrome on Windows',
            'grant_type': 'etp_rt_cookie',
        }

        response = self.session.post(
            f'{self.api_base_url}/auth/v1/token',
            cookies=self._get_cookies(),
            headers=headers,
            data=data
        )
        
        if response.status_code != 200:
            logger.error(f"Authentication failed: {response.status_code}")
            return False
        
        result = response.json()
        
        self.access_token = result.get('access_token')
        self.refresh_token = result.get('refresh_token')
        self.account_id = result.get('account_id')
        
        expires_in = int(result.get('expires_in', 3600) or 3600)
        self._set_expires_at(expires_in=expires_in)
        self._save_token_cache()
        
        return True

    def _refresh(self) -> None:
        """Refresh access token - single attempt."""
        if not self.refresh_token:
            raise RuntimeError("refresh_token missing")
        
        headers = self._get_headers()
        headers['authorization'] = f'Basic {PUBLIC_TOKEN}'
        headers['content-type'] = 'application/x-www-form-urlencoded'
        
        data = {
            'grant_type': 'refresh_token',
            'refresh_token': self.refresh_token,
            'device_type': 'Chrome on Windows',
        }
        if self.device_id:
            data['device_id'] = self.device_id

        response = self.session.post(
            f'{self.api_base_url}/auth/v1/token',
            cookies=self._get_cookies(),
            headers=headers,
            data=data
        )
        
        if response.status_code != 200:
            logger.error(f"Token refresh failed: {response.status_code}")
            raise RuntimeError(f"Token refresh failed: {response.status_code}")
        
        result = response.json()
        self.access_token = result.get('access_token')
        self.refresh_token = result.get('refresh_token') or self.refresh_token
        
        expires_in = int(result.get('expires_in', 3600) or 3600)
        self._set_expires_at(expiresIn=expires_in)
        self._save_token_cache()

    def _ensure_token(self) -> None:
        """Ensure valid access token - no retries."""
        if not self.access_token:
            if not self.start():
                raise RuntimeError("Authentication failed")
            return
        
        # Refresh if expiring soon
        if time.time() >= (self.expires_at - 30):
            try:
                self._refresh()
            except Exception:
                if not self.start():
                    raise RuntimeError("Re-authentication failed")

    def request(self, method: str, url: str, **kwargs):
        """Single request attempt - no retries."""
        self._ensure_token()
        
        headers = kwargs.pop('headers', {}) or {}
        merged_headers = {**self._get_headers(), **headers}
        kwargs['headers'] = merged_headers
        kwargs.setdefault('cookies', self._get_cookies())
        kwargs.setdefault('timeout', config_manager.config.get_int('REQUESTS', 'timeout', default=30))
        
        response = self.session.request(method, url, **kwargs)
        
        # Only handle 401 once
        if response.status_code == 401:
            try:
                self._refresh()
            except Exception:
                self.start()
            kwargs['headers'] = {**self._get_headers(), **headers}
            response = self.session.request(method, url, **kwargs)
        
        return response

    def refresh(self) -> None:
        """Public refresh method."""
        self._refresh()

    def get_streams(self, media_id: str) -> Dict:
        """Get playback data - single attempt only."""
        pb_url = f'{self.play_service_url}/v3/{media_id}/web/chrome/play'

        response = self.request('GET', pb_url, params={'locale': self.locale})

        if response.status_code == 403:
            raise Exception("Playback Rejected: Subscription required")

        if response.status_code == 404:
            raise Exception(f"Playback endpoint not found: {pb_url}")

        if response.status_code == 420:
            try:
                payload = response.json()
                error_code = payload.get("error")
                active_streams = payload.get("activeStreams", [])

                if error_code in ("TOO_MANY_ACTIVE_STREAMS", "TOO_MANY_CONCURRENT_STREAMS") and active_streams:
                    logger.error(f"TOO_MANY_ACTIVE_STREAMS: cleaning up {len(active_streams)} streams")

                    for s in active_streams:
                        if isinstance(s, dict):
                            content_id = s.get("contentId")
                            token = s.get("token")
                            if content_id and token:
                                self.deauth_video(content_id, token)
            except Exception:
                pass
            
            raise Exception("TOO_MANY_ACTIVE_STREAMS. Wait and try again.")

        if response.status_code != 200:
            raise Exception(f"Playback failed: {response.status_code}")

        data = response.json()

        if data.get('error') == 'Playback is Rejected':
            raise Exception("Playback Rejected: Premium required")

        return data

    def deauth_video(self, media_id: str, token: str) -> bool:
        """Mark playback token as inactive to free stream slot."""
        if not media_id or not token:
            return False

        try:
            response = self.session.patch(
                f'{PLAY_SERVICE_URL}/v1/token/{media_id}/{token}/inactive',
                cookies=self._get_cookies(),
                headers=self._get_headers(),
            )
            return response.status_code in (200, 204)
        
        except Exception as e:
            logger.error(f"Failed to deauth stream token: {e}")
            return False

    def get_available_versions(self, url_id: str) -> List[Dict]:
        """
        Return the list of all available audio versions for an episode

        Returns:
            List of dicts with: guid, audio_locale
        """
        try:
            playback_data = self.get_streams(url_id)
            versions_list = playback_data.get('versions') or []

            # Deauth immediately to free the slot
            token = playback_data.get("token") or _find_token_recursive(playback_data)
            if token:
                self.deauth_video(url_id, token)

            result = []
            seen = set()
            for v in versions_list:
                guid = v.get('guid') or v.get('id')
                locale = v.get('audio_locale')
                if guid and locale and locale not in seen:
                    seen.add(locale)
                    result.append({"guid": guid, "audio_locale": locale})

            return result

        except Exception as e:
            logger.error(f"get_available_versions failed for {url_id}: {e}")
            return []

    def get_versions_by_locales(self, url_id: str, locales: List[str]) -> List[Dict]:
        """
        Get playback sessions for specified audio locales.
        
        Parameters:
            url_id: The media ID (can be main episode or season ID)
            locales: List of BCP47 locales (e.g., ["it-IT", "en-US"])
        """
        if not locales:
            logger.error("get_versions_by_locales called with empty locales list")
            return []
        
        versions = []
        
        try:
            # Get versions list for the main content
            playback_data = self.get_streams(url_id)
            
            # Extract versions if available
            versions_list = playback_data.get('versions')
            logger.info(f"Found {len(versions_list) if isinstance(versions_list, list) else 0} versions for url_id: {url_id}")
            
            if not versions_list:
                logger.error(f"No versions found for url_id: {url_id}")
                return []

            # Filter and fetch each version matching the requested locales
            for version in versions_list:
                if not isinstance(version, dict):
                    continue
                
                version_guid = version.get('guid') or version.get('id')
                audio_locale = version.get('audio_locale') or version.get('audio', {}).get('locale')
                logger.info(f"Checking version: guid={version_guid}, audio_locale={audio_locale}")

                if not version_guid or not audio_locale:
                    logger.info("Skipping version due to missing guid or audio_locale")
                    continue
                
                # Check if this version's locale matches requested locales
                if audio_locale not in locales:
                    logger.info(f"Skipping version due to locale mismatch: {audio_locale} not in {locales}")
                    continue
                
                try:
                    # Get playback data for this specific version
                    logger.info(f"Fetching playback for version {version_guid} with locale {audio_locale}...")
                    version_playback = self.get_streams(version_guid)
                    
                    mpd_url = version_playback.get('url')
                    token = version_playback.get("token") or _find_token_recursive(version_playback)
                    logger.info(f"Version {version_guid} - mpd_url: {mpd_url}, token: {'found' if token else 'not found'}")
                    
                    if mpd_url:
                        versions.append({
                            "guid": version_guid,
                            "audio_locale": audio_locale,
                            "mpd_url": mpd_url,
                            "token": token,
                            "mpd_headers": self._get_headers()
                        })
                    
                    # Deauth immediately to free streaming slot
                    if token:
                        self.deauth_video(version_guid, token)
                
                except Exception as e:
                    logger.error(f"Failed to fetch streams for version {version_guid}: {e}")
                    continue
            
            # Deauth the main url_id as well
            main_token = playback_data.get("token") or _find_token_recursive(playback_data)
            if main_token:
                self.deauth_video(url_id, main_token)
        
        except Exception as e:
            logger.error(f"Error in get_versions_by_locales: {e}")
        
        return versions
        
def _find_token_recursive(obj) -> Optional[str]:
    """Recursively search for 'token' field in playback response."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() == "token" and isinstance(v, str) and len(v) > 10:
                return v
            token = _find_token_recursive(v)
            if token:
                return token
    elif isinstance(obj, list):
        for el in obj:
            token = _find_token_recursive(el)
            if token:
                return token
    return None


def _extract_subtitles(data: Dict) -> List[Dict]:
    """Extract all subtitles from playback data."""
    subtitles = []
    
    # Process regular subtitles
    subs_obj = data.get('subtitles') or {}
    for lang, info in subs_obj.items():
        if not info or not info.get('url'):
            continue

        subtitles.append({
            'language': lang,
            'url': info['url'],
            'format': info.get('format') or 'ass',
            'type': info.get('type'),
            'closed_caption': bool(info.get('closed_caption')),
            'label': info.get('display') or info.get('title') or info.get('language')
        })

    # Process captions/closed captions
    captions_obj = data.get('captions') or data.get('closed_captions') or {}
    for lang, info in captions_obj.items():
        if not info or not info.get('url'):
            continue

        subtitles.append({
            'language': lang,
            'url': info['url'],
            'format': info.get('format') or 'vtt',
            'type': info.get('type') or 'captions',
            'closed_caption': True if info.get('closed_caption') is None else bool(info.get('closed_caption')),
            'label': info.get('display') or info.get('title') or info.get('language')
        })

    return subtitles


def get_playback_session(client: CrunchyrollClient, url_id: str, main_guid: Optional[str] = None) -> Tuple[str, Dict, List[Dict], Optional[str], Optional[str]]:
    """
    Get playback session with SINGLE API call.
    If main_guid is provided, fetch subtitles from main track for complete subs.
    
    Returns:
        - mpd_url: str
        - headers: Dict
        - subtitles: List[Dict]
        - token: Optional[str]
        - audio_locale: Optional[str]
    """
    playback_data = client.get_streams(url_id)

    # Extract relevant data
    mpd_url = playback_data.get('url')
    audio_locale = playback_data.get('audio_locale') or playback_data.get('audio', {}).get('locale')
    token = playback_data.get("token") or _find_token_recursive(playback_data)
    
    # Get subtitles: prefer main_guid for complete subtitles if available
    if main_guid and main_guid != url_id:
        try:
            # Fetch subtitles from main track
            main_playback_data = client.get_streams(main_guid)
            subtitles = _extract_subtitles(main_playback_data)
            
            # Deauth main track token
            main_token = main_playback_data.get("token") or _find_token_recursive(main_playback_data)
            if main_token:
                client.deauth_video(main_guid, main_token)

        except Exception as e:
            logger.error(f"Failed to fetch subtitles from main track: {e}")
            subtitles = _extract_subtitles(playback_data)

    else:
        subtitles = _extract_subtitles(playback_data)
    
    # Immediately deauth to free stream slot (non-blocking)
    if token:
        try:
            client.deauth_video(url_id, token)
        except Exception as e:
            logger.error(f"Deauth during playback failed: {e}")
    
    headers = client._get_headers()
    return mpd_url, headers, subtitles, token, audio_locale