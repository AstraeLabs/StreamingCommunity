# 29.01.26

import logging
from base64 import b64decode
from typing import List, Optional

from rich.console import Console
from urllib.parse import urlparse

from VibraVid.utils.config import config_manager
from VibraVid.utils.http_client import create_client_curl, get_headers


console = Console()
logger = logging.getLogger(__name__)
db_config = config_manager.config.get_dict("DRM", "vault")
VAULT_URL = db_config.get("lab_v2").get("url", "")
TOKEN = db_config.get("lab_v2").get("token", "")


def _extract_kid_from_pssh(pssh_b64: str) -> Optional[str]:
    """Extract KID hex string from a PlayReady PSSH base64 blob."""
    try:
        data = b64decode(pssh_b64)
    except Exception:
        return None

    if b"<KID>" in data:
        start = data.index(b"<KID>") + 5
        end = data.index(b"</KID>", start)
        try:
            return b64decode(data[start:end]).hex()
        except Exception:
            return None

    return None


def _api_call(method: str, params: dict) -> dict:
    """POST a JSON-RPC-style request to the lab vault, return the `message` dict."""
    payload = {"method": method, "params": params, "token": TOKEN}
    try:
        logger.info(f"Calling Lab API ({method}): {params}")
        r = create_client_curl(headers=get_headers()).post(VAULT_URL, json=payload)
        r.raise_for_status()
        data = r.json()

        if data.get("status_code") != 200:
            raise RuntimeError(f"Lab API error: {data}")
        return data.get("message", {})

    except Exception as e:
        logger.error(f"Lab API call failed ({method}): {e}")
        console.print(f"[red]Lab API call failed ({method}): {e}")
        return {}


class LabDBVault:
    """Vault backed by the external Lab API."""

    def _clean_license_url(self, license_url: str) -> str:
        """Strip query params / fragments from a license URL."""
        parsed = urlparse(license_url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    def _normalize_kid(self, kid: str) -> str:
        """Return a clean lowercase hex KID, resolving PSSH blobs when needed."""
        if "=" in kid and len(kid) > 32:
            resolved = _extract_kid_from_pssh(kid)
            if resolved:
                return resolved
        return kid.replace("-", "").strip().lower()

    # --------- SET
    def set_key(self, kid: str, key: str, drm_type: str, license_url: str, pssh: str = None, label: str = None) -> bool:
        """
        Store a single DRM key in the lab vault.

        Returns:
            bool: True if the key was stored successfully.
        """
        pass

    def set_keys(self, keys_list: List[str], drm_type: str, license_url: str, pssh: str = None, kid_to_label: Optional[dict] = None) -> int:
        """
        Store multiple DRM keys in the lab vault.

        Returns:
            int: Number of keys successfully stored.
        """
        pass

    # --------- GET
    def get_keys_by_pssh(self, license_url: str, pssh: str, drm_type: str) -> List[str]:
        """
        Retrieve all keys for a given license URL and PSSH.

        Returns:
            List[str]: List of "kid:key" strings.
        """
        pass

    def get_keys_by_kids(self, license_url: Optional[str], kids: List[str], drm_type: str, pssh: str = None) -> List[str]:
        """
        Retrieve keys for one or more KIDs
        """
        if not kids:
            return []

        results: List[str] = []

        for kid_raw in kids:
            kid = self._normalize_kid(kid_raw)
            params: dict = {"kid": kid, "session_id": None}
            if license_url:
                params["service"] = self._clean_license_url(license_url)

            msg = _api_call("GetKey", params)
            if not msg:
                continue

            for entry in msg.get("keys", []):
                if isinstance(entry, dict):
                    if entry.get("kid") == kid:
                        key_val = entry.get("key")
                        if key_val:
                            results.append(f"{kid}:{key_val}")
                elif isinstance(entry, str) and ":" in entry:
                    k, v = entry.split(":", 1)
                    if k == kid:
                        results.append(f"{kid}:{v}")

        if results:
            if pssh:
                pssh_display = f"{pssh[:30]}..." if len(pssh) > 30 else pssh
            else:
                pssh_display = f"{kids[0][:30]}..." if kids else "..."
            
            console.print(f"\n[red]{drm_type} [cyan](PSSH: [yellow]{pssh_display}[cyan])")
            for r in results:
                kid_val, key_val = r.split(":", 1)
                console.print(f"    - [red]{kid_val}[white]:[green]{key_val} [cyan]| [#a855f7]lab")

        return results

    def get_keys_by_kid(self, license_url: Optional[str], kid: str, drm_type: str) -> List[str]:
        """Convenience wrapper for a single KID lookup."""
        return self.get_keys_by_kids(license_url, [kid], drm_type)

    # --------- UPDATE
    def update_key_validity(self, kid: str, is_valid: bool, license_url: Optional[str] = None, drm_type: Optional[str] = None, pssh: Optional[str] = None) -> bool:
        """
        Update the validity status of a key in the lab vault.

        Returns:
            bool: True if updated successfully.
        """
        pass


is_lab_db_valid = bool(VAULT_URL and TOKEN)
obj_labDbVault = LabDBVault() if is_lab_db_valid else None