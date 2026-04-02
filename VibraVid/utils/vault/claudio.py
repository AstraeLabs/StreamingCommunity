# 02.04.26

import logging
from typing import List, Optional

from rich.console import Console
from urllib.parse import urlparse
from VibraVid.utils.http_client import create_client
from VibraVid.utils.config import config_manager


console = Console()
logger = logging.getLogger(__name__)
db_config = config_manager.config.get_dict("DRM", "vault")
VAULT_URL = db_config.get("claudio", {}).get("url", "")
TOKEN = db_config.get("claudio", {}).get("token", "")


class ClaudioDBVault:
    def __init__(self):
        self.base_url = VAULT_URL
        self.headers = {"Content-Type": "application/json"}
        self.session = create_client(headers=self.headers, http2=True)

    def _clean_license_url(self, license_url: str) -> str:
        """Extract base URL from license URL (remove query parameters and fragments)"""
        if not license_url:
            return ""
        parsed = urlparse(license_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return base_url.rstrip("/")

    def _post(self, endpoint: str, payload: dict) -> Optional[dict]:
        """Internal helper: POST to an endpoint, return parsed JSON or None on error.""" 
        url = f"{self.base_url}{endpoint}"
        if TOKEN:
            url = f"{url}?token={TOKEN}"
        try:
            logger.info(f"POST to Claudio endpoint '{endpoint}' with TOKEN{' (added)' if TOKEN else ' (not configured)'} with payload: {payload}")
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            console.print(f"[red]Claudio Vault request error ({endpoint}): {e}")
            logger.error(f"Claudio Vault request error ({endpoint}): {e}")
            return None

    def set_keys(self, keys_list: List[str], drm_type: str, license_url: str, pssh: str, kid_to_label: Optional[dict] = None) -> int:
        """
        Add multiple keys to the Claudio vault in a single bulk request.

        Returns:
            int: Number of keys successfully added
        """
        logger.info(f"Adding {len(keys_list)} keys to Claudio vault for DRM type '{drm_type}' and license URL '{license_url}'")
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

        result = self._post("/functions/v1/save-keys", payload)
        logger.info(f"Claudio Vault response for saving keys: {result}")

        if result is None:
            return 0

        added = result.get("added", 0)
        return added

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

        logger.info(f"Claudio get_keys_by_pssh: license_url={base_license_url}, drm_type={drm_type}, pssh={pssh[:20]}…")
        result = self._post("/functions/v1/get-keys", payload)
        logger.info(f"Claudio Vault response for get_keys_by_pssh: {result}")

        if result is None:
            return []

        keys = result.get("keys", [])
        if keys:
            pssh_display = f"{pssh[:30]}..." if len(pssh) > 30 else pssh
            console.print(f"\n[red]{drm_type} [cyan](PSSH: [yellow]{pssh_display}[cyan])")
            for k in keys:
                kid_val = k.get("kid")
                key_val = k.get("key")
                console.print(f"    - [red]{kid_val}[white]:[green]{key_val} [cyan]| [#FF6B9D]claudio")

        return [f"{k['kid']}:{k['key']}" for k in keys if k.get("kid") and k.get("key")]

    def get_keys_by_kids(self, license_url: Optional[str], kids: List[str], drm_type: str, pssh: str = None) -> List[str]:
        """
        Retrieve keys for one or more KIDs in a single bulk request.

        Returns:
            List[str]: List of "kid:key" strings
        """
        normalized_kids = [k.replace("-", "").strip().lower() for k in kids]
        base_license_url = self._clean_license_url(license_url) if license_url else None

        payload: dict = {"drm_type": drm_type, "kids": normalized_kids}
        if base_license_url:
            payload["license_url"] = base_license_url
        if pssh:
            payload["pssh"] = pssh

        logger.info(f"Claudio get_keys_by_kids: KIDs={normalized_kids}, drm_type={drm_type}, license_url={base_license_url}, pssh={pssh[:20] if pssh else 'N/A'}…")
        result = self._post("/functions/v1/get-keys", payload)
        logger.info(f"Claudio Vault response for get_keys_by_kids: {result}")

        if result is None:
            return []

        keys = result.get("keys", [])
        if keys:
            console.print(f"\n[red]{drm_type}")
            for k in keys:
                kid_val = k.get("kid")
                key_val = k.get("key")
                console.print(f"    - [red]{kid_val}[white]:[green]{key_val} [cyan]| [#FF6B9D]claudio")

        return [f"{k['kid']}:{k['key']}" for k in keys if k.get("kid") and k.get("key")]


is_claudio_external_db_valid = not(VAULT_URL == "")
claudio_vault = ClaudioDBVault() if is_claudio_external_db_valid else None