# 29.01.26

import logging
from typing import Optional

from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.utils.vault._url_utils import clean_license_url
from VibraVid.utils.vault import supa_vault, lab_vault
from VibraVid.core.decryptor import KeysManager
from VibraVid.setup import binary_paths

from .playready import get_playready_keys
from .widevine import get_widevine_keys


console = Console()
logger = logging.getLogger(__name__)
USE_CDM = config_manager.config.get_bool("DRM", "use_cdm")


class DRMManager:
    _VAULT_REGISTRY = [
        ("lab",     lab_vault),
        ("supa",    supa_vault),
    ]
    _VAULT_LABELS = {
        "supa": "claudio", 
        "lab": "lab"
    }

    def __init__(self, widevine_device_path: str = None, playready_device_path: str = None, widevine_remote_cdm_api: list[str] = None, playready_remote_cdm_api: list[str] = None, prefer_remote_cdm: bool = True):
        """Initialize DRM Manager with CDM paths and database connections."""
        self.widevine_device_path = widevine_device_path
        self.playready_device_path = playready_device_path
        self.widevine_remote_cdm_api = widevine_remote_cdm_api
        self.playready_remote_cdm_api = playready_remote_cdm_api
        self.prefer_remote_cdm = prefer_remote_cdm
        self._vaults: list[tuple[str, object]] = [(name, obj) for name, obj in self._VAULT_REGISTRY if obj is not None]

    def _display_keys(self, resolved: list[str], vault_keys: list[str], drm_type: str, pssh_val: Optional[str], source: Optional[str], header: bool) -> None:
        """Display resolved keys in the console, indicating which came from vaults and which were newly extracted"""
        if not resolved:
            return

        if header:
            pssh_disp = f"{pssh_val[:30]}..." if pssh_val and len(pssh_val) > 30 else (pssh_val or "...")
            console.print(f"\n[red]{drm_type} [cyan](PSSH: [yellow]{pssh_disp}[cyan])")

        label = self._VAULT_LABELS.get(source, source)
        vault_kids = {k.split(":")[0].strip().lower() for k in vault_keys}
        plain  = [k for k in resolved if k.split(":")[0].strip().lower() not in vault_kids]
        tagged = [k for k in resolved if k.split(":")[0].strip().lower() in vault_kids]
        
        for k in plain + tagged:
            kid_val, key_val = k.split(":", 1)
            suffix = f" [cyan]| [#a855f7]{label}" if k in tagged else ""
            console.print(f"    - [red]{kid_val}[white]:[green]{key_val}{suffix}")

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

    def _resolve_keys(self, pssh_list: list[dict], license_url: str, drm_type: str, cdm_fn, cdm_kwargs: dict, key: str | list[str] = None) -> Optional[KeysManager]:
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
            manual = KeysManager(key)
            manual_keys = manual.get_keys_list()
            
            if manual_keys:
                missing = self._missing_kids(all_kids, manual_keys)
                if missing:
                    msg = f"Missing {drm_type} key for KID(s): {', '.join(missing)}. Decryption cannot occur!"
                    logger.warning(msg)
                    console.print(f"[bold yellow]WARNING: {msg}[/bold yellow]")

                base_license_url = clean_license_url(license_url) or "generic"
                pssh_val = next((i.get("pssh") for i in pssh_list if i.get("pssh")), None)
                kid_to_label = {
                    i["kid"].replace("-", "").strip().lower(): i["label"]
                    for i in pssh_list
                    if i.get("kid") and i["kid"] != "N/A" and i.get("label")
                } or None

                self._store_keys(manual_keys, drm_type, base_license_url, pssh_val, kid_to_label, source=None)
                self._display_keys(manual_keys, [], drm_type, pssh_val, None, header=True)
                return KeysManager(manual_keys)

        base_license_url = clean_license_url(license_url)

        pssh_val = next((i.get("pssh") for i in pssh_list if i.get("pssh")), None)
        kid_to_label = {
            i["kid"].replace("-", "").strip().lower(): i["label"]
            for i in pssh_list
            if i.get("kid") and i["kid"] != "N/A" and i.get("label")
        } or None

        # Step 1: vault lookup with license_url
        vault_keys: list[str] = []
        vault_source = None

        if self._vaults and base_license_url and all_kids:
            found_keys, vault_source = self._db_lookup(all_kids, base_license_url, drm_type, pssh_val)
            vault_keys = list(set(found_keys))

            if vault_keys:
                self._store_keys(vault_keys, drm_type, base_license_url, pssh_val, kid_to_label, source=vault_source)

            if set(all_kids).issubset({k.split(":")[0].strip().lower() for k in vault_keys}):
                logger.info(f"{drm_type} keys found in vault(s): {len(vault_keys)} key(s)")
                self._display_keys(vault_keys, vault_keys, drm_type, pssh_val, vault_source, header=True)
                return KeysManager(vault_keys)

        # Step 2: If no license_url but DRM detected → try generic lookup in database
        if not license_url and all_kids and self._vaults:
            logger.warning(f"DRM detected but missing license_url. Searching database for {len(all_kids)} {drm_type} KID(s) using 'generic' lookup")
            found_keys, vault_source = self._db_lookup(all_kids, "generic", drm_type, pssh_val)
            vault_keys = list(set(found_keys))

            if vault_keys and set(all_kids).issubset({k.split(":")[0].strip().lower() for k in vault_keys}):
                logger.info(f"{drm_type} keys found in vault(s) via generic lookup: {len(vault_keys)} key(s)")
                self._display_keys(vault_keys, vault_keys, drm_type, pssh_val, vault_source, header=True)
                return KeysManager(vault_keys)
            
            elif vault_keys:
                logger.warning(f"Found {len(vault_keys)} {drm_type} key(s) but not all KIDs covered. Partial match: {vault_keys}")

        # Step 3: CDM extraction — only for KIDs not already covered by vault
        if USE_CDM:
            try:
                vault_covered = {k.split(":")[0].strip().lower() for k in vault_keys}
                missing_kids  = [kid for kid in all_kids if kid not in vault_covered]

                if missing_kids:
                    # Filter pssh_list to only the PSSHs whose KID is still missing
                    missing_pssh_list = [
                        item for item in pssh_list
                        if item.get("kid", "").replace("-", "").strip().lower() in missing_kids
                    ] or pssh_list  # safety: if filtering gives empty, use full list
                    logger.info(f"{drm_type} CDM extraction for {len(missing_kids)} missing KID(s): {missing_kids}")
                    cdm_result = cdm_fn(missing_pssh_list, license_url, **cdm_kwargs)
                
                else:
                    cdm_result = None

                if cdm_result:
                    cdm_keys = cdm_result.get_keys_list()

                    # Merge: vault keys + CDM keys (CDM may return extras like b770…, keep all)
                    all_keys = list({k.split(":")[0]: k for k in vault_keys + cdm_keys}.values())
                    logger.info(f"{drm_type} CDM extraction successful: {len(cdm_keys)} new key(s), {len(all_keys)} total")
                    self._store_keys(all_keys, drm_type, base_license_url, pssh_val, kid_to_label, source=None)
                    self._display_keys(all_keys, vault_keys, drm_type, pssh_val, vault_source, header=False)
                    return KeysManager(all_keys)

                elif vault_keys:
                    # CDM returned nothing new but we have partial vault keys — return those
                    logger.warning(f"{drm_type} CDM returned no new keys; returning {len(vault_keys)} vault key(s)")
                    self._display_keys(vault_keys, vault_keys, drm_type, pssh_val, vault_source, header=False)
                    return KeysManager(vault_keys)

                logger.error(f"{drm_type} CDM extraction returned no keys")
                console.print("[yellow]CDM extraction returned no keys")

            except Exception as e:
                logger.error(f"{drm_type} CDM error: {e}")
                console.print(f"[red]CDM error: {e}")

            logger.error(f"All {drm_type} extraction methods failed")
            console.print(f"\n[red]All extraction methods failed for {drm_type}")
            console.print(f"[yellow]Please place CDM files (.wvd for Widevine, .prd for PlayReady) in:\n  {binary_paths.get_binary_directory()}[/yellow]")
        else:
            console.print("[yellow]CDM extraction disabled by config.")

    def get_wv_keys(self, pssh_list: list[dict], license_url: str, license_data: dict = None, license_certificate: str = None, headers: dict = None, key: str = None, license_request_fn=None):
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
                license_data=license_data,
                license_certificate=license_certificate,
                prefer_remote_cdm=self.prefer_remote_cdm,
                license_request_fn=license_request_fn,
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
    
    def add_keys(self, keys: list[str], drm_type: str, license_url: str, pssh: str = None, kid_to_label: Optional[dict] = None) -> dict[str, int]:
        """
        Manually push one or more keys to all connected vaults.

        Args:
            keys:         List of "kid:key" strings (e.g. ["abc123:def456"]).
            drm_type:     DRM type string, e.g. "widevine" or "playready".
            license_url:  License server URL (query params are stripped automatically).
            pssh:         Optional PSSH blob associated with these keys.
            kid_to_label: Optional mapping {kid_hex: label} for track labelling.

        Returns:
            dict[str, int]: {vault_name: keys_added} for each vault that accepted the call.
        """
        if not keys:
            logger.warning("add_keys called with empty keys list — nothing to store.")
            return {}

        # Validate format
        valid_keys = []
        for entry in keys:
            if ":" not in entry:
                logger.warning(f"Skipping malformed key entry (expected 'kid:key'): {entry!r}")
                console.print(f"[yellow]Skipping malformed key: {entry!r}")
                continue
            valid_keys.append(entry)

        if not valid_keys:
            console.print("[red]No valid keys to store.")
            return {}

        base_license_url = clean_license_url(license_url) if license_url else "generic"
        results: dict[str, int] = {}

        for name, vdb in self._vaults:
            logger.info(f"[add_keys] Storing {len(valid_keys)} {drm_type} key(s) to {name}")
            try:
                added = vdb.set_keys(valid_keys, drm_type, base_license_url, pssh, kid_to_label)
                results[name] = added
            except Exception as e:
                logger.error(f"[add_keys] Failed to store to {name}: {e}")
                console.print(f"[red]✗ {name}: {e}")
                results[name] = 0

        return results
