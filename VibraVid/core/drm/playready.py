# 29.01.26

import json
import base64
import logging

from rich.console import Console
from pyplayready.cdm import Cdm
from pyplayready.device import Device
from pyplayready.remote.remotecdm import RemoteCdm
from pyplayready.system.pssh import PSSH

from VibraVid.setup import get_info_prd
from VibraVid.utils.http_client import create_client
from VibraVid.source.utils.decrypt import KeysManager


console = Console()
logger = logging.getLogger(__name__)


def get_playready_keys(pssh_list: list[dict], license_url: str, cdm_device_path: str = None, cdm_remote_api: list[str] = None, headers: dict = None, key: str = None, license_data: dict = None):
    """
    Extract PlayReady CONTENT keys (KID/KEY) from a license.

    Args:
        - pssh_list (list[dict]): List of dicts {'pssh': ..., 'kid': ..., 'type': ...}
        - license_url (str): PlayReady license URL (may include query params).
        - cdm_device_path (str): Path to local .prd CDM file. Optional if using remote.
        - cdm_remote_api (list): Remote CDM API config. Optional if using local device.
        - headers (dict): HTTP headers for the license request.
        - key (str): Pre-existing KID:KEY — bypasses CDM entirely.
        - license_data (dict):    Extra fields merged into the license request body BEFORE the challenge is added.

    Returns:
        KeysManager | None
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

    return _get_playready_keys_local_cdm(pssh_list, license_url, cdm_device_path, cdm_remote_api, headers, license_data)


def _get_playready_keys_local_cdm(pssh_list: list[dict], license_url: str, cdm_device_path: str, cdm_remote_api: list[str], headers: dict = None, license_data: dict = None):
    """Extract PlayReady keys using local or remote CDM device."""
    cdm = None
    if cdm_device_path is not None:
        console.print(f"\n{get_info_prd(cdm_device_path)}")
        try:
            device = Device.load(cdm_device_path)
            cdm = Cdm.from_device(device)
        except Exception as e:
            logger.error(f"Error loading local CDM device: {e}")
            console.print(f"[red]Error loading local CDM device: {e}")
            return None
    else:
        console.print("[green]Using remote CDM.")
        try:
            cdm = RemoteCdm(**cdm_remote_api)
        except Exception as e:
            logger.error(f"Error initializing remote CDM: {e}")
            console.print(f"[red]Error initializing remote CDM: {e}")
            return None

    # Open CDM session
    session_id = cdm.open()
    all_content_keys = []

    try:
        for item in pssh_list:
            pssh = item["pssh"]
            kid_info = str(item.get("kid", "N/A")).replace("-", "").lower().strip()
            type_info = item.get("type", "unknown")
            console.print(f"[red]{type_info} [cyan](PSSH: [yellow]{pssh[:30]}...[cyan] KID: [red]{kid_info})")

            # Parse PSSH
            pssh_obj = PSSH(pssh)
            if not pssh_obj.wrm_headers:
                logger.error(f"No WRM headers found in PSSH for {kid_info}")
                console.print("[red]No WRM headers found in PSSH")
                continue

            # Create license challenge
            challenge = cdm.get_license_challenge(session_id, pssh_obj.wrm_headers[0])
            challenge_bytes = (challenge if isinstance(challenge, bytes) else challenge.encode("utf-8"))

            # Build request body and headers
            req_headers = (headers or {}).copy()

            if license_data:
                encoded_challenge = base64.b64encode(challenge_bytes).decode("utf-8")
                body = json.dumps({**license_data, "licenseChallenge": encoded_challenge}, separators=(",", ":")).encode("utf-8")
                req_headers.pop("Content-Type", None)
                req_headers.pop("content-type", None)
                req_headers["Content-Type"] = "text/plain"
            else:
                body = challenge_bytes
                req_headers.setdefault("Content-Type", "text/xml; charset=utf-8")

            if license_url is None:
                console.print("\n[red]License URL is None.")
                continue

            logger.info(f"License challenge for {kid_info}: {challenge_bytes}, type: {type(challenge_bytes)}")
            try:
                console.print("[dim]Requesting license ...")
                response = create_client(headers=req_headers).post(license_url, data=body)
            except Exception as e:
                logger.error(f"License request error for {kid_info}: {e}")
                console.print(f"[red]License request error for pssh {pssh[:30]}...: {e}")
                continue

            if response.status_code != 200:
                logger.error(f"License error for {kid_info}: HTTP {response.status_code}")
                console.print(f"[red]License error for pssh {pssh[:30]}...: {response.status_code}\nResponse: {response.text[:200]}\nUrl: {license_url}\nHeaders: {req_headers}")
                continue

            # Parse license response
            license_payload = response.text
            logger.info(f"License response [{response.status_code}]: {response.text[:200]}")

            if license_data:
                try:
                    rj = response.json()
                    b64_license = rj.get("playReadyLicense", {}).get("license")
                    license_payload = base64.b64decode(b64_license).decode("utf-8")
                    logger.info(f"Decoded PR license): {license_payload}")
                except Exception as e:
                    console.print(f"[red]Failed to parse license response: {e}\nRaw: {response.text[:200]}")
                    continue
            
            # Extract CONTENT keys
            try:
                cdm.parse_license(session_id, license_payload)
                for key_obj in cdm.get_keys(session_id):
                    kid = key_obj.key_id.hex.replace("-", "").lower().strip()
                    key_val = key_obj.key.hex().replace("-", "").strip()
                    formatted_key = f"{kid}:{key_val}"
                    if formatted_key not in all_content_keys:
                        all_content_keys.append(formatted_key)
            except Exception as e:
                logger.error(f"Error extracting keys: {e}")
                console.print(f"[red]Error extracting keys: {e}")
                continue

        if all_content_keys:
            for k in all_content_keys:
                kid, key_val = k.split(":")
                console.print(f"    - [red]{kid}[white]:[green]{key_val}")
        else:
            console.print("[yellow]No keys extracted")

        return KeysManager(all_content_keys) if all_content_keys else None

    except Exception as e:
        logger.error(f"Unexpected error during PlayReady key extraction: {e}")
        console.print(f"[red]Unexpected error during key extraction: {e}")
        return None

    finally:
        cdm.close(session_id)