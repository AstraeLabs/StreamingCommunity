# 19.05.25

from .drm_info import DRMInfo


class Segment:
    def __init__(self, url, number, seg_type='media'):
        self.url = url
        self.number = number
        self.type = seg_type
        self.size = 0
        self.downloaded = False
    
    def __repr__(self):
        return f"Segment({self.number}, {self.type})"


class Stream:
    def __init__(self, stream_type, stream_id=None):
        self.type = stream_type
        self.id = stream_id
        self.segments = []
        self.bitrate = 0
        self.language = 'und'
        self.resolution = 'unknown'
        self.width = 0
        self.height = 0
        self.fps = 'unknown'
        self.codecs = 'unknown'
        self.name = 'unknown'
        self.role = 'main'
        self.drm = DRMInfo()
        self.encryption_method = None
        self.key_uri = None
        self.key_data = None
        self.iv = None
        self.selected = False
        self.duration = 0
        self.playlist_url = None
    
    def add_segment(self, segment):
        self.segments.append(segment)
    
    def get_description(self):
        if self.type == 'video':
            return f"video_{self.resolution}"
        elif self.type == 'audio':
            return f"audio_{self.language}"
        elif self.type == 'image':
            return f"thumbnail_{self.resolution}"
        else:
            return f"subtitle_{self.language}"
    
    def get_type_display(self):
        """Get type string for table"""
        if self.type == 'video':
            return 'Video'
        elif self.type == 'audio':
            return 'Audio'
        elif self.type == 'image':
            return 'Thumbnail'
        else:
            return 'Subtitle'
    
    def get_duration_display(self):
        """Get duration display string"""
        if self.duration > 0:
            minutes = int(self.duration // 60)
            seconds = int(self.duration % 60)
            return f"{minutes:02d}:{seconds:02d}"
        return "-"
    
    def __repr__(self):
        drm_str = f", {self.drm.drm_type}" if self.drm.is_encrypted() else ""
        return f"Stream({self.type}, {self.get_description()}, {len(self.segments)} segments{drm_str})"