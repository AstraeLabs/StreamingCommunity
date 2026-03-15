# 29.01.26

import time
import base64
import logging

from rich.console import Console
from pywidevine.cdm import Cdm
from pywidevine.device import Device
from pywidevine.device import DeviceTypes
from pywidevine.remotecdm import RemoteCdm
from pywidevine.pssh import PSSH

from VibraVid.setup import get_info_wvd
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client_curl
from VibraVid.source.utils.object import KeysManager


console = Console()
logger = logging.getLogger(__name__)
DELAY = config_manager.config.get("DRM", "delay")


def get_widevine_keys(pssh_list: list[dict], license_url: str, cdm_device_path: str = None, cdm_remote_api: list[str] = None, headers: dict = None, key: str = None, license_certificate: str = None):
    """
    Extract Widevine CONTENT keys (KID/KEY) from a license.

    Args:
        - pssh_list (list[dict]): List of dicts {'pssh': ..., 'kid': ..., 'type': ...}
        - license_url (str): Widevine license URL.
        - cdm_device_path (str): Path to local CDM file (device.wvd). Optional if using remote.
        - cdm_remote_api (list[str]): Remote CDM API config. Optional if using local device.
        - headers (dict): Optional HTTP headers for the license request (from fetch).
        - key (str): Optional raw license data to bypass HTTP request.
        - license_certificate (str): Optional base64-encoded SignedMessage for CDM Privacy Mode. If None or empty, set_service_certificate is not called.

    Returns:
        list: List of strings "KID:KEY" (only CONTENT keys) or None if error.
    """
    # Handle pre-existing key
    if key:
        k_split = key.split(":")
        if len(k_split) == 2:
            return KeysManager([f"{k_split[0].replace('-', '').strip()}:{k_split[1].replace('-', '').strip()}"])
        return None

    # Check if we have either local or remote CDM
    if cdm_device_path is None and cdm_remote_api is None:
        logger.error("Must provide either cdm_device_path or cdm_remote_api")
        console.print("[red]Error: Must provide either cdm_device_path or cdm_remote_api.")
        return None

    return _get_widevine_keys(pssh_list, license_url, cdm_device_path, cdm_remote_api, headers, license_certificate)


def _get_widevine_keys(pssh_list: list[dict], license_url: str, cdm_device_path: str, cdm_remote_api: list[str], headers: dict = None, license_certificate: str = None):
    """Extract Widevine keys using local or remote CDM device."""
    device = None
    cdm = None

    # Create a set of all expected KIDs (normalized)
    expected_kids = set()
    for item in pssh_list:
        kid = str(item.get("kid", "")).replace("-", "").lower().strip()
        if kid and kid != "n/a":
            expected_kids.add(kid)

    # Initialize device
    if cdm_device_path is not None:
        console.print(f"\n{get_info_wvd(cdm_device_path)}")
        try:
            device = Device.load(cdm_device_path)
            cdm = Cdm.from_device(device)
        except Exception as e:
            logger.error(f"Error loading local CDM device: {e}")
            console.print(f"[red]Error loading local CDM device: {e}")
            return None
    else:
        console.print("[cyan]Using remote CDM.")
        try:
            if cdm_remote_api["device_type"] == "ANDROID":
                cdm_remote_api["device_type"] = DeviceTypes.ANDROID
            elif cdm_remote_api["device_type"] == "CHROME":
                cdm_remote_api["device_type"] = DeviceTypes.CHROME
            else:
                logger.error(f"Unsupported remote CDM device type: {cdm_remote_api['device_type']}")
                console.print(f"[red]Unsupported remote CDM device type: {cdm_remote_api['device_type']}")
                return None
            cdm = RemoteCdm(**cdm_remote_api)
        except Exception as e:
            logger.error(f"Error initializing remote CDM: {e}")
            console.print(f"[red]Error initializing remote CDM: {e}")
            return None

    # Open CDM session
    session_id = cdm.open()

    if license_certificate:
        try:
            cert = license_certificate.strip().replace("\n", "").replace(" ", "")
            cdm.set_service_certificate(session_id, cert)
            console.print("[dim]Service certificate set (Privacy Mode enabled).")
        except Exception as e:
            logger.error(f"Failed to set service certificate: {e}")
            console.print(f"[yellow]Warning: Failed to set service certificate: {e}")

    all_content_keys = []
    extracted_kids = set()

    try:
        for i, item in enumerate(pssh_list):
            console.print("[dim]Sleeping for delay...")
            time.sleep(DELAY)

            pssh = item["pssh"]
            kid_info = str(item.get("kid", "N/A")).replace("-", "").lower().strip()
            type_info = item.get("type", "unknown")
            console.print(f"[red]{type_info} [cyan](PSSH: [yellow]{pssh[:30]}...[cyan] KID: [red]{kid_info})")

            # Create license challenge
            try:
                console.print("[dim]Creating license challenge...")
                challenge = cdm.get_license_challenge(session_id, PSSH(pssh))
            except Exception as e:
                logger.error(f"Error creating challenge for {kid_info}: {e}")
                console.print(f"[red]Error creating challenge for PSSH {pssh[:30]}...: {e}")
                continue

            # Prepare headers (use original headers from fetch)
            req_headers = headers.copy() if headers else {}
            if "Content-Type" not in req_headers:
                req_headers["Content-Type"] = "application/octet-stream"

            if license_url is None:
                logger.error("License URL is None")
                console.print("\n[red]License URL is None.")
                continue

            # Make license request
            try:
                console.print("[dim]Requesting license ...")
                response = create_client_curl(headers=req_headers).post(license_url, data=challenge)
            except Exception as e:
                logger.error(f"License request error for {kid_info}: {e}")
                console.print(f"[red]License request error for PSSH {pssh[:30]}...: {e}")
                continue

            if response.status_code != 200:
                logger.error(f"License error for {kid_info}: HTTP {response.status_code}")
                console.print(f"[red]License error for PSSH {pssh[:30]}...: {response.status_code}\nResponse: {response.content.decode('latin-1')[:200]}\nUrl: {license_url}\nHeaders: {req_headers}")
                continue

            # Parse license response
            content_type = response.headers.get("content-type", "").lower()
            license_bytes = response.content

            if "application/json" in content_type:
                logger.info(f"Parsing JSON license response for {kid_info}")
                try:
                    data = response.json()
                    if "license" in data:
                        license_bytes = base64.b64decode(data["license"])
                    else:
                        logger.error(f"'license' field not found in JSON response for {kid_info}")
                        console.print(f"[red]'license' field not found in JSON response for PSSH {pssh[:30]}...]")
                        continue
                except Exception as e:
                    logger.error(f"Error parsing JSON license response for {kid_info}: {e}")
                    console.print(f"[red]Error parsing JSON license response for PSSH {pssh[:30]}...: {e}")
                    pass  # SKIP JSON parsing error and try raw content
            else:
                logger.info(f"Received non-JSON license response for {kid_info} (content-type: {content_type})")

            if not license_bytes:
                console.print(f"[red]License data is empty for PSSH {pssh[:30]}...]")
                continue

            # Parse license
            try:
                console.print("[dim]Parsing license with CDM...")
                cdm.parse_license(session_id, license_bytes)
            except Exception as e:
                logger.error(f"Error parsing license for PSSH {pssh[:30]}...: {e}")
                console.print(f"[red]Error parsing license for PSSH {pssh[:30]}...: {e}")
                continue

            # Extract CONTENT keys
            try:
                for key_obj in cdm.get_keys(session_id):
                    if key_obj.type != "CONTENT":
                        continue

                    # Get KID and normalize
                    kid = key_obj.kid.hex.lower().strip()
                    formatted_key = f"{kid}:{key_obj.key.hex()}"
                    if formatted_key not in all_content_keys:
                        all_content_keys.append(formatted_key)
                        extracted_kids.add(kid)

            except Exception as e:
                console.print(f"[red]Error extracting keys for PSSH {pssh[:30]}...: {e}")
                continue

        if all_content_keys:
            for i, k in enumerate(all_content_keys):
                kid, key_val = k.split(":")
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