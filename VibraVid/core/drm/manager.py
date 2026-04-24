# 29.01.26

import logging
from typing import Optional
from urllib.parse import urlparse

from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.utils.vault import supa_vault, lab_vault, claudio_vault
from VibraVid.core.decryptor import KeysManager

from .playready import get_playready_keys
from .widevine import get_widevine_keys


console = Console()
logger = logging.getLogger(__name__)
USE_CDM = config_manager.config.get_bool("DRM", "use_cdm")


class DRMManager:
    _VAULT_REGISTRY = [
        ("claudio", claudio_vault),
        ("lab",     lab_vault),
        ("supa",    supa_vault),
    ]

    def __init__(self, widevine_device_path: str = None, playready_device_path: str = None, widevine_remote_cdm_api: list[str] = None, playready_remote_cdm_api: list[str] = None, prefer_remote_cdm: bool = True):
        """Initialize DRM Manager with CDM paths and database connections."""
        self.widevine_device_path = widevine_device_path
        self.playready_device_path = playready_device_path
        self.widevine_remote_cdm_api = widevine_remote_cdm_api
        self.playready_remote_cdm_api = playready_remote_cdm_api
        self.prefer_remote_cdm = prefer_remote_cdm
        self._vaults: list[tuple[str, object]] = [(name, obj) for name, obj in self._VAULT_REGISTRY if obj is not None]

    def _clean_license_url(self, license_url: str) -> str:
        """Strip query params / fragments from a license URL."""
        if not license_url:
            return ""
        parsed = urlparse(license_url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    def _missing_kids(self, all_kids: list[str], found_keys: list[str]) -> list[str]:
        """Return list of KIDs that are in all_kids but not yet covered by found_keys."""
        found = {k.split(":")[0].strip().lower() for k in found_keys}
        return [kid for kid in all_kids if kid not in found]

    def _db_lookup(self, all_kids: list[str], base_license_url: str, drm_type: str, pssh_val: str = None) -> tuple[list[str], str]:
        """Query vaults in priority order, stopping as soon as all KIDs are covered."""
        found_keys: list[str] = []
        source = None

        if not all_kids or not base_license_url or not self._vaults:
            return found_keys, source

        for name, vdb in self._vaults:
            missing = self._missing_kids(all_kids, found_keys)
            if not missing:
                break

            logger.info(f"Querying {name} DB for {len(missing)} {drm_type} KID(s) | PSSH={pssh_val}" if pssh_val else f"Querying {name} DB for {len(missing)} {drm_type} KID(s)")
            keys = list(vdb.get_keys_by_kids(base_license_url, missing, drm_type, pssh_val) or [])
            if keys:
                found_keys.extend(keys)
                source = name

        return found_keys, source

    def _store_keys(self, keys_list: list[str], drm_type: str = "manual", base_license_url: str = "generic", pssh_val: str = None, kid_to_label: Optional[dict] = None, source: str = None) -> None:
        """Store keys in all connected vaults, skipping the one they were sourced from."""
        for name, vdb in self._vaults:
            if name == source:
                continue  # avoid writing back to the vault we just read from

            logger.info(f"Storing {len(keys_list)} {drm_type} key(s) to {name} database")
            try:
                # local vault does not accept kid_to_label — call with base signature
                if name == "local":
                    vdb.set_keys(keys_list, drm_type, base_license_url, pssh_val)
                else:
                    vdb.set_keys(keys_list, drm_type, base_license_url, pssh_val, kid_to_label)
            except Exception as e:
                logger.error(f"Failed to sync to {name} (will continue): {e}")
                console.print(f"[yellow]Warning: Could not sync to {name}: {e}")

    def _resolve_keys(self, pssh_list: list[dict], license_url: str, drm_type: str, cdm_fn, cdm_kwargs: dict, key: str = None) -> Optional[KeysManager]:
        """
        Shared key resolution logic for both Widevine and PlayReady.
        Step 1: Manual key override. Step 2: vault lookup (by license_url or generic). Step 3: CDM extraction as fallback.
        """
        all_kids = [
            item["kid"].replace("-", "").strip().lower()
            for item in pssh_list
            if item.get("kid") and item["kid"] != "N/A"
        ]

        if key:
            manual_keys = []
            for entry in key.split("|"):
                parts = entry.split(":")
                if len(parts) == 2:
                    logger.info(f"Using manual {drm_type} key override for KID {parts[0].strip()}")
                    kid_val = parts[0].replace("-", "").strip()
                    key_val = parts[1].replace("-", "").strip()
                    manual_keys.append(f"{kid_val}:{key_val}")
            if manual_keys:
                missing = self._missing_kids(all_kids, manual_keys)
                if missing:
                    msg = f"Missing {drm_type} key for KID(s): {', '.join(missing)}. Decryption cannot occur!"
                    logger.warning(msg)
                    console.print(f"[bold yellow]WARNING: {msg}[/bold yellow]")

                base_license_url = self._clean_license_url(license_url) or "generic"
                pssh_val = next((i.get("pssh") for i in pssh_list if i.get("pssh")), None)
                kid_to_label = {
                    i["kid"].replace("-", "").strip().lower(): i["label"]
                    for i in pssh_list
                    if i.get("kid") and i["kid"] != "N/A" and i.get("label")
                } or None
                self._store_keys(manual_keys, drm_type, base_license_url, pssh_val, kid_to_label, source=None)
                return KeysManager(manual_keys)

        base_license_url = self._clean_license_url(license_url)

        pssh_val = next((i.get("pssh") for i in pssh_list if i.get("pssh")), None)
        kid_to_label = {
            i["kid"].replace("-", "").strip().lower(): i["label"]
            for i in pssh_list
            if i.get("kid") and i["kid"] != "N/A" and i.get("label")
        } or None

        # Step 1: vault lookup with license_url
        if self._vaults and base_license_url and all_kids:
            logger.info(f"Looking up {len(all_kids)} {drm_type} KID(s) across {len(self._vaults)} vault(s)")
            found_keys, source = self._db_lookup(all_kids, base_license_url, drm_type, pssh_val)
            unique_keys = list(set(found_keys))
            
            if unique_keys:
                self._store_keys(unique_keys, drm_type, base_license_url, pssh_val, kid_to_label, source=source)
            if set(all_kids).issubset({k.split(":")[0].strip().lower() for k in unique_keys}):
                logger.info(f"{drm_type} keys found in vault(s): {len(unique_keys)} key(s)")
                return KeysManager(unique_keys)

        # Step 2: If no license_url but DRM detected → try generic lookup in database
        if not license_url and all_kids and self._vaults:
            logger.warning(f"DRM detected but missing license_url. Searching database for {len(all_kids)} {drm_type} KID(s) using 'generic' lookup")
            found_keys, source = self._db_lookup(all_kids, "generic", drm_type, pssh_val)
            unique_keys = list(set(found_keys))

            if unique_keys and set(all_kids).issubset({k.split(":")[0].strip().lower() for k in unique_keys}):
                logger.info(f"{drm_type} keys found in vault(s) via generic lookup: {len(unique_keys)} key(s)")
                return KeysManager(unique_keys)
            elif unique_keys:
                logger.warning(f"Found {len(unique_keys)} {drm_type} key(s) but not all KIDs covered. Partial match: {unique_keys}")

        # Step 3: CDM extraction
        if USE_CDM:
            try:
                keys = cdm_fn(pssh_list, license_url, **cdm_kwargs)
                if keys:
                    logger.info(f"{drm_type} CDM extraction successful: {len(keys.get_keys_list())} key(s)")
                    missing = self._missing_kids(all_kids, keys.get_keys_list())
                    if missing:
                        msg = f"Missing {drm_type} key for KID(s): {', '.join(missing)}. Decryption cannot occur!"
                        logger.warning(msg)
                        console.print(f"[bold yellow]WARNING: {msg}[/bold yellow]")
                    
                    self._store_keys(keys.get_keys_list(), drm_type, base_license_url, pssh_val, kid_to_label, source=None)
                    return keys

                logger.error(f"{drm_type} CDM extraction returned no keys. Missing keys for KIDs: {', '.join(all_kids)}. Decryption cannot occur!")
                console.print(f"[bold red]WARNING: Missing {drm_type} key for KID(s): {', '.join(all_kids)}. Decryption cannot occur![/bold red]")
                return None

            except Exception as e:
                logger.error(f"{drm_type} CDM error: {e}")
                console.print(f"[red]CDM error: {e}")

            logger.error(f"All {drm_type} extraction methods failed. Missing keys for KIDs: {', '.join(all_kids)}. Decryption cannot occur!")
            console.print(f"\n[bold red]WARNING: Missing {drm_type} key for KID(s): {', '.join(all_kids)}. Decryption cannot occur![/bold red]")
            return None
        else:
            logger.error(f"CDM extraction disabled. Missing {drm_type} keys for KIDs: {', '.join(all_kids)}. Decryption cannot occur!")
            console.print(f"[bold red]WARNING: Missing {drm_type} key for KID(s): {', '.join(all_kids)}. Decryption cannot occur![/bold red]")
            return None

    def get_wv_keys(self, pssh_list: list[dict], license_url: str, license_certificate: str = None, headers: dict = None, key: str = None):
        """
        Get Widevine keys.
        """
        return self._resolve_keys(
            pssh_list, license_url, "widevine",
            cdm_fn=get_widevine_keys,
            cdm_kwargs=dict(
                cdm_device_path=self.widevine_device_path,
                cdm_remote_api=self.widevine_remote_cdm_api,
                headers=headers,
                key=key,
                license_certificate=license_certificate,
                prefer_remote_cdm=self.prefer_remote_cdm,
            ),
            key=key,
        )

    def get_pr_keys(self, pssh_list: list[dict], license_url: str, headers: dict = None, key: str = None, license_data: dict = None):
        """
        Get PlayReady keys.
        """
        return self._resolve_keys(
            pssh_list, license_url, "playready",
            cdm_fn=get_playready_keys,
            cdm_kwargs=dict(
                cdm_device_path=self.playready_device_path,
                cdm_remote_api=self.playready_remote_cdm_api,
                headers=headers,
                key=key,
                license_data=license_data,
                prefer_remote_cdm=self.prefer_remote_cdm,
            ),
            key=key,
        )