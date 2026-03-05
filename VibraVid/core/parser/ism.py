# 13.02.26

import xml.etree.ElementTree as ET
from typing import Optional, List, Dict

from rich.console import Console
from pyplayready.system.pssh import PSSH as PR_PSSH
from pywidevine.pssh import PSSH as WV_PSSH

from VibraVid.utils.http_client import create_client_curl, get_userAgent


console = Console()


class DRMSystem:
    """DRM system constants and utilities."""
    WIDEVINE = 'widevine'
    PLAYREADY = 'playready'
    FAIRPLAY = 'fairplay'
    
    UUIDS = {
        WIDEVINE: 'edef8ba9-79d6-4ace-a3c8-27dcd51d21ed',
        PLAYREADY: '9a04f079-9840-4286-ab92-e65be0885f95',
        FAIRPLAY: '94ce86fb-07ff-4f43-adb8-93d2fa968ca2'
    }
    
    PLAYREADY_URNS = [
        'urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95',
        'urn:microsoft:playready'
    ]
    
    @classmethod
    def from_uuid(cls, uuid: str) -> Optional[str]:
        u = uuid.lower()
        return next((t for t, v in cls.UUIDS.items() if v in u), None)


class ISMParser:
    def __init__(self, ism_url: str = None, headers: Dict[str, str] = None, ism_file: str = None):
        """
        Initialize ISMParser
        
        Args:
            ism_url: ISM manifest URL (optional)
            headers: HTTP headers for URL requests
            ism_file: Path to local ISM manifest file (takes precedence over URL)
        """
        self.ism_url = ism_url
        self.headers = headers or {}
        self.ism_file = ism_file
        self.root = None
    
    def parse(self) -> bool:
        """Parse ISM manifest from file or URL."""
        try:
            if self.ism_file:
                return self.parse_from_file(self.ism_file)
            
            # Otherwise, download from URL
            if not self.ism_url:
                console.print("[red]Error: Neither ism_file nor ism_url provided[/red]")
                return False
            
            # Generate fresh User-Agent
            ism_headers = self.headers.copy()
            ism_headers['User-Agent'] = get_userAgent()
            
            console.print("[cyan]Downloading ISM manifest...[/cyan]")
            r = create_client_curl(headers=ism_headers).get(self.ism_url, timeout=10)
            r.raise_for_status()
            
            self.root = ET.fromstring(r.content)
            
            console.print("[green][OK] ISM manifest loaded[/green]")
            return True
        
        except Exception as e:
            console.print(f"[red]Error parsing ISM: {e}[/red]")
            return False
    
    def parse_from_file(self, file_path: str) -> bool:
        """Parse ISM manifest from a local file."""
        try:
            self.root = ET.parse(file_path).getroot()
            return True
        
        except Exception:
            # Only fallback to URL if file parsing fails AND we have a URL
            if self.ism_url:
                try:
                    ism_headers = self.headers.copy()
                    ism_headers['User-Agent'] = get_userAgent()
                    
                    r = create_client_curl(headers=ism_headers).get(self.ism_url, timeout=10)
                    r.raise_for_status()
                    
                    self.root = ET.fromstring(r.content)
                    return True
                except Exception:
                    return False
            return False
    
    def _find(self, element: ET.Element, path: str) -> Optional[ET.Element]:
        """Find element."""
        return element.find(path)
    
    def _findall(self, element: ET.Element, path: str) -> List[ET.Element]:
        """Find all elements."""
        return element.findall(path)
    
    def _get_drm_data(self) -> Dict[str, List[str]]:
        """Extract DRM data from Protection elements in root."""
        drm_data = {}
        
        if not self.root:
            return drm_data
        
        # Look for Protection element at root level
        protection = self.root.find('Protection')
        
        if protection is not None:
            system_id = protection.get('SystemID', '').lower()
            
            # Get ProtectionHeader (contains PSSH or PRO data)
            proto_header = protection.find('ProtectionHeader')
            if proto_header is not None and proto_header.text:
                if not system_id:
                    system_id = proto_header.get('SystemID', '').lower()
                
                # PlayReady UUID
                if '9a04f079' in system_id or system_id == '':

                    # For ISM, ProtectionHeader text contains the PSSH/PRO data
                    pssh_data = proto_header.text.strip()
                    if pssh_data:
                        try:

                            # Validate with pyplayready (PSSH data is base64 encoded)
                            PR_PSSH(pssh_data)
                            drm_data.setdefault(DRMSystem.PLAYREADY, []).append(pssh_data)
                        except Exception as e:
                            console.print(f"[yellow]Warning: PSSH validation failed but adding anyway: {str(e)[:100]}[/yellow]")
                            drm_data.setdefault(DRMSystem.PLAYREADY, []).append(pssh_data)
                
                # Widevine UUID
                elif 'edef8ba9' in system_id:
                    pssh_data = proto_header.text.strip()
                    if pssh_data:
                        try:
                            
                            # Validate with pywidevine
                            WV_PSSH(pssh_data)
                            drm_data.setdefault(DRMSystem.WIDEVINE, []).append(pssh_data)
                            console.print(f"[green][OK] Found Widevine protection ({len(pssh_data)} bytes PSSH)[/green]")
                            
                        except Exception as e:
                            # Still add it even if validation fails
                            console.print(f"[yellow]Warning: PSSH validation failed but adding anyway: {str(e)[:100]}[/yellow]")
                            drm_data.setdefault(DRMSystem.WIDEVINE, []).append(pssh_data)
        
        return drm_data
    
    def get_drm_info(self, drm_preference='playready'):
        """Extract DRM information from ISM manifest."""
        if not self.root:
            return {
                "available_drm_types": [],
                "selected_drm_type": None,
                "widevine_pssh": [],
                "playready_pssh": []
            }
        
        # Get DRM data
        drm_data = self._get_drm_data()
        
        # Prepare PSSH lists
        wv_pssh = []
        pr_pssh = []
        seen_wv = set()
        seen_pr = set()
        
        for pssh in drm_data.get(DRMSystem.WIDEVINE, []):
            if pssh not in seen_wv:
                seen_wv.add(pssh)
                wv_pssh.append({
                    'pssh': pssh,
                    'kid': 'N/A',
                    'type': DRMSystem.WIDEVINE
                })
        
        for pssh in drm_data.get(DRMSystem.PLAYREADY, []):
            if pssh not in seen_pr:
                seen_pr.add(pssh)
                pr_pssh.append({
                    'pssh': pssh,
                    'kid': 'N/A',
                    'type': DRMSystem.PLAYREADY
                })
        
        # Determine available DRM types
        available = []
        if wv_pssh:
            available.append(DRMSystem.WIDEVINE)
        if pr_pssh:
            available.append(DRMSystem.PLAYREADY)
        
        # Select DRM type
        selected = drm_preference if drm_preference in available else (available[0] if available else None)
        
        return {
            'available_drm_types': available,
            'selected_drm_type': selected,
            'widevine_pssh': wv_pssh,
            'playready_pssh': pr_pssh
        }