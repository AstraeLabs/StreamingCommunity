# 04.01.25

import os
import json
from typing import List, Tuple

from rich.console import Console

from ..utils.object import StreamInfo 


console = Console()


class LogParser:
    def __init__(self, show_warnings: bool = True, show_errors: bool = True):
        self.warnings = []
        self.errors = []
        self.show_warnings = show_warnings
        self.show_errors = show_errors
    
    def parse_line(self, line: str) -> Tuple[bool, bool]:
        """Parse a log line, return (has_warning, has_error)"""
        line = line.strip()
        
        if 'WARN' in line.upper(): 
            self.warnings.append(line)
            if self.show_warnings and 'Response' in str(line):
                console.print(f"N_M3U8[yellow] - {line}")

        if 'ERROR' in line.upper():
            self.errors.append(line)
            if self.show_errors:
                console.print(f"N_M3U8[red] - {line}")

        return 'WARN' in line.upper(), 'ERROR' in line.upper()


def _is_image_track(s: dict) -> bool:
    """Return True for thumbnail/image sprite tracks (e.g. GroupId 'images_1', 'thumb_...')."""
    gid = str(s.get("GroupId", "")).lower()
    return gid.startswith("image") or gid.startswith("thumb")


def create_key(s):
    """Create a unique key for a stream from meta.json data"""
    if _is_image_track(s):
        return f"IMAGE|{s.get('GroupId','')}|{s.get('Bandwidth',0)}"

    if "Resolution" in s and s.get("Resolution"): 
        return f"VIDEO|{s.get('Resolution','')}|{s.get('Bandwidth',0)}|{s.get('Codecs','')}|{s.get('FrameRate','')}|{s.get('VideoRange','')}"

    if s.get("MediaType") == "AUDIO": 
        return f"AUDIO|{s.get('Language','')}|{s.get('Name','')}|{s.get('Bandwidth',0)}|{s.get('Codecs','')}|{s.get('Channels','')}"

    return f"SUBTITLE|{s.get('Language','')}|{s.get('Name','')}|{s.get('Role','')}"


def classify_stream(s):
    """Classify stream type based on meta.json data"""
    if _is_image_track(s):
        return "Image"
    
    # Check MediaType
    media_type = s.get("MediaType", "").upper()
    if media_type == "AUDIO":
        return "Audio"
    elif media_type == "SUBTITLES":
        return "Subtitle"
    elif media_type == "VIDEO":
        return "Video"
    
    # Fallback: if has Resolution, it's Video
    if "Resolution" in s and s.get("Resolution"):
        return "Video"
    
    # Default to Video for unknown types
    return "Video"


def parse_meta_json(json_path: str, selected_json_path: str) -> List[StreamInfo]:
    """Parse meta.json and meta_selected.json to determine which streams are selected"""
    if not os.path.exists(json_path):
        return []

    with open(json_path, 'r', encoding='utf-8-sig') as f: 
        metadata = json.load(f)
        
    selected_map = {}
    if selected_json_path and os.path.isfile(selected_json_path):
        with open(selected_json_path, 'r', encoding='utf-8-sig') as f:
            for s in json.load(f):

                selected_map[create_key(s)] = {
                    'extension': s.get("Extension", ""),
                    'duration': s.get("Playlist", {}).get("TotalDuration", 0),
                    'segments': s.get("SegmentsCount", 0)
                }
    
    streams = []
    seen_keys = {}
    for s in metadata:
        key = create_key(s)
        bw = s.get('Bandwidth', 0)
        
        if key in seen_keys:
            idx = seen_keys[key]
            streams[idx].total_duration += s.get("Playlist", {}).get("TotalDuration", 0)
            continue
            
        seen_keys[key] = len(streams)
        bw_str = f"{bw/1e6:.1f} Mbps" if bw >= 1e6 else (f"{bw/1e3:.0f} Kbps" if bw >= 1e3 else f"{bw:.0f} bps")
        
        sel = key in selected_map
        det = selected_map.get(key, {})
        st_type = classify_stream(s)
        
        streams.append(StreamInfo(
            type_=st_type,
            resolution=s.get("Resolution", ""),
            language=s.get("Language", ""),
            name=s.get("Name", ""),
            bandwidth="N/A" if st_type == "Subtitle" else bw_str,
            raw_bandwidth=bw,
            codec=s.get("Codecs", ""),
            selected=sel,
            extension=det.get('extension', s.get("Extension", "")),
            total_duration=det.get('duration', s.get("Playlist", {}).get("TotalDuration", 0)),
            frame_rate=s.get('FrameRate', 0),
            channels=s.get('Channels', '').replace('CH', ''),
            role=s.get('Role', ''),
            group_id=s.get("GroupId", "") or s.get("id", ""),
            descriptor="N_m3u8",
        ))
        
    return streams