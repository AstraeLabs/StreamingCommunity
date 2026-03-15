# 29.01.26

import logging
import sys
from typing import Optional
from urllib.parse import urlparse

from rich.console import Console

from VibraVid.utils.vault import (obj_localDbValut, obj_externalSupaDbVault, obj_labDbVault)
from VibraVid.source.utils.object import KeysManager

from .playready import get_playready_keys
from .widevine import get_widevine_keys


console = Console()
logger = logging.getLogger(__name__)


class DRMManager:
    def __init__(self, widevine_device_path: str = None, playready_device_path: str = None, widevine_remote_cdm_api: list[str] = None, playready_remote_cdm_api: list[str] = None,):
        """Initialize DRM Manager with CDM paths and database connections."""
        self.widevine_device_path = widevine_device_path
        self.playready_device_path = playready_device_path
        self.widevine_remote_cdm_api = widevine_remote_cdm_api
        self.playready_remote_cdm_api = playready_remote_cdm_api

        self.is_local_db_connected = obj_localDbValut is not None
        self.is_supa_db_connected = obj_externalSupaDbVault is not None
        self.is_lab_db_connected = obj_labDbVault is not None

    def _clean_license_url(self, license_url: str) -> str:
        """Strip query params / fragments from a license URL."""
        if not license_url:
            return ""
        parsed = urlparse(license_url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    def _lookup_keys(self, db_obj, base_url: str, kids: list, drm_type: str) -> list:
        return list(db_obj.get_keys_by_kids(None, kids, drm_type) or [])

    def _missing_kids(self, all_kids: list[str], found_keys: list[str]) -> list[str]:
        """Return list of KIDs that are in all_kids but not in found_keys."""
        found = {k.split(":")[0].strip().lower() for k in found_keys}
        return [kid for kid in all_kids if kid not in found]

    def _store_keys(self, keys_list: list[str], drm_type: str, base_license_url: str, pssh_val: str, kid_to_label: Optional[dict] = None) -> None:
        """Store keys in connected databases."""
        if self.is_local_db_connected and base_license_url and pssh_val:
            logger.info(f"Storing {len(keys_list)} {drm_type} key(s) to local database")
            console.print(f"Storing {len(keys_list)} key(s) to local database...")
            obj_localDbValut.set_keys(keys_list, drm_type, base_license_url, pssh_val)

        if self.is_supa_db_connected and base_license_url and pssh_val:
            logger.info(f"Storing {len(keys_list)} {drm_type} key(s) to Supabase database")
            obj_externalSupaDbVault.set_keys(keys_list, drm_type, base_license_url, pssh_val, kid_to_label)

    def _db_lookup(self, all_kids: list[str], base_license_url: str, drm_type: str) -> list[str]:
        """Lookup keys in connected databases, returning list of found keys."""
        found_keys: list[str] = []
        if not all_kids or not base_license_url:
            return found_keys

        if self.is_local_db_connected:
            logger.info(f"Querying local DB for {len(all_kids)} {drm_type} KID(s)")
            found_keys.extend(self._lookup_keys(obj_localDbValut, base_license_url, all_kids, drm_type))

        if self.is_supa_db_connected:
            missing = self._missing_kids(all_kids, found_keys)
            if missing:
                logger.info(f"Querying Supabase for {len(missing)} {drm_type} KID(s)")
                found_keys.extend(self._lookup_keys(obj_externalSupaDbVault, base_license_url, missing, drm_type))

        if self.is_lab_db_connected:
            missing = self._missing_kids(all_kids, found_keys)
            if missing:
                logger.info(f"Querying Lab DB for {len(missing)} {drm_type} KID(s)")
                found_keys.extend(self._lookup_keys(obj_labDbVault, base_license_url, missing, drm_type))

        return found_keys

    def get_wv_keys(self, pssh_list: list[dict], license_url: str, license_certificate: str = None, headers: dict = None, key: str = None):
        """
        Get Widevine keys.
        """
        if key:
            manual_keys = []
            for entry in key.split("|"):
                parts = entry.split(":")
                if len(parts) == 2:
                    kid_val = parts[0].replace("-", "").strip()
                    key_val = parts[1].replace("-", "").strip()
                    if not manual_keys:
                        console.print("[cyan]Using Manual Key.")
                    console.print(f"    - [red]{kid_val}[white]:[green]{key_val[:-1]}* [cyan]| [red]Manual")
                    manual_keys.append(f"{kid_val}:{key_val}")
            if manual_keys:
                return KeysManager(manual_keys)

        base_license_url = self._clean_license_url(license_url)
        all_kids = [
            item["kid"].replace("-", "").strip().lower()
            for item in pssh_list
            if item.get("kid") and item["kid"] != "N/A"
        ]

        pssh_val = next((i.get("pssh") for i in pssh_list if i.get("pssh")), None)
        kid_to_label = {
            i["kid"].replace("-", "").strip().lower(): i["label"]
            for i in pssh_list
            if i.get("kid") and i["kid"] != "N/A" and i.get("label")
        } or None

        # Step 1: vault lookup
        if ((self.is_local_db_connected or self.is_supa_db_connected or self.is_lab_db_connected) and base_license_url and all_kids):
            logger.info(f"Looking up {len(all_kids)} Widevine KID(s) across available vaults")
            found_keys = self._db_lookup(all_kids, base_license_url, "widevine")
            unique_keys = list(set(found_keys))
            if unique_keys:
                self._store_keys(unique_keys, "widevine", base_license_url, pssh_val, kid_to_label)
            if set(all_kids).issubset({k.split(":")[0].strip().lower() for k in unique_keys}):
                logger.info(f"Widevine keys found in vault(s): {len(unique_keys)} key(s)")
                return KeysManager(unique_keys)

        # Step 2: CDM extraction
        try:
            keys = get_widevine_keys(pssh_list, license_url, self.widevine_device_path, self.widevine_remote_cdm_api, headers, key, license_certificate)
            if keys:
                logger.info(f"Widevine CDM extraction successful: {len(keys.get_keys_list())} key(s)")
                self._store_keys(keys.get_keys_list(),"widevine",base_license_url, pssh_val, kid_to_label)
                return keys

            logger.error("Widevine CDM extraction returned no keys")
            console.print("[yellow]CDM extraction returned no keys")
            sys.exit(0)

        except Exception as e:
            logger.error(f"Widevine CDM error: {e}")
            console.print(f"[red]CDM error: {e}")

        logger.error("All Widevine extraction methods failed")
        console.print("\n[red]All extraction methods failed for Widevine")
        sys.exit(0)
        return None

    def get_pr_keys(self, pssh_list: list[dict], license_url: str, headers: dict = None, key: str = None, license_data: dict = None):
        """
        Get PlayReady keys.
        """
        if key:
            manual_keys = []
            for entry in key.split("|"):
                parts = entry.split(":")
                if len(parts) == 2:
                    kid_val = parts[0].replace("-", "").strip()
                    key_val = parts[1].replace("-", "").strip()
                    if not manual_keys:
                        console.print("[cyan]Using Manual Key.")
                    console.print(f"    - [red]{kid_val}[white]:[green]{key_val[:-1]}* [cyan]| [red]Manual")
                    manual_keys.append(f"{kid_val}:{key_val}")
            if manual_keys:
                return KeysManager(manual_keys)

        base_license_url = self._clean_license_url(license_url)
        all_kids = [
            item["kid"].replace("-", "").strip().lower()
            for item in pssh_list
            if item.get("kid") and item["kid"] != "N/A"
        ]

        pssh_val = next((i.get("pssh") for i in pssh_list if i.get("pssh")), None)
        kid_to_label = {
            i["kid"].replace("-", "").strip().lower(): i["label"]
            for i in pssh_list
            if i.get("kid") and i["kid"] != "N/A" and i.get("label")
        } or None

        # Step 1: vault lookup
        if ((self.is_local_db_connected or self.is_supa_db_connected or self.is_lab_db_connected) and base_license_url and all_kids):
            logger.info(f"Looking up {len(all_kids)} PlayReady KID(s) across available vaults")
            found_keys = self._db_lookup(all_kids, base_license_url, "playready")
            unique_keys = list(set(found_keys))
            if unique_keys:
                self._store_keys(unique_keys, "playready", base_license_url, pssh_val, kid_to_label)
            if set(all_kids).issubset({k.split(":")[0].strip().lower() for k in unique_keys}):
                logger.info(f"PlayReady keys found in vault(s): {len(unique_keys)} key(s)")
                return KeysManager(unique_keys)

        # Step 2: CDM extraction
        try:
            keys = get_playready_keys(pssh_list, license_url, self.playready_device_path, self.playready_remote_cdm_api, headers, key, license_data=license_data)
            if keys:
                logger.info(f"PlayReady CDM extraction successful: {len(keys.get_keys_list())} key(s)")
                self._store_keys(keys.get_keys_list(), "playready", base_license_url, pssh_val, kid_to_label,)
                return keys

            logger.error("PlayReady CDM extraction returned no keys")
            console.print("[yellow]CDM extraction returned no keys")
            sys.exit(0)

        except Exception as e:
            logger.error(f"PlayReady CDM error: {e}")
            console.print(f"[red]CDM error: {e}")

        logger.error("All PlayReady extraction methods failed")
        console.print("\n[red]All extraction methods failed for PlayReady")
        return None