# 10.01.26

import base64
import xml.etree.ElementTree as ET
from typing import Optional, List, Dict, Tuple
from uuid import UUID

from rich.console import Console
from pywidevine.pssh import PSSH
from pyplayready.system.pssh import PSSH as PR_PSSH

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
    
    ABBREV = {
        WIDEVINE: 'WV',
        PLAYREADY: 'PR',
        FAIRPLAY: 'FP'
    }
    
    CENC_SCHEME = 'urn:mpeg:dash:mp4protection:2011'
    WIDEVINE_URN = 'urn:uuid:edef8ba9-79d6-4ace-a3c8-27dcd51d21ed'
    PLAYREADY_URNS = [
        'urn:uuid:9a04f079-9840-4286-ab92-e65be0885f95',
        'urn:microsoft:playready'
    ]
    
    @classmethod
    def get_uuid(cls, drm_type: str) -> Optional[str]:
        return cls.UUIDS.get(drm_type.lower())
    
    @classmethod
    def from_uuid(cls, uuid: str) -> Optional[str]:
        u = uuid.lower()
        return next((t for t, v in cls.UUIDS.items() if v in u), None)


class MPDParser:
    def __init__(self, mpd_url: str, headers: Dict[str, str] = None):
        self.mpd_url = mpd_url
        self.headers = headers or {}
        self.root = None
        self.namespace_map = {}
    
    def parse(self) -> bool:
        """Parse MPD from URL."""
        try:
            # Generate fresh User-Agent for MPD fetch
            mpd_headers = self.headers.copy()
            mpd_headers['User-Agent'] = get_userAgent()
            r = create_client_curl(headers=mpd_headers).get(self.mpd_url)
            r.raise_for_status()
            self.root = ET.fromstring(r.content)
            self._extract_namespaces()
            return True
        
        except Exception as e:
            console.print(f"[red]Error parsing MPD: {e}")
            return False
    
    def parse_from_file(self, file_path: str) -> bool:
        """Parse MPD from a local file."""
        try:
            self.root = ET.parse(file_path).getroot()
            self._extract_namespaces()
            return True
        
        except Exception:
            # Fallback to URL parsing
            return self.parse()
    
    def _extract_namespaces(self):
        """Extract and register namespaces from XML root."""
        self.namespace_map = {
            'mpd': 'urn:mpeg:dash:schema:mpd:2011',
            'cenc': 'urn:mpeg:cenc:2013',
            'mspr': 'urn:microsoft:playready'
        }
        
        # Register namespaces
        for prefix, uri in self.namespace_map.items():
            ET.register_namespace(prefix, uri)
    
    def _xpath(self, path: str) -> str:
        """Convert path with namespace prefixes to full namespace URIs."""
        for prefix, uri in self.namespace_map.items():
            path = path.replace(f'{prefix}:', f'{{{uri}}}')
        return path
    
    def _find(self, element: ET.Element, path: str) -> Optional[ET.Element]:
        """Find element with namespace handling."""
        return element.find(self._xpath(path), self.namespace_map)
    
    def _findall(self, element: ET.Element, path: str) -> List[ET.Element]:
        """Find all elements with namespace handling."""
        return element.findall(self._xpath(path), self.namespace_map)
    
    def _get_default_kid(self, element: ET.Element) -> Optional[str]:
        """Extract default_KID from ContentProtection elements."""
        for cp in self._findall(element, 'mpd:ContentProtection'):
            kid = (cp.get('{urn:mpeg:cenc:2013}default_KID') or cp.get('default_KID') or cp.get('kid'))
            if kid:
                return kid.lower().replace('-', '')

            # fallback: try to parse any Widevine PSSH for a key id
            pssh_text = cp.findtext(self._xpath('cenc:pssh'))
            if pssh_text and pssh_text.strip():
                try:
                    pssh_obj = PSSH(pssh_text.strip())
                    if pssh_obj.key_ids:
                        k = str(pssh_obj.key_ids[0])
                        return k.lower().replace('-', '')
                except Exception:
                    pass
        return None
    
    def _get_drm_data(self, element: ET.Element) -> Dict[str, List[str]]:
        """Extract DRM types and their PSSH data using pywidevine/pyplayready."""
        drm_data = {}
        
        for cp in self._findall(element, 'mpd:ContentProtection'):
            scheme = (cp.get('schemeIdUri') or '').lower()
            
            # Widevine
            if DRMSystem.WIDEVINE_URN in scheme:
                pssh_text = cp.findtext(self._xpath('cenc:pssh'))
                if pssh_text and pssh_text.strip():
                    try:
                        # Validate with pywidevine
                        pssh = PSSH(pssh_text.strip())
                        
                        # Extract KID if available
                        kid_attr = cp.get('kid') or cp.get('{urn:mpeg:cenc:2013}kid')
                        if kid_attr:
                            kid = UUID(bytes=base64.b64decode(kid_attr))

                            # Update PSSH with KID if missing
                            if not pssh.key_ids or all(k.int == 0 for k in pssh.key_ids):
                                pssh.set_key_ids([kid])
                        
                        drm_data.setdefault(DRMSystem.WIDEVINE, []).append(pssh_text.strip())
                    except Exception:
                        pass
            
            # PlayReady
            elif any(urn in scheme for urn in DRMSystem.PLAYREADY_URNS):

                # Try both pssh and pro elements
                pr_text = (cp.findtext(self._xpath('cenc:pssh')) or cp.findtext(self._xpath('mspr:pro')) or cp.findtext('pro'))
                
                if pr_text and pr_text.strip():
                    try:
                        # Validate with pyplayready
                        PR_PSSH(pr_text.strip())
                        drm_data.setdefault(DRMSystem.PLAYREADY, []).append(pr_text.strip())
                    except Exception:
                        pass
        
        return drm_data

    def _get_content_info(self, adapt_set: ET.Element) -> Tuple[str, str]:
        """Extract content type and language from adaptation set."""
        c_type = (adapt_set.get('contentType') or adapt_set.get('mimeType') or '').lower()
        content_type = 'video' if 'video' in c_type else 'audio' if 'audio' in c_type else 'image' if 'image' in c_type else 'text' if 'text' in c_type else 'N/A'
        lang = adapt_set.get('lang', 'N/A')
        return content_type, lang
    
    def get_adaptation_sets_info(self, selected_ids=None, selected_kids=None, selected_langs=None, selected_periods=None):
        """Get information about all AdaptationSets."""
        if not self.root:
            return []
        
        adaptation_sets = []
        
        # Normalize filters
        norm_ids = [str(i) for i in (selected_ids or [])]
        norm_kids = [k.lower().replace('-', '') for k in (selected_kids or []) if k]
        norm_langs = [lang.lower() for lang in (selected_langs or []) if lang]
        norm_periods = [str(p) for p in (selected_periods or []) if p]
        
        for period in self._findall(self.root, 'mpd:Period'):
            period_id = period.get('id')
            
            # Filter by period
            if norm_periods and period_id and period_id not in norm_periods:
                continue
            
            for adapt_set in self._findall(period, 'mpd:AdaptationSet'):
                content_type, lang = self._get_content_info(adapt_set)
                
                # Skip non-media types (keep text for external sub support if present)
                if content_type == 'image':
                    continue
                
                # Apply filters
                if not self._matches_filters(adapt_set, content_type, lang, norm_ids, norm_kids, norm_langs):
                    continue
                
                # Extract info
                info = self._extract_adaptation_set_info(adapt_set, content_type, lang, norm_ids)
                adaptation_sets.append(info)
        
        return adaptation_sets
    
    def _matches_filters(self, adapt_set, content_type, lang, selected_ids, selected_kids, selected_langs):
        """Check if adaptation set matches filter criteria."""
        adapt_id = adapt_set.get('id', 'N/A')
        rep_ids = [rep.get('id') for rep in self._findall(adapt_set, 'mpd:Representation')]
        
        # ID filter
        if selected_ids:
            if not (adapt_id in selected_ids or any(rid in selected_ids for rid in rep_ids)):
                return False
        
        # KID filter
        if selected_kids:
            adapt_kids = [self._get_default_kid(adapt_set)]
            adapt_kids.extend(self._get_default_kid(rep) for rep in self._findall(adapt_set, 'mpd:Representation'))
            norm_adapt_kids = [k.lower().replace('-', '') for k in adapt_kids if k]
            if not any(tk in norm_adapt_kids for tk in selected_kids):
                return False
        
        # Language filter
        if selected_langs and content_type == 'audio':
            if lang.lower() not in selected_langs:
                return False
        
        return True
    
    def _extract_adaptation_set_info(self, adapt_set, content_type, lang, selected_ids=None):
        """Extract detailed information from adaptation set."""
        default_kid = self._get_default_kid(adapt_set)
        
        # If a specific Representation was selected, extract its KID instead
        if selected_ids:
            for rep in self._findall(adapt_set, 'mpd:Representation'):
                rep_id = rep.get('id', 'N/A')
                if rep_id in selected_ids:
                    rep_kid = self._get_default_kid(rep)
                    if rep_kid:
                        default_kid = rep_kid
                    break
        
        # Combine PSSH from AdaptationSet and Representations
        pssh_map = self._get_drm_data(adapt_set)
        for rep in self._findall(adapt_set, 'mpd:Representation'):
            rep_pssh = self._get_drm_data(rep)
            for drm_type, psshs in rep_pssh.items():
                pssh_map.setdefault(drm_type, []).extend(psshs)
        
        # Deduplicate
        for drm_type in pssh_map:
            pssh_map[drm_type] = list(dict.fromkeys(pssh_map[drm_type]))

            # If we still don't have a default kid we can try to extract it from any available PSSH box.
            if not default_kid:
                for drm_type, psshs in pssh_map.items():
                    for p in psshs:
                        try:
                            parsed = PSSH(p)
                            if parsed.key_ids:
                                default_kid = str(parsed.key_ids[0]).lower().replace('-', '')
                                break
                        except Exception:
                            pass
                    if default_kid:
                        break
            
        # Get video height
        height = None
        if content_type == 'video':
            heights = []
            for rep in self._findall(adapt_set, 'mpd:Representation'):
                h = rep.get('height')
                if h:
                    try:
                        heights.append(int(h))
                    except ValueError:
                        pass
            
            height = max(heights) if heights else None
        
        return {
            'id': adapt_set.get('id', 'N/A'),
            'content_type': content_type,
            'language': lang,
            'default_kid': default_kid,
            'drm_types': list(pssh_map.keys()),
            'pssh_map': pssh_map,
            'is_protected': bool(pssh_map),
            'height': height
        }
    
    def print_adaptation_sets_info(self, selected_ids=None, selected_kids=None, selected_langs=None, selected_periods=None):
        """Print AdaptationSets information."""
        sets = self.get_adaptation_sets_info(selected_ids, selected_kids, selected_langs, selected_periods)
        
        if not sets:
            return
        
        # Group by content type
        groups = {}
        for s in sets:
            groups.setdefault(s['content_type'], []).append(s)
        
        for c_type, items in groups.items():
            has_filter = any([selected_ids, selected_kids, selected_langs])
            is_uniform = len({i['default_kid'] for i in items}) == 1 and not has_filter
            
            seen = set()
            for item in ([items[0]] if is_uniform else items):
                kid = item['default_kid'] or 'Not found'
                prot = ', '.join(item['drm_types']) if item['drm_types'] else 'No'
                
                if is_uniform:
                    label = f"all {c_type}"
                else:
                    parts = [c_type]
                    if item.get('height'):
                        parts.append(f"{item['height']}p")
                    if item.get('language') != 'N/A':
                        parts.append(f"({item['language']})")
                    label = " ".join(parts)
                
                key = f"{label}_{kid}"
                if key in seen:
                    continue
                seen.add(key)
                
                if prot == 'No':
                    continue

                if "text (" not in label:  # Don't print text tracks to avoid clutter (they usually don't have DRM)
                    console.print(f"    [red]- {label}[white], [cyan]Kid: [yellow]{kid}, [cyan]Protection: [yellow]{prot}")
    
    def get_drm_info(self, drm_preference, selected_ids=None, selected_kids=None, selected_langs=None, selected_periods=None):
        """Extract DRM information from MPD."""
        if not self.root:
            return {
                "available_drm_types": [],
                "selected_drm_type": None,
                "widevine_pssh": [],
                "playready_pssh": []
            }
        
        # Get matched adaptation sets
        matched_sets = self.get_adaptation_sets_info(selected_ids, selected_kids, selected_langs, selected_periods)
        
        # Collect PSSH data
        wv_pssh = []
        pr_pssh = []
        seen_wv = set()
        seen_pr = set()
        
        for info in matched_sets:
            pssh_map = info.get('pssh_map', {})

            # Build human-readable label for this adaptation set
            c_type = info.get('content_type', '')
            lang = info.get('language', 'N/A')
            height = info.get('height')
            if c_type == 'video' and height:
                track_label = f"video {height}p"
            elif c_type == 'audio':
                track_label = f"audio ({lang})" if lang and lang != 'N/A' else "audio"
            else:
                track_label = c_type or None

            # Widevine PSSH
            for pssh in pssh_map.get(DRMSystem.WIDEVINE, []):
                if pssh not in seen_wv:
                    seen_wv.add(pssh)
                    kid_val = info.get('default_kid')
                    if not kid_val or kid_val.lower() in ('n/a', ''):
                        try:
                            parsed = PSSH(pssh)
                            if parsed.key_ids:
                                kid_val = str(parsed.key_ids[0]).lower().replace('-', '')
                        except Exception:
                            pass
                        
                    wv_pssh.append({
                        'pssh': pssh,
                        'kid': kid_val or 'N/A',
                        'type': DRMSystem.WIDEVINE,
                        'label': track_label,
                    })
            
            # PlayReady PSSH
            for pssh in pssh_map.get(DRMSystem.PLAYREADY, []):
                if pssh not in seen_pr:
                    seen_pr.add(pssh)
                    pr_pssh.append({
                        'pssh': pssh,
                        'kid': info.get('default_kid') or 'N/A',
                        'type': DRMSystem.PLAYREADY,
                        'label': track_label,
                    })
        
        # Determine available DRM types
        available = []
        if wv_pssh:
            available.append(DRMSystem.WIDEVINE)
        if pr_pssh:
            available.append(DRMSystem.PLAYREADY)
        
        # Select DRM type
        selected = drm_preference if drm_preference in available else (available[0] if available else None)
        self.print_adaptation_sets_info(selected_ids, selected_kids, selected_langs, selected_periods)
        
        return {
            'available_drm_types': available,
            'selected_drm_type': selected,
            'widevine_pssh': wv_pssh,
            'playready_pssh': pr_pssh
        }