# 22.12.25

import uuid
import random

from ua_generator import generate

from VibraVid.utils.http_client import create_client_curl


_discovery_api = None


class DiscoveryAPI:
    def __init__(self):
        self.device_id = str(uuid.uuid4())
        self.device_info = self._generate_device_info()
        self.user_agent = self.device_info['user_agent']
        self.bearer_token = None
        self._initialize()
        
    def _generate_device_info(self):
        ua = generate(device='desktop', browser=random.choice(['chrome', 'firefox', 'edge', 'safari']))
        
        browser_name_map = {
            'chrome': 'chrome',
            'firefox': 'firefox',
            'edge': 'edge',
            'safari': 'safari'
        }
        
        browser_name = browser_name_map.get(ua.browser.lower(), 'chrome')
        browser_version = ua.ch.browser_full_version if hasattr(ua.ch, 'browser_full_version') else '125.0.0.0'
        os_version = ua.ch.platform_version if hasattr(ua.ch, 'platform_version') else 'NT 10.0'
    
        device_info = {
            'user_agent': ua.text,
            'device': {
                'browser': {
                    'name': browser_name,
                    'version': browser_version,
                },
                'id': '',
                'language': random.choice(['en', 'en-US', 'en-GB']),
                'make': '',
                'model': '',
                'name': browser_name,
                'os': ua.ch.platform if hasattr(ua.ch, 'platform') else 'Windows',
                'osVersion': os_version,
                'player': {
                    'name': 'Discovery Player Web',
                    'version': '3.1.0',
                },
                'type': 'desktop',
            }
        }
        
        return device_info
    
    def _initialize(self):
        headers = {
            'user-agent': self.user_agent,
            'x-device-info': f'dsc/4.4.1 (desktop/desktop; Windows/NT 10.0; {self.device_id})',
            'x-disco-client': 'WEB:UNKNOWN:dsc:4.4.1'
        }
        params = {
            'deviceId': self.device_id,
            'realm': 'go',
            'shortlived': 'true'
        }
        
        try:
            response = create_client_curl(headers=headers).get('https://us1-prod-direct.go.discovery.com/token', params=params)
            response.raise_for_status()
            self.bearer_token = response.json()['data']['attributes']['token']
            
        except Exception as e:
            raise RuntimeError(f"Failed to get bearer token: {e}")
    
    def get_request_headers(self):
        return {
            'accept': '*/*',
            'user-agent': self.user_agent,
            'x-disco-client': 'WEB:UNKNOWN:dsc:4.4.1',
            'x-disco-params': 'realm=go,siteLookupKey=dsc,bid=dsc,hn=go.discovery.com,hth=us,features=ar',
        }
    
    def get_cookies(self):
        return {'st': self.bearer_token}


def get_api():
    """Get or create Discovery API instance"""
    global _discovery_api
    if _discovery_api is None:
        _discovery_api = DiscoveryAPI()
    return _discovery_api


def get_playback_info(video_id):
    """
    Get playback information for a video including MPD URL and license token
    
    Args:
        video_id (str): The video ID
    """
    api = get_api()
    
    cookies = api.get_cookies()
    headers = {
        'user-agent': api.user_agent,
        'x-disco-client': 'WEB:UNKNOWN:dsc:4.4.1',
    }
    
    json_data = {
        'videoId': video_id,
        'wisteriaProperties': {
            'advertiser': {},
            'appBundle': '',
            'device': api.device_info['device'],
            'gdpr': 0,
            'platform': 'desktop',
            'product': 'dsc',
            'siteId': 'dsc'
        },
        'deviceInfo': {
            'adBlocker': False,
            'deviceId': '',
            'drmTypes': {
                'widevine': True,
                'playready': True,
                'fairplay': False,
                'clearkey': False,
            },
            'drmSupported': True
        },
    }
    
    response = create_client_curl().post('https://us1-prod-direct.go.discovery.com/playback/v3/videoPlaybackInfo', cookies=cookies, headers=headers, json=json_data)
    
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
    #playready_scheme = streaming_data[0]['protection']['schemes'].get('playready')
    fairplay_scheme = streaming_data[0]['protection']['schemes'].get('fairplay')

    if fairplay_scheme:
        raise RuntimeError("FairPlay DRM is not supported")
    
    return {
        'mpd_url': streaming_data[0]['url'],
        'license_url': widevine_scheme['licenseUrl'] if widevine_scheme else None,
        'license_token': streaming_data[0]['protection']['drmToken'] if widevine_scheme else None,
        'type': streaming_data[0]['type']
    }


def generate_license_headers(license_token):
    """
    Generate headers for license requests
    
    Args:
        license_token (str): The DRM token from playback info
    """
    return {
        'preauthorization': license_token,
        'user-agent': get_api().user_agent,
    }