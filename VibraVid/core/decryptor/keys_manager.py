# 01.04.26

import logging
from typing import Optional

from ._mp4_inspector import extract_widevine_kid

logger = logging.getLogger(__name__)


class KeysManager:
    def __init__(self, keys=None) -> None:
        self._keys: list[tuple[str, str]] = []
        if keys:
            self.add_keys(keys)

    def add_keys(self, keys) -> None:
        if isinstance(keys, str):
            for k in keys.split("|"):
                pair = k.strip()
                if ":" in pair:
                    kid, key = pair.split(":", 1)
                    self._keys.append((kid.strip(), key.strip()))
        elif isinstance(keys, list):
            for k in keys:
                if isinstance(k, str):
                    pair = k.strip()
                    if ":" in pair:
                        kid, key = pair.split(":", 1)
                        self._keys.append((kid.strip(), key.strip()))
                elif isinstance(k, dict):
                    kid = k.get("kid", "")
                    key = k.get("key", "")
                    if kid and key:
                        self._keys.append((kid.strip(), key.strip()))

    def get_keys_list(self) -> list[str]:
        return [f"{kid}:{key}" for kid, key in self._keys]

    def __len__(self) -> int:               
        return len(self._keys)
    def __iter__(self):                           
        return iter(self._keys)
    def __getitem__(self, index):                 
        return self._keys[index]
    def __bool__(self)      -> bool:              
        return len(self._keys) > 0

def normalize_keys(keys) -> list[tuple[str, str]]:
    """
    Coerce any supported key representation into a list of ``(kid, key)`` lowercase hex string tuples.
    """
    if isinstance(keys, KeysManager):
        raw = keys.get_keys_list()
    elif isinstance(keys, str):
        raw = [k.strip() for k in keys.split("|") if k.strip()]
    elif isinstance(keys, list):
        raw = keys
    else:
        raw = []

    normalized: list[tuple[str, str]] = []
    for item in raw:
        if isinstance(item, (tuple, list)) and len(item) == 2:
            normalized.append((str(item[0]).lower(), str(item[1]).lower()))
        
        elif isinstance(item, str):
            for pair in item.split("|"):
                p = pair.strip()
                if not p:
                    continue

                if ":" in p:
                    kid, key = p.split(":", 1)
                    normalized.append((kid.strip().lower(), key.strip().lower()))
                else:
                    normalized.append(("1", p.lower()))
    
    return normalized


def is_zero_kid(kid: Optional[str]) -> bool:
    """Return True when *kid* is all-zero hex (fixed-key stream)."""
    return bool(kid and kid.lower() == "0" * len(kid))


def resolve_fixed_key_if_needed(encrypted_path: str, detected_kid: Optional[str], normalized_keys: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    For fixed-key streams (all-zero KID) with multiple candidates, attempt to
    narrow to the correct key by extracting the real KID from the Widevine PSSH.

    Falls back to the first key if PSSH extraction fails or yields no match.
    """
    if not is_zero_kid(detected_kid) or len(normalized_keys) <= 1:
        return normalized_keys

    pssh_kid = extract_widevine_kid(encrypted_path)
    if not pssh_kid:
        logger.warning("Fixed-key stream with multiple keys but no PSSH KID extracted; using first key")
        return [normalized_keys[0]]

    for pair in normalized_keys:
        if pair[0].lower() == pssh_kid:
            logger.info(f"Fixed-key stream: selected key by PSSH KID match ({pssh_kid})")
            return [pair]

    logger.warning(f"No key matched PSSH KID {pssh_kid}; using first key")
    return [normalized_keys[0]]