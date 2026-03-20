# 29.01.26

import logging
from typing import List, Optional

from rich.console import Console
from urllib.parse import urlparse
from VibraVid.utils.http_client import create_client
from VibraVid.utils.config import config_manager


console = Console()
logger = logging.getLogger(__name__)
db_config = config_manager.config.get_dict("DRM", "vault")
VAULT_URL = db_config.get("supa").get("url", "")
TOKEN = db_config.get("supa").get("token", "")


class ExternalSupaDBVault:
    def __init__(self):
        self.base_url = f"{VAULT_URL}/functions/v1"
        self.headers = {"Content-Type": "application/json"}

    def _clean_license_url(self, license_url: str) -> str:
        """Extract base URL from license URL (remove query parameters and fragments)"""
        parsed = urlparse(license_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return base_url.rstrip("/")

    def _post(self, endpoint: str, payload: dict) -> Optional[dict]:
        """Internal helper: POST to an endpoint, return parsed JSON or None on error."""
        url = f"{self.base_url}/{endpoint}"
        try:
            logger.info("Post to Supabase endpoint '%s' with payload: %s", endpoint, payload)
            response = create_client(headers=self.headers).post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            console.print(f"[red]Supabase request error ({endpoint}): {e}")
            logger.error(f"Supabase request error ({endpoint}): {e}")
            return None

    # --------- SET
    def set_keys(self, keys_list: List[str], drm_type: str, license_url: str, pssh: str, kid_to_label: Optional[dict] = None) -> int:
        """
        Add multiple keys to the vault in a single bulk request.

        Args:
            keys_list: List of "kid:key" strings
            drm_type: 'widevine' or 'playready'
            license_url: Full license URL (will be cleaned server-side)
            pssh: PSSH string
            kid_to_label: Optional dict mapping normalised KID → human label

        Returns:
            int: Number of keys successfully added
        """
        logger.info(f"Adding {len(keys_list)} keys to vault for DRM type '{drm_type}' and license URL '{license_url}'")
        if not keys_list:
            return 0

        base_license_url = self._clean_license_url(license_url)
        keys_payload = []
        for key_str in keys_list:
            if ":" not in key_str:
                continue

            kid, key = key_str.split(":", 1)
            kid_clean = kid.strip()
            kid_norm = kid_clean.lower().replace("-", "")
            entry: dict = {"kid": kid_clean, "key": key.strip()}

            if kid_to_label:
                label = kid_to_label.get(kid_norm)
                if label:
                    entry["label"] = label

            keys_payload.append(entry)

        if not keys_payload:
            return 0

        payload = {
            "license_url": base_license_url,
            "pssh": pssh,
            "drm_type": drm_type,
            "keys": keys_payload,
        }

        result = self._post("save-keys", payload)
        logger.info(f"Vault response for saving keys: {result}")

        if result is None:
            return 0

        added = result.get("added", 0)
        return added

    # --------- GET
    def get_keys_by_pssh(self, license_url: str, pssh: str, drm_type: str) -> List[str]:
        """
        Retrieve all keys for a given license URL and PSSH (single request).

        Returns:
            List[str]: List of "kid:key" strings
        """
        base_license_url = self._clean_license_url(license_url)
        payload = {
            "license_url": base_license_url,
            "pssh": pssh,
            "drm_type": drm_type,
        }

        logger.info(f"Supabase get_keys_by_pssh: license_url={base_license_url}, drm_type={drm_type}, pssh={pssh[:20]}…")
        result = self._post("get-keys", payload)
        logger.info(f"Vault response for get_keys_by_pssh: {result}")

        if result is None:
            return []

        keys = result.get("keys", [])
        if keys:
            pssh_display = f"{pssh[:30]}..." if len(pssh) > 30 else pssh
            console.print(f"\n[red]{drm_type} [cyan](PSSH: [yellow]{pssh_display}[cyan])")
            for k in keys:
                kid_val, key_val = k["kid_key"].split(":", 1)
                console.print(f"    - [red]{kid_val}[white]:[green]{key_val} [cyan]| [#a855f7]supa")

        return [k["kid_key"] for k in keys]

    def get_keys_by_kids(self, license_url: Optional[str], kids: List[str], drm_type: str, pssh: str = None) -> List[str]:
        """
        Retrieve keys for one or more KIDs in a single bulk request.
        If license_url is None the search is global (all entries for that drm_type).

        Returns:
            List[str]: List of "kid:key" strings
        """
        if not kids:
            return []

        normalized_kids = [k.replace("-", "").strip().lower() for k in kids]
        base_license_url = self._clean_license_url(license_url) if license_url else None

        payload: dict = {"drm_type": drm_type, "kids": normalized_kids}
        if base_license_url:
            payload["license_url"] = base_license_url

        result = self._post("get-keys", payload)
        logger.info(f"Vault response for get_keys_by_kids: {result}")

        if result is None:
            return []

        keys = result.get("keys", [])
        if keys:
            if pssh:
                pssh_display = f"{pssh[:30]}..." if len(pssh) > 30 else pssh
            else:
                pssh_display = f"{normalized_kids[0][:30]}..." if normalized_kids else "..."
            console.print(f"\n[red]{drm_type} [cyan](PSSH: [yellow]{pssh_display}[cyan])")
            for k in keys:
                kid_val, key_val = k["kid_key"].split(":", 1)
                console.print(f"    - [red]{kid_val}[white]:[green]{key_val} [cyan]| [#a855f7]supa")

        return [k["kid_key"] for k in keys]

    def get_keys_by_kid(self, license_url: Optional[str], kid: str, drm_type: str) -> List[str]:
        """Convenience wrapper for a single KID lookup."""
        return self.get_keys_by_kids(license_url, [kid], drm_type)


is_supa_external_db_valid = not (VAULT_URL == "")
obj_externalSupaDbVault = ExternalSupaDBVault() if is_supa_external_db_valid else None