# 22.12.25

import uuid
from typing import Dict, Optional

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client_curl


_discovery_client = None
cookie_st = config_manager.login.get("discoveryeu", "st")


class DiscoveryPlus:
    def __init__(self, cookies: Optional[Dict[str, str]] = None):
        """
        Initialize Discovery Plus client with automatic authentication fallback
        
        Args:
            cookies: Optional dictionary containing 'st' token. If None or empty, uses anonymous auth.
        """
        self.device_id = str(uuid.uuid1())
        self.client_id = "b6746ddc-7bc7-471f-a16c-f6aaf0c34d26"
        self.base_url = "https://default.any-any.prd.api.discoveryplus.com"
        self.access_token = None
        self.bearer_token = None
        self.is_anonymous = False
        
        # Base headers for Android TV client
        self.headers = {
            'accept': '*/*',
            'accept-language': 'it,it-IT;q=0.9,en;q=0.8',
            'user-agent': 'androidtv dplus/20.8.1.2 (android/9; en-US; SHIELD Android TV-NVIDIA; Build/1)',
            'x-disco-client': 'ANDROIDTV:9:dplus:20.8.1.2',
            'x-disco-params': 'realm=bolt,bid=dplus,features=ar',
            'x-device-info': f'dplus/20.8.1.2 (NVIDIA/SHIELD Android TV; android/9-mdarcy; {self.device_id}/{self.client_id})',
        }
        
        # Check if we have valid cookies
        if cookies and cookies.get('st'):
            print("Using authenticated mode with st cookie")
            self.cookies = cookies
            self.is_anonymous = False
            self._authenticate()
        else:
            print("No st cookie found, using anonymous authentication")
            self.cookies = {}
            self.is_anonymous = True
            self._anonymous_authenticate()

    def _anonymous_authenticate(self):
        """Authenticate anonymously and get bearer token"""
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'x-device-info': f'dsc/4.4.1 (desktop/desktop; Windows/NT 10.0; {self.device_id})',
            'x-disco-client': 'WEB:UNKNOWN:dsc:4.4.1'
        }
        params = {
            'deviceId': self.device_id,
            'realm': 'dplay',
            'shortlived': 'true'
        }
        
        try:
            response = create_client_curl(headers=headers).get(
                'https://eu1-prod-direct.discoveryplus.com/token', 
                params=params
            )
            response.raise_for_status()
            self.bearer_token = response.json()['data']['attributes']['token']
            
            # Update headers for anonymous mode
            self.headers['Authorization'] = f'Bearer {self.bearer_token}'
            self.base_url = "https://eu1-prod-direct.discoveryplus.com"
            print(f"Anonymous bearer token obtained: {self.bearer_token[:20]}...")
            
        except Exception as e:
            raise RuntimeError(f"Failed to get anonymous bearer token: {e}")
    
    def _authenticate(self):
        """Authenticate with st cookie and get access token"""
        try:
            url = f"{self.base_url}/token"
            params = {'realm': 'bolt', 'deviceId': self.device_id}
            
            response = create_client_curl(headers=self.headers, cookies=self.cookies).get(url, params=params)
            response.raise_for_status()
            
            data = response.json()
            self.access_token = data['data']['attributes']['token']
            
            # Get routing config
            url = f"{self.base_url}/session-context/headwaiter/v1/bootstrap"
            response = create_client_curl(headers=self.headers, cookies=self.cookies).post(url)
            response.raise_for_status()
            
            config = response.json()
            tenant = config['routing']['tenant']
            market = config['routing']['homeMarket']
            self.base_url = f"https://default.{tenant}-{market}.prd.api.discoveryplus.com"
            print(f"Authenticated with access token for {tenant}-{market}")
            
        except Exception as e:
            print(f"Authenticated mode failed: {e}")
            self.is_anonymous = True
            self.cookies = {}
            self._anonymous_authenticate()
    
    def get_playback_info(self, edit_id: str) -> Dict[str, str]:
        """
        Get manifest and license URLs for playback
        
        Args:
            edit_id: Edit ID of the content (for authenticated) or video ID (for anonymous)
            
        Returns:
            Dictionary with 'manifest' and 'license' URLs
        """
        if self.is_anonymous:
            return self._get_playback_info_anonymous(edit_id)
        else:
            return self._get_playback_info_authenticated(edit_id)
    
    def _get_playback_info_anonymous(self, video_id: str) -> Dict[str, str]:
        """Get playback info using anonymous bearer token"""
        cookies = {'st': self.bearer_token} if self.bearer_token else {}
        
        headers = {
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'x-disco-client': 'WEB:UNKNOWN:dsc:4.4.1',
        }
        
        json_data = {
            'videoId': video_id,
            'wisteriaProperties': {
                'advertiser': {},
                'appBundle': '',
                'device': {
                    'browser': {'name': 'chrome', 'version': '125.0.0.0'},
                    'id': '',
                    'language': 'en-US',
                    'make': '',
                    'model': '',
                    'name': 'chrome',
                    'os': 'Windows',
                    'osVersion': 'NT 10.0',
                    'player': {'name': 'Discovery Player Web', 'version': '3.1.0'},
                    'type': 'desktop',
                },
                'gdpr': 0,
                'platform': 'desktop',
                'product': 'dsc',
                'siteId': 'dsc'
            },
            'deviceInfo': {
                'adBlocker': False,
                'deviceId': '',
                'drmTypes': {
                    'widevine': False,
                    'playready': True,
                    'fairplay': True,
                    'clearkey': True,
                },
                'drmSupported': True
            },
        }
        
        response = create_client_curl().post(
            'https://eu1-prod-direct.discoveryplus.com/playback/v3/videoPlaybackInfo',
            cookies=cookies,
            headers=headers,
            json=json_data
        )
        
        if response.status_code == 403:
            json_response = response.json()
            errors = json_response.get('errors', [])
            if errors and errors[0].get('code') == 'access.denied.missingpackage':
                raise RuntimeError("Content requires a subscription/account to view")
            else:
                raise RuntimeError("Content is geo-restricted")
        
        response.raise_for_status()
        json_response = response.json()
        
        streaming_data = json_response['data']['attributes']['streaming']
        widevine_scheme = streaming_data[0]['protection']['schemes'].get('widevine')
        playready_scheme = streaming_data[0]['protection']['schemes'].get('playready')
        
        return {
            'manifest': streaming_data[0]['url'],
            'license': widevine_scheme['licenseUrl'] if widevine_scheme else (
                playready_scheme['licenseUrl'] if playready_scheme else None
            ),
            'license_token': streaming_data[0]['protection']['drmToken'] if widevine_scheme else None,
            'type': streaming_data[0]['type']
        }
    
    def _get_playback_info_authenticated(self, edit_id: str) -> Dict[str, str]:
        """Get playback info using authenticated access token"""
        url = f"{self.base_url}/playback-orchestrator/any/playback-orchestrator/v1/playbackInfo"
        
        headers = self.headers.copy()
        headers['Authorization'] = f'Bearer {self.access_token}'
        
        payload = {
            'appBundle': 'com.wbd.stream',
            'applicationSessionId': self.device_id,
            'capabilities': {
                'codecs': {
                    'audio': {
                        'decoders': [
                            {'codec': 'aac', 'profiles': ['lc', 'he', 'hev2', 'xhe']},
                            {'codec': 'eac3', 'profiles': ['atmos']},
                        ]
                    },
                    'video': {
                        'decoders': [
                            {
                                'codec': 'h264',
                                'levelConstraints': {
                                    'framerate': {'max': 60, 'min': 0},
                                    'height': {'max': 2160, 'min': 48},
                                    'width': {'max': 3840, 'min': 48},
                                },
                                'maxLevel': '5.2',
                                'profiles': ['baseline', 'main', 'high'],
                            },
                            {
                                'codec': 'h265',
                                'levelConstraints': {
                                    'framerate': {'max': 60, 'min': 0},
                                    'height': {'max': 2160, 'min': 144},
                                    'width': {'max': 3840, 'min': 144},
                                },
                                'maxLevel': '5.1',
                                'profiles': ['main10', 'main'],
                            },
                        ],
                        'hdrFormats': ['hdr10', 'hdr10plus', 'dolbyvision', 'dolbyvision5', 'dolbyvision8', 'hlg'],
                    },
                },
                'contentProtection': {
                    'contentDecryptionModules': [
                        {'drmKeySystem': 'playready', 'maxSecurityLevel': 'SL3000'}
                    ]
                },
                'manifests': {'formats': {'dash': {}}},
            },
            'consumptionType': 'streaming',
            'deviceInfo': {
                'player': {
                    'mediaEngine': {'name': '', 'version': ''},
                    'playerView': {'height': 2160, 'width': 3840},
                    'sdk': {'name': '', 'version': ''},
                }
            },
            'editId': edit_id,
            'firstPlay': False,
            'gdpr': False,
            'playbackSessionId': str(uuid.uuid4()),
            'userPreferences': {},
        }
        
        response = create_client_curl(headers=headers, cookies=self.cookies).post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        
        # Get manifest URL
        manifest = (
            data.get('fallback', {}).get('manifest', {}).get('url', '').replace('_fallback', '')
            or data.get('manifest', {}).get('url')
        )
        
        # Get license URL
        license_url = (
            data.get('fallback', {}).get('drm', {}).get('schemes', {}).get('playready', {}).get('licenseUrl')
            or data.get('drm', {}).get('schemes', {}).get('playready', {}).get('licenseUrl')
        )
        
        return {
            'manifest': manifest,
            'license': license_url,
            'type': 'dash'
        }
    
    def generate_license_headers(self, license_token: Optional[str] = None) -> Dict[str, str]:
        """
        Generate headers for license requests
        
        Args:
            license_token: Optional DRM token for anonymous mode
        """
        if self.is_anonymous and license_token:
            return {
                'preauthorization': license_token,
                'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            }
        else:
            return {
                'user-agent': self.headers['user-agent'],
            }


def get_client():
    """Get or create DiscoveryPlus client instance with automatic auth detection"""
    global _discovery_client
    if _discovery_client is None:
        cookies = {'st': cookie_st} if cookie_st else None
        _discovery_client = DiscoveryPlus(cookies)
    return _discovery_client