# 19.05.25

import re
import logging
from urllib.parse import urlparse, urljoin

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

from ..utils.object import Stream, Segment

logger = logging.getLogger(__name__)
TIMEOUT = config_manager.config.get_int('REQUESTS', 'timeout')


class HLSParser:
    def __init__(self, m3u8_url, headers=None, provided_kid=None):
        self.m3u8_url = m3u8_url
        self.headers = headers or get_headers()
        self.provided_kid = provided_kid        # NOT USE
        self.base_url = self._get_base_url()
        self.master_content = None
        
    def _get_base_url(self):
        parsed = urlparse(self.m3u8_url)
        path = parsed.path.rsplit('/', 1)[0]
        return f"{parsed.scheme}://{parsed.netloc}{path}/"
    
    def fetch_manifest(self):
        logger.info(f"Fetching M3U8: {self.m3u8_url}")
        
        try:
            with create_client(headers=self.headers, timeout=TIMEOUT, follow_redirects=True) as client:
                response = client.get(self.m3u8_url)
                response.raise_for_status()
                self.master_content = response.text
                return True
                
        except Exception as e:
            logger.error(f"Failed to fetch M3U8: {e}")
            return False
    
    def parse_streams(self):
        streams = []
        lines = self.master_content.split('\n')
        i = 0
        
        while i < len(lines):
            line = lines[i].strip()
            
            if line.startswith('#EXT-X-STREAM-INF:'):
                stream = self._parse_stream_inf(line)
                if i + 1 < len(lines):
                    stream_url = lines[i + 1].strip()
                    if stream_url and not stream_url.startswith('#'):
                        stream.playlist_url = urljoin(self.base_url, stream_url)
                        streams.append(stream)
                i += 2
                continue
            
            elif line.startswith('#EXT-X-MEDIA:') and 'TYPE=AUDIO' in line:
                stream = self._parse_media_tag(line, 'audio')
                if stream:
                    streams.append(stream)
            
            elif line.startswith('#EXT-X-MEDIA:') and 'TYPE=SUBTITLES' in line:
                stream = self._parse_media_tag(line, 'subtitle')
                if stream:
                    streams.append(stream)
            
            i += 1
        
        return streams
    
    def _parse_stream_inf(self, line):
        stream = Stream('video')
        
        bandwidth_match = re.search(r'BANDWIDTH=(\d+)', line)
        if bandwidth_match:
            stream.bitrate = int(bandwidth_match.group(1))
        
        resolution_match = re.search(r'RESOLUTION=(\d+x\d+)', line)
        if resolution_match:
            stream.resolution = resolution_match.group(1)
            width, height = stream.resolution.split('x')
            stream.width = int(width)
            stream.height = int(height)
        
        fps_match = re.search(r'FRAME-RATE=([\d.]+)', line)
        if fps_match:
            stream.fps = fps_match.group(1)
        
        codecs_match = re.search(r'CODECS="([^"]+)"', line)
        if codecs_match:
            stream.codecs = codecs_match.group(1)
        
        return stream
    
    def _parse_media_tag(self, line, stream_type):
        stream = Stream(stream_type)
        
        lang_match = re.search(r'LANGUAGE="([^"]+)"', line)
        if lang_match:
            stream.language = lang_match.group(1)
        
        name_match = re.search(r'NAME="([^"]+)"', line)
        if name_match:
            stream.name = name_match.group(1)
        
        uri_match = re.search(r'URI="([^"]+)"', line)
        if uri_match:
            stream.playlist_url = urljoin(self.base_url, uri_match.group(1))
            return stream
        
        return None
    
    def fetch_segments(self, playlist_url):
        try:
            with create_client(headers=self.headers, timeout=TIMEOUT, follow_redirects=True) as client:
                response = client.get(playlist_url)
                response.raise_for_status()
                content = response.text
                
                # Check if it's a direct subtitle file
                if "WEBVTT" in content[:100]:
                    return [Segment(playlist_url, 1, 'media')], None, None, None, None, 0
                
                base_url = playlist_url.rsplit('/', 1)[0] + '/'
                segments = []
                bandwidth = None
                encryption_method = None
                key_uri = None
                iv = None
                total_duration = 0
                
                lines = content.split('\n')
                for i, line in enumerate(lines):
                    line = line.strip()
                    
                    if line.startswith('#EXT-X-STREAM-INF:'):
                        bandwidth_match = re.search(r'BANDWIDTH=(\d+)', line)
                        if bandwidth_match:
                            bandwidth = int(bandwidth_match.group(1))
                    
                    elif line.startswith('#EXT-X-KEY:'):
                        method_match = re.search(r'METHOD=([^,]+)', line)
                        uri_match = re.search(r'URI="([^"]+)"', line)
                        iv_match = re.search(r'IV=0x([0-9a-fA-F]+)', line)
                        if method_match and uri_match:
                            encryption_method = method_match.group(1)
                            key_uri = urljoin(base_url, uri_match.group(1))
                            iv = iv_match.group(1) if iv_match else None
                            logger.info(f"Found encryption: {encryption_method}, key: {key_uri}, IV: {iv}")
                    
                    elif line.startswith('#EXTINF:'):
                        # Extract duration
                        duration_match = re.search(r'#EXTINF:([\d.]+)', line)
                        if duration_match:
                            total_duration += float(duration_match.group(1))
                        
                        if i + 1 < len(lines):
                            segment_url = lines[i + 1].strip()
                            if segment_url and not segment_url.startswith('#'):
                                full_url = urljoin(base_url, segment_url)
                                segments.append(Segment(full_url, len(segments) + 1, 'media'))
                
                # Fallback for subtitles without #EXTINF
                if not segments:
                    for line in content.split('\n'):
                        line = line.strip()
                        if line and not line.startswith('#'):
                            full_url = urljoin(base_url, line)
                            segments.append(Segment(full_url, len(segments) + 1, 'media'))
                
                logger.info(f"Found {len(segments)} segments, duration: {total_duration:.1f}s")
                return segments, bandwidth, encryption_method, key_uri, iv, total_duration
                
        except Exception as e:
            logger.error(f"Failed to fetch media playlist: {e}")
            return [], None, None, None, None, 0