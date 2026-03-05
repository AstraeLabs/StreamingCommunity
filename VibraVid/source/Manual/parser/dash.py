# 19.05.25

import re
import logging
from urllib.parse import urlparse, urljoin

import xml.etree.ElementTree as ET

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

from ..utils.drm_info import DRMInfo
from ..utils.object import Stream, Segment


logger = logging.getLogger(__name__)
TIMEOUT = config_manager.config.get_int('REQUESTS', 'timeout')


class DashParser:
    def __init__(self, mpd_url, headers=None, provided_kid=None):
        self.mpd_url = mpd_url
        self.headers = headers or get_headers()
        self.provided_kid = provided_kid
        self.base_url = self._get_base_url()
        self.mpd_content = None
        self.root = None
        self.ns = {
            'mpd': 'urn:mpeg:dash:schema:mpd:2011',
            'cenc': 'urn:mpeg:cenc:2013'
        }
        
    def _get_base_url(self):
        parsed = urlparse(self.mpd_url)
        path = parsed.path.rsplit('/', 1)[0]
        return f"{parsed.scheme}://{parsed.netloc}{path}/"
    
    def fetch_manifest(self):
        try:
            with create_client(headers=self.headers, timeout=TIMEOUT, follow_redirects=True) as client:
                response = client.get(self.mpd_url)
                response.raise_for_status()
                self.mpd_content = response.text
                self.root = ET.fromstring(self.mpd_content)
                return True
                
        except Exception as e:
            logger.error(f"Failed to fetch MPD: {e}")
            return False
    
    def parse_streams(self):
        streams = []
        
        # Get media presentation duration
        duration_str = self.root.get('mediaPresentationDuration')
        media_duration = self._parse_duration(duration_str) if duration_str else 0
        
        for adapt_set in self.root.findall('.//mpd:AdaptationSet', self.ns):
            content_type = adapt_set.get('contentType') or adapt_set.get('mimeType', '')
            
            if 'video' in content_type:
                stream_type = 'video'
            elif 'audio' in content_type:
                stream_type = 'audio'
            elif 'text' in content_type or 'subtitle' in content_type:
                stream_type = 'subtitle'
            elif 'image' in content_type:
                stream_type = 'image'
            else:
                continue
            
            adaptation_drm = self._extract_drm_from_element(adapt_set)
            
            for rep in adapt_set.findall('.//mpd:Representation', self.ns):
                stream = self._parse_representation(rep, adapt_set, stream_type)
                if stream:
                    role_elem = adapt_set.find('.//mpd:Role', self.ns)
                    if role_elem is not None:
                        stream.role = role_elem.get('value', 'main')
                    
                    stream.duration = media_duration
                    
                    rep_drm = self._extract_drm_from_element(rep)
                    
                    if rep_drm['pssh'] or rep_drm['kid']:
                        stream.drm = rep_drm['drm_info']
                    elif adaptation_drm['pssh'] or adaptation_drm['kid']:
                        stream.drm = adaptation_drm['drm_info']
                    
                    streams.append(stream)

        return streams
    
    def _parse_duration(self, duration_str):
        """Parse ISO 8601 duration (PT1H2M3.456S) to seconds"""
        try:
            match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?', duration_str)
            if match:
                hours = int(match.group(1) or 0)
                minutes = int(match.group(2) or 0)
                seconds = float(match.group(3) or 0)
                return hours * 3600 + minutes * 60 + seconds
        except Exception:
            pass
        return 0
    
    def _extract_drm_from_element(self, element):
        drm_info = DRMInfo()
        pssh = None
        kid = None
        
        for cp in element.findall('.//mpd:ContentProtection', self.ns):
            scheme_id_uri = cp.get('schemeIdUri')
            if scheme_id_uri:
                drm_info.set_method(scheme_id_uri)
            
            pssh_elem = cp.find('.//cenc:pssh', self.ns)
            if pssh_elem is not None and pssh_elem.text:
                pssh = pssh_elem.text.strip()
                drm_info.set_pssh(pssh)
            
            default_kid = cp.get('{urn:mpeg:cenc:2013}default_KID')
            if default_kid:
                kid = default_kid.replace('-', '').lower()
                drm_info.set_kid(kid)
                drm_info.default_kid = kid
        
        # Use provided KID if no KID found in manifest and provided_kid is available
        if not kid and self.provided_kid:
            drm_info.set_kid(self.provided_kid)
            kid = self.provided_kid
        
        return {
            'drm_info': drm_info,
            'pssh': pssh,
            'kid': kid
        }
    
    def _parse_representation(self, rep, adapt_set, stream_type):
        rep_id = rep.get('id', 'unknown')
        bandwidth = int(rep.get('bandwidth', 0))
        
        stream = Stream(stream_type, rep_id)
        stream.bitrate = bandwidth
        
        if stream_type == 'video':
            stream.width = int(rep.get('width', 0))
            stream.height = int(rep.get('height', 0))
            stream.resolution = f"{stream.width}x{stream.height}"
            stream.fps = rep.get('frameRate', 'unknown')
            stream.codecs = rep.get('codecs') or adapt_set.get('codecs', 'unknown')

        elif stream_type == 'audio':
            stream.language = adapt_set.get('lang', 'und')
            stream.codecs = rep.get('codecs') or adapt_set.get('codecs', 'unknown')

        elif stream_type == 'subtitle':
            stream.language = adapt_set.get('lang', 'und')
            stream.codecs = rep.get('codecs') or adapt_set.get('codecs', 'vtt')
        
        segment_template = rep.find('.//mpd:SegmentTemplate', self.ns)
        if segment_template is None:
            segment_template = adapt_set.find('.//mpd:SegmentTemplate', self.ns)
        
        if segment_template is not None:
            self._parse_segment_template(segment_template, rep_id, stream)
        
        return stream
    
    def _parse_segment_template(self, template, rep_id, stream):
        initialization = template.get('initialization', '')
        media = template.get('media', '')
        start_number = int(template.get('startNumber', 1))
        
        initialization = initialization.replace('$RepresentationID$', rep_id)
        media = media.replace('$RepresentationID$', rep_id)
        
        if initialization:
            init_url = urljoin(self.base_url, initialization)
            stream.add_segment(Segment(init_url, 0, 'init'))
        
        timeline = template.find('.//mpd:SegmentTimeline', self.ns)
        if timeline is not None:
            segment_num = start_number
            current_time = 0
            uses_time_template = '$Time$' in media
            
            for s in timeline.findall('.//mpd:S', self.ns):
                t = s.get('t')
                if t is not None:
                    current_time = int(t)
                
                duration = int(s.get('d', 0))
                repeat = int(s.get('r', 0))
                
                for _ in range(repeat + 1):
                    if uses_time_template:
                        segment_url = media.replace('$Time$', str(current_time))
                    else:
                        segment_url = media.replace('$Number$', str(segment_num))
                    
                    segment_url = urljoin(self.base_url, segment_url)
                    stream.add_segment(Segment(segment_url, segment_num, 'media'))
                    
                    current_time += duration
                    segment_num += 1