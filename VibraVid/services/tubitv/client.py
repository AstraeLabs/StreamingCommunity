# 16.12.25

import os
import json
import uuid
from typing import Tuple, Optional

from VibraVid.utils import config_manager, os_manager
from VibraVid.utils.http_client import create_client, get_userAgent, get_headers


tubi_email = config_manager.login.get('tubi', 'email')
tubi_password = config_manager.login.get('tubi', 'password')

_cached_token = None
CACHE_FILE = os.path.join(os.getcwd(), ".cache", "tubi_token.json")


def generate_device_id():
    """Generate a unique device ID"""
    return str(uuid.uuid4())


def get_bearer_token():
    """Get the Bearer token required for Tubi TV authentication"""
    global _cached_token
    
    # Try memory cache
    if _cached_token:
        return _cached_token
        
    # Try disk cache
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                data = json.load(f)
                _cached_token = data.get('access_token')
                if _cached_token:
                    return _cached_token
        except Exception:
            pass

    if not tubi_email or not tubi_password:
        raise Exception("Email or Password not set in configuration.")

    json_data = {
        'type': 'email',
        'platform': 'web',
        'device_id': generate_device_id(),
        'credentials': {
            'email': str(tubi_email).strip(),
            'password': str(tubi_password).strip()
        },
    }

    print("Logging in to Tubi TV...")
    response = create_client(headers=get_headers()).post(
        'https://account.production-public.tubi.io/user/login',
        json=json_data
    )
    
    if response.status_code == 503:
        raise Exception("Service Unavailable: Set VPN to America.")
    
    login_data = response.json()
    _cached_token = login_data['access_token']
    
    # Save to disk cache
    try:
        cache_dir = os.path.dirname(CACHE_FILE)
        if not os.path.exists(cache_dir):
            os.makedirs(cache_dir, exist_ok=True)
        with open(CACHE_FILE, 'w') as f:
            json.dump(login_data, f)
    except Exception:
        pass

    return _cached_token


def get_playback_url(content_id: str, bearer_token: str) -> Tuple[str, Optional[str], Optional[dict]]:
    """
    Get the playback URL (HLS) and license URL for a given content ID.

    Parameters:
        - content_id (str): ID of the video content
        - bearer_token (str): Bearer token for authentication

    Returns:
        - Tuple[str, Optional[str], Optional[dict]]: (master_playlist_url, license_url, headers)
    """
    headers = {
        'authorization': f"Bearer {bearer_token}",
        'user-agent': get_userAgent(),
    }

    params = {
        'content_id': content_id,
        'limit_resolutions[]': [
            'h264_1080p',
            'h265_1080p',
        ],
        'video_resources[]': [
            'hlsv6_widevine_nonclearlead',
            'hlsv6_playready_psshv0',
            'hlsv6',
        ]
    }

    response = create_client(headers=headers).get(
        'https://content-cdn.production-public.tubi.io/api/v2/content',
        params=params
    )
    
    json_data = response.json()
    master_playlist_url = json_data['video_resources'][0]['manifest']['url']
    
    license_url = None
    if 'license_server' in json_data['video_resources'][0]:
        license_url = json_data['video_resources'][0]['license_server']['url']
    
    return master_playlist_url, license_url, headers