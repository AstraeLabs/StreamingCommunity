# 29.01.26

from rich.console import Console
from pyplayready.cdm import Cdm
from pyplayready.device import Device
from pyplayready.remote.remotecdm import RemoteCdm
from pyplayready.system.pssh import PSSH

from VibraVid.setup import get_info_prd
from VibraVid.utils.http_client import create_client_curl
from VibraVid.source.utils.object import KeysManager


console = Console()


def get_playready_keys(pssh_list: list[dict], license_url: str, cdm_device_path: str = None, cdm_remote_api: list[str] = None, headers: dict = None, key: str = None):
    """
    Extract PlayReady CONTENT keys (KID/KEY) from a license.

    Args:
        - pssh_list (list[dict]): List of dicts {'pssh': ..., 'kid': ..., 'type': ...}
        - license_url (str): PlayReady license URL.
        - cdm_device_path (str): Path to local CDM file (device.prd). Optional if using remote.
        - cdm_remote_api (list[str]): Remote CDM API config. Optional if using local device.
        - headers (dict): Optional HTTP headers for the license request.
        - key (str): Optional raw license data to bypass HTTP request.

    Returns:
        list: List of strings "KID:KEY" (only CONTENT keys) or None if error.
    """
    # Handle pre-existing key
    if key:
        k_split = key.split(':')
        if len(k_split) == 2:
            return KeysManager([f"{k_split[0].replace('-', '').strip()}:{k_split[1].replace('-', '').strip()}"])
        return None

    # Check if we have either local or remote CDM
    if cdm_device_path is None and cdm_remote_api is None:
        console.print("[red]Error: Must provide either cdm_device_path or cdm_remote_api.")
        return None
    
    return _get_playready_keys_local_cdm(pssh_list, license_url, cdm_device_path, cdm_remote_api, headers)


def _get_playready_keys_local_cdm(pssh_list: list[dict], license_url: str, cdm_device_path: str, cdm_remote_api: list[str], headers: dict = None):
    """Extract PlayReady keys using local or remote CDM device."""
    device = None
    cdm = None

    # Create a set of all expected KIDs (normalized)
    expected_kids = set()
    for item in pssh_list:
        kid = str(item.get('kid', '')).replace('-', '').lower().strip()
        if kid and kid != 'n/a':
            expected_kids.add(kid)
    
    # Initialize device
    if cdm_device_path is not None:
        console.print(get_info_prd(cdm_device_path))
        try:
            device = Device.load(cdm_device_path)
            cdm = Cdm.from_device(device)
        except Exception as e:
            console.print(f"[red]Error loading local CDM device: {e}")
            return None
    else:
        console.print("[green]Using remote CDM.")
        try:
            cdm = RemoteCdm(**cdm_remote_api)
        except Exception as e:
            console.print(f"[red]Error initializing remote CDM: {e}")
            return None

    # Open CDM session
    session_id = cdm.open()
    all_content_keys = []
    extracted_kids = set()
    
    try:
        for i, item in enumerate(pssh_list):
            pssh = item['pssh']
            kid_info = str(item.get('kid', 'N/A')).replace('-', '').lower().strip()
            type_info = item.get('type', 'unknown')
            console.print(f"[red]{type_info} [cyan](PSSH: [yellow]{pssh[:30]}...[cyan] KID: [red]{kid_info})")
            
            # Parse PSSH
            try:
                pssh_obj = PSSH(pssh)
            except Exception as e:
                console.print(f"[red]Invalid PlayReady PSSH/PRO header: {e}")
                continue
            
            if not pssh_obj.wrm_headers:
                console.print("[red]No WRM headers found in PSSH")
                continue
            
            # Create license challenge
            try:
                challenge = cdm.get_license_challenge(session_id, pssh_obj.wrm_headers[0])
            except Exception as e:
                console.print(f"[red]Error creating license challenge: {e}")
                continue
            
            # Prepare headers
            req_headers = headers.copy() if headers else {}
            if 'Content-Type' not in req_headers:
                req_headers['Content-Type'] = 'text/xml; charset=utf-8'

            if license_url is None:
                console.print("\n[red]License URL is None.")
                continue

            # Make license request
            try:
                response = create_client_curl(headers=req_headers).post(license_url, data=challenge)
            except Exception as e:
                console.print(f"[red]License request error: {e}")
                continue

            if response.status_code != 200:
                console.print(f"[red]License error: {response.status_code}\nResponse: {response.text[:200]}\nUrl: {license_url}\nHeaders: {req_headers}")
                continue

            # Parse license
            try:
                cdm.parse_license(session_id, response.text)
            except Exception as e:
                console.print(f"[red]Error parsing license: {e}")
                continue

            # Extract CONTENT keys
            try:
                for key_obj in cdm.get_keys(session_id):
                    kid = key_obj.key_id.hex.replace('-', '').lower().strip()
                    key_val = key_obj.key.hex().replace('-', '').strip()
                    formatted_key = f"{kid}:{key_val}"

                    if formatted_key not in all_content_keys:
                        all_content_keys.append(formatted_key)
                        extracted_kids.add(kid)
                        
            except Exception as e:
                console.print(f"[red]Error extracting keys: {e}")
                continue

        if all_content_keys:
            for i, k in enumerate(all_content_keys):
                kid, key_val = k.split(':')
                console.print(f"    - [red]{kid}[white]:[green]{key_val}")
        else:
            console.print("[yellow]No keys extracted")
        
        return KeysManager(all_content_keys) if all_content_keys else None
    
    except Exception as e:
        console.print(f"[red]Unexpected error during key extraction: {e}")
        return None
    
    finally:
        try:
            cdm.close(session_id)
        except Exception:
            pass