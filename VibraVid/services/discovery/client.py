# 22.12.25

import uuid
from typing import Dict, Optional

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client


_discovery_client = None
cookie_st = config_manager.login.get("discovery", "st")


class DiscoveryPlus:
    def __init__(self, cookies: Optional[Dict[str, str]] = None):
        """
        Initialize Discovery Plus client with automatic authentication.
        """
        self.device_id = str(uuid.uuid1())
        self.client_id = "b6746ddc-7bc7-471f-a16c-f6aaf0c34d26"

        # Base headers for Android TV client (will be updated after auth)
        self.base_headers = {
            'accept': '*/*',
            'accept-language': 'it,it-IT;q=0.9,en;q=0.8',
            'user-agent': 'androidtv dplus/20.8.1.2 (android/9; en-US; SHIELD Android TV-NVIDIA; Build/1)',
            'x-disco-client': 'ANDROIDTV:9:dplus:20.8.1.2',
            'x-disco-params': 'realm=bolt,bid=dplus,features=ar',
            'x-device-info': f'dplus/20.8.1.2 (NVIDIA/SHIELD Android TV; android/9-mdarcy; {self.device_id}/{self.client_id})',
        }
        self.cookies = cookies or {}
        self.access_token = None
        self.base_url = None
        self.headers = None

        self._authenticate()

    def _authenticate(self):
        """
        Obtain an access token and market‑specific base URL.
        """
        if self.cookies.get("st"):
            print("Login with ST token...")
            self.access_token = self.cookies["st"]
        else:
            print("Generating anonymous ST token...")
            token_url = "https://default.any-any.prd.api.discoveryplus.com/token"
            params = {"realm": "bolt", "deviceId": self.device_id}
            response = create_client(headers=self.base_headers, cookies=self.cookies).get(token_url, params=params)
            response.raise_for_status()
            data = response.json()
            self.access_token = data['data']['attributes']['token']

        # Step 2: Bootstrap to get market‑specific base_url
        bootstrap_url = "https://default.any-any.prd.api.discoveryplus.com/session-context/headwaiter/v1/bootstrap"
        headers = self.base_headers.copy()
        headers['Authorization'] = f'Bearer {self.access_token}'
        response = create_client(headers=headers, cookies=self.cookies).post(bootstrap_url)
        response.raise_for_status()
        config = response.json()
        tenant = config['routing']['tenant']
        market = config['routing']['homeMarket']
        self.base_url = f"https://default.{tenant}-{market}.prd.api.discoveryplus.com"

        # Final headers for all subsequent requests
        self.headers = self.base_headers.copy()
        self.headers['Authorization'] = f'Bearer {self.access_token}'

    def get_playback_info(self, edit_id: str) -> Dict[str, str]:
        """
        Get manifest and license URLs for a given edit_id.
        """
        payload = {
            "appBundle": "com.wbd.stream",
            "applicationSessionId": self.device_id,
            "capabilities": {
                "codecs": {
                    "audio": {
                        "decoders": [
                            {"codec": "aac", "profiles": ["lc", "he", "hev2", "xhe"]},
                            {"codec": "eac3", "profiles": ["atmos"]},
                        ]
                    },
                    "video": {
                        "decoders": [
                            {
                                "codec": "h264",
                                "levelConstraints": {
                                    "framerate": {"max": 60, "min": 0},
                                    "height": {"max": 2160, "min": 48},
                                    "width": {"max": 3840, "min": 48},
                                },
                                "maxLevel": "5.2",
                                "profiles": ["baseline", "main", "high"],
                            },
                            {
                                "codec": "h265",
                                "levelConstraints": {
                                    "framerate": {"max": 60, "min": 0},
                                    "height": {"max": 2160, "min": 144},
                                    "width": {"max": 3840, "min": 144},
                                },
                                "maxLevel": "5.1",
                                "profiles": ["main10", "main"],
                            },
                        ],
                        "hdrFormats": ["hdr10", "hdr10plus", "dolbyvision", "dolbyvision5", "dolbyvision8", "hlg"],
                    },
                },
                "contentProtection": {
                    'contentDecryptionModules': [
                        {'drmKeySystem': 'playready', 'maxSecurityLevel': 'SL3000'}
                    ]
                },
                "manifests": {"formats": {"dash": {}}},
            },
            "consumptionType": "streaming",
            "deviceInfo": {
                "player": {
                    "mediaEngine": {"name": "", "version": ""},
                    "playerView": {"height": 2160, "width": 3840},
                    "sdk": {"name": "", "version": ""},
                }
            },
            "editId": edit_id,
            "firstPlay": False,
            "gdpr": False,
            "playbackSessionId": str(uuid.uuid4()),
            "userPreferences": {},
        }

        url = f"{self.base_url}/playback-orchestrator/any/playback-orchestrator/v1/playbackInfo"
        response = create_client(headers=self.headers, cookies=self.cookies).post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        
        # Extract manifest and license
        manifest = (
            data.get('fallback', {}).get('manifest', {}).get('url', '').replace('_fallback', '')
            or data.get('manifest', {}).get('url')
        )
        license_url = (
            data.get('fallback', {}).get('drm', {}).get('schemes', {}).get('playready', {}).get('licenseUrl')
            or data.get('drm', {}).get('schemes', {}).get('playready', {}).get('licenseUrl')
        )

        return {
            'manifest': manifest,
            'license': license_url,
            'type': 'dash',
            'license_headers': {}
        }


def get_client():
    """Get or create DiscoveryPlus client instance."""
    global _discovery_client
    if _discovery_client is None:
        cookies = {'st': cookie_st} if cookie_st else None
        _discovery_client = DiscoveryPlus(cookies)
    return _discovery_client