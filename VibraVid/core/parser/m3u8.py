# 07.03.26

import re
import base64
import binascii
import logging
from typing import Dict, List

from pywidevine.pssh import PSSH
from pyplayready.system.pssh import PSSH as PR_PSSH
from rich.console import Console

from VibraVid.utils.http_client import create_client_curl, get_userAgent


console = Console()
logger = logging.getLogger(__name__)


class M3U8Parser:
    def __init__(self, m3u8_url: str, headers: Dict[str, str] = None, content: str = None):
        """
        Initialize M3U8 parser.
        
        Args:
            m3u8_url: URL to M3U8 file
            headers: Optional HTTP headers for fetching
            content: Optional pre-loaded M3U8 content (skips fetch if provided)
        """
        self.m3u8_url = m3u8_url
        self.headers = headers or {}
        self.content = content or ""

    def fetch(self) -> bool:
        """Fetch M3U8 content from URL (skipped if content is already set)."""
        if self.content:
            return True
            
        try:
            m3u8_headers = self.headers.copy()
            if 'User-Agent' not in m3u8_headers:
                m3u8_headers['User-Agent'] = get_userAgent()
            
            r = create_client_curl(headers=m3u8_headers).get(self.m3u8_url)
            r.raise_for_status()
            self.content = r.text
            return True
        except Exception as e:
            logger.error(f"Error fetching M3U8: {e}")
            return False
    
    def set_content(self, content: str) -> None:
        """Set M3U8 content directly (useful for avoiding redundant fetches)."""
        self.content = content

    def get_drm_info(self) -> Dict[str, List[Dict]]:
        """
        Extract PSSH data from M3U8 tags with DRM type information.
        
        Returns:
            Dict with structure: {"widevine": [{"pssh": ..., "type": "Widevine"}, ...], "playready": [...]}
        """
        if not self.content and not self.fetch():
            return {"widevine": [], "playready": []}

        wv_pssh = []
        pr_pssh = []
        seen_pssh = set()

        # Look for EXT-X-KEY or EXT-X-SESSION-KEY
        key_pattern = re.compile(r'#EXT-X-(?:SESSION-)?KEY:(.*?)URI="(data:.*?,([^"]+))"', re.IGNORECASE | re.DOTALL)
        matches = key_pattern.findall(self.content)
        
        for attributes, full_uri, b64_data in matches:
            try:
                console.print(f"[dim]M3U8Parser: Processing URI: {full_uri[:50]}...")
                is_wv = "edef8ba9-79d6-4ace-a3c8-27dcd51d21ed" in attributes.lower() or "edef8ba9-79d6-4ace-a3c8-27dcd51d21ed" in full_uri.lower()
                is_pr = "com.microsoft.playready" in attributes.lower() or "9a04f079-9840-4286-ab92-e65be0885f95" in attributes.lower() or "9a04f079-9840-4286-ab92-e65be0885f95" in full_uri.lower()
                b64_data_clean = b64_data.strip().split(',')[0].split(';')[0].split('"')[0]

                # Decode base64
                try:
                    decoded_data = base64.b64decode(b64_data_clean)
                except binascii.Error:
                    cleaned_b64 = re.sub(r'[^A-Za-z0-9+/=]', '', b64_data_clean)
                    decoded_data = base64.b64decode(cleaned_b64)
                
                # Check if it's a Widevine PSSH
                try:
                    PSSH(decoded_data)
                    console.print("[green]M3U8Parser: Detected Widevine PSSH")

                    if b64_data_clean not in seen_pssh:
                        wv_pssh.append({"pssh": b64_data_clean, "type": "Widevine"})
                        seen_pssh.add(b64_data_clean)
                    continue

                except Exception:
                    if is_wv:
                        logger.info("M3U8Parser: Tag indicates Widevine, adding data")
                        if b64_data_clean not in seen_pssh:
                            wv_pssh.append({"pssh": b64_data_clean, "type": "Widevine"})
                            seen_pssh.add(b64_data_clean)
                        continue

                # Check if it's a PlayReady PSSH
                try:
                    PR_PSSH(decoded_data)
                    logger.info("M3U8Parser: Detected PlayReady PSSH")
                    if b64_data_clean not in seen_pssh:
                        pr_pssh.append({"pssh": b64_data_clean, "type": "PlayReady"})
                        seen_pssh.add(b64_data_clean)
                    continue

                except Exception:
                    if is_pr:
                        logger.info("M3U8Parser: Tag indicates PlayReady, adding data")
                        if b64_data_clean not in seen_pssh:
                            pr_pssh.append({"pssh": b64_data_clean, "type": "PlayReady"})
                            seen_pssh.add(b64_data_clean)
                        continue
                
                logger.warning("M3U8Parser: Data decoded but not recognized as WV or PR PSSH")

            except Exception as e:
                logger.error(f"M3U8Parser: Error decoding b64 data: {e}")

        return {
            "widevine": wv_pssh,
            "playready": pr_pssh
        }

    def get_kids(self, widevine_pssh: List = None) -> List[str]:
        """
        Extract KIDs from M3U8 if available, or from the PSSH boxes.
        
        Args:
            widevine_pssh: List of PSSH data. Can be:
                - List[str]: Base64 PSSH strings (for backward compatibility)
                - List[Dict]: PSSH dicts with 'pssh' key (new format from get_drm_info)
        """
        kids = []

        # 1. Try to extract from the M3U8 content directly (KEYID attribute)
        keyid_matches = re.findall(r'KEYID=(0x[0-9a-fA-F]+|[0-9a-fA-F\-]{32,36})', self.content)
        for kid in keyid_matches:
            clean_kid = kid.replace('-', '').replace('0x', '').lower()
            if len(clean_kid) == 32:
                kids.append(clean_kid)

        # 2. Extract from provided PSSH boxes
        if widevine_pssh:
            for pssh_item in widevine_pssh:
                p_b64 = pssh_item.get('pssh') if isinstance(pssh_item, dict) else pssh_item
                
                try:
                    decoded = base64.b64decode(p_b64)
                    pssh_obj = PSSH(decoded)
                    if hasattr(pssh_obj, 'key_ids'):
                        for kid_bin in pssh_obj.key_ids:
                            if isinstance(kid_bin, bytes):
                                kids.append(kid_bin.hex())
                            else:
                                kids.append(str(kid_bin).lower().replace('-', ''))
                except Exception:
                    pass

        return list(set(kids))