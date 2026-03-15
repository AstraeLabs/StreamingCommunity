# 02.02.26

import json
import subprocess
import logging
from pathlib import Path
from math import gcd

from VibraVid.setup import get_ffprobe_path


logger = logging.getLogger(__name__)


class NFOGenerator:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        self.data = None
        self.format_info = {}
        self.streams = []
        
    def _run_ffprobe(self):
        """Execute ffprobe and parse JSON output."""
        cmd = [
            get_ffprobe_path(),
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(self.file_path)
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            self.data = json.loads(result.stdout)
            self.format_info = self.data.get("format", {})
            self.streams = self.data.get("streams", [])
            return True
        except Exception as e:
            logger.error(f"FFprobe error: {e}")
            return False
    
    @staticmethod
    def format_size(bytes_size):
        """Convert bytes to human-readable format."""
        try:
            size = float(bytes_size)
            if size >= 1073741824:
                return f"{size / 1073741824:.1f} GiB"
            elif size >= 1048576:
                return f"{size / 1048576:.1f} MiB"
            else:
                return f"{size / 1024:.1f} KiB"
        except (ValueError, TypeError):
            return "N/A"
    
    @staticmethod
    def format_duration(seconds):
        """Convert seconds to 'X h XX min' format."""
        try:
            seconds = int(float(seconds))
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours} h {minutes} min" if hours > 0 else f"{minutes} min {seconds % 60} s"
        except (ValueError, TypeError):
            return "N/A"
    
    @staticmethod
    def format_bitrate(bitrate):
        """Convert bitrate to Mb/s or kb/s."""
        try:
            rate = float(bitrate)
            return f"{rate / 1000000:.1f} Mb/s" if rate >= 1000000 else f"{rate / 1000:.0f} kb/s"
        except (ValueError, TypeError):
            return "N/A"
    
    @staticmethod
    def parse_frame_rate(rate_str):
        """Parse frame rate string to decimal."""
        if not rate_str or "/" not in rate_str:
            return "N/A"
        try:
            num, den = map(float, rate_str.split("/"))
            fps = num / den
            # Check for common frame rates
            if abs(fps - 23.976) < 0.01:
                return "23.976 (24000/1001) FPS"
            elif abs(fps - 29.97) < 0.01:
                return "29.970 (30000/1001) FPS"
            else:
                return f"{fps:.3f} FPS" if fps != int(fps) else f"{int(fps)} FPS"
        except (ValueError, ZeroDivisionError):
            return "N/A"
    
    @staticmethod
    def get_aspect_ratio(width, height):
        """Calculate display aspect ratio."""
        try:
            w, h = int(width), int(height)
            ratio = w / h
            
            # Common cinematic/TV ratios
            if abs(ratio - 2.39) < 0.02:
                return "2.39:1"
            elif abs(ratio - 2.35) < 0.02:
                return "2.35:1"
            elif abs(ratio - 1.78) < 0.02:
                return "16:9"
            elif abs(ratio - 1.33) < 0.02:
                return "4:3"
            else:
                divisor = gcd(w, h)
                return f"{w // divisor}:{h // divisor}"
        except (ValueError, TypeError, ZeroDivisionError):
            return "N/A"
    
    def _get_hdr_format(self, stream):
        """Detect HDR format (Dolby Vision, HDR10, HDR10+)."""
        hdr_types = []
        
        # Check side data for Dolby Vision
        for side_data in stream.get("side_data_list", []):
            sd_type = side_data.get("side_data_type", "")
            if "DOVI" in sd_type or "Dolby" in sd_type:
                hdr_types.append("Dolby Vision")
                break
        
        # Check color transfer for HDR10
        color_transfer = stream.get("color_transfer", "")
        if "smpte2084" in color_transfer.lower():
            hdr_types.append("HDR10")
        
        return " / ".join(hdr_types) if hdr_types else None
    
    def _get_color_info(self, stream):
        """Extract color space information."""
        info = {}
        
        # Color space
        if color_space := stream.get("color_space"):
            info["Color space"] = color_space.upper()
        
        # Chroma subsampling
        pix_fmt = stream.get("pix_fmt", "")
        if "420" in pix_fmt:
            info["Chroma subsampling"] = "4:2:0"
        elif "422" in pix_fmt:
            info["Chroma subsampling"] = "4:2:2"
        elif "444" in pix_fmt:
            info["Chroma subsampling"] = "4:4:4"
        
        # Bit depth
        if "10" in pix_fmt or stream.get("bits_per_raw_sample") == "10":
            info["Bit depth"] = "10 bits"
        elif "8" in pix_fmt or stream.get("bits_per_raw_sample") == "8":
            info["Bit depth"] = "8 bits"
        
        # Color range
        color_range = stream.get("color_range", "")
        if color_range == "tv":
            info["Color range"] = "Limited"
        elif color_range == "pc":
            info["Color range"] = "Full"
        
        # Color primaries
        color_primaries = stream.get("color_primaries", "")
        primaries_map = {"bt2020": "BT.2020", "bt709": "BT.709"}
        if color_primaries in primaries_map:
            info["Color primaries"] = primaries_map[color_primaries]
        
        # Transfer characteristics
        transfer_map = {"smpte2084": "PQ", "bt709": "BT.709", "arib-std-b67": "HLG"}
        if color_transfer := stream.get("color_transfer"):
            info["Transfer characteristics"] = transfer_map.get(color_transfer, color_transfer)
        
        return info
    
    def _format_audio_channels(self, stream):
        """Format audio channel configuration."""
        channels = stream.get("channels")
        channel_layout = stream.get("channel_layout", "")
        
        layout_map = {
            "5.1": "L R C LFE Ls Rs",
            "5.1(side)": "L R C LFE Ls Rs",
            "7.1": "L R C LFE Ls Rs Lrs Rrs",
            "stereo": "L R",
            "mono": "C"
        }
        return layout_map.get(channel_layout, f"{channels} channels" if channels else "N/A")

    def _build_general_section(self):
        """Build general information section."""
        lines = [
            "=" * 80,
            "GENERAL",
            "=" * 80,
            f"Complete name                            : {self.file_path.name}",
        ]
        
        # Format
        format_name = self.format_info.get("format_long_name", self.format_info.get("format_name", "Unknown"))
        lines.append(f"Format                                   : {format_name}")
        
        # File size
        file_size = self.file_path.stat().st_size
        lines.append(f"File size                                : {self.format_size(file_size)}")
        
        # Duration
        if duration := self.format_info.get("duration"):
            lines.append(f"Duration                                 : {self.format_duration(duration)}")
        
        # Overall bitrate
        if bitrate := self.format_info.get("bit_rate"):
            lines.append(f"Overall bit rate                         : {self.format_bitrate(bitrate)}")
        
        # Frame rate (from video stream)
        video_stream = next((s for s in self.streams if s.get("codec_type") == "video"), None)
        if video_stream and (frame_rate := video_stream.get("r_frame_rate")):
            lines.append(f"Frame rate                               : {self.parse_frame_rate(frame_rate)}")
        
        # Encoded date
        tags = self.format_info.get("tags", {})
        if creation_time := tags.get("creation_time"):
            lines.append(f"Encoded date                             : {creation_time}")
        
        # Writing application
        if encoder := tags.get("encoder"):
            lines.append(f"Writing application                      : {encoder}")
        
        lines.append("")
        return lines
    
    def _build_video_section(self, stream, stream_num):
        """Build video stream information section."""
        lines = [
            "=" * 80,
            f"VIDEO #{stream_num}",
            "=" * 80,
            f"ID                                       : {stream.get('index', stream_num)}"
        ]
        
        # Format and codec
        codec_name = stream.get("codec_name", "Unknown").upper()
        lines.append(f"Format                                   : {codec_name}")
        
        if codec_long := stream.get("codec_long_name"):
            lines.append(f"Format/Info                              : {codec_long}")
        
        if profile := stream.get("profile"):
            lines.append(f"Format profile                           : {profile}")
        
        # HDR format
        if hdr := self._get_hdr_format(stream):
            lines.append(f"HDR format                               : {hdr}")
        
        # Codec ID
        if codec_tag := stream.get("codec_tag_string"):
            if codec_tag != "0x0000":
                lines.append(f"Codec ID                                 : {codec_tag}")
        
        # Duration and bitrate
        if duration := stream.get("duration"):
            lines.append(f"Duration                                 : {self.format_duration(duration)}")
        
        if bitrate := stream.get("bit_rate"):
            lines.append(f"Bit rate                                 : {self.format_bitrate(bitrate)}")
        
        # Resolution
        width = stream.get("width")
        height = stream.get("height")
        if width and height:
            lines.append(f"Width                                    : {width:,} pixels")
            lines.append(f"Height                                   : {height:,} pixels")
            lines.append(f"Display aspect ratio                     : {self.get_aspect_ratio(width, height)}")
        
        # Frame rate
        if frame_rate := stream.get("r_frame_rate"):
            lines.append("Frame rate mode                          : Constant")
            lines.append(f"Frame rate                               : {self.parse_frame_rate(frame_rate)}")
        
        # Color information
        color_info = self._get_color_info(stream)
        for key, value in color_info.items():
            lines.append(f"{key:40} : {value}")
        
        # Stream size (if calculable)
        if bitrate and duration:
            try:
                stream_size = (float(bitrate) * float(duration)) / 8
                total_size = self.file_path.stat().st_size
                percentage = (stream_size / total_size) * 100
                lines.append(f"Stream size                              : {self.format_size(stream_size)} ({percentage:.0f}%)")
            except (ValueError, TypeError, ZeroDivisionError):
                pass
        
        # Writing library
        if encoder := stream.get("tags", {}).get("encoder"):
            lines.append(f"Writing library                          : {encoder}")
        
        lines.append("")
        return lines
    
    def _build_audio_section(self, stream, stream_num):
        """Build audio stream information section."""
        lines = [
            "=" * 80,
            f"AUDIO #{stream_num}",
            "=" * 80,
            f"ID                                       : {stream.get('index', stream_num)}"
        ]
        
        # Format and codec
        codec_name = stream.get("codec_name", "Unknown").upper()
        
        # Special handling for Dolby Atmos (E-AC-3)
        if codec_name == "EAC3":
            lines.extend([
                "Format                                   : E-AC-3 JOC",
                "Format/Info                              : Enhanced AC-3 with Joint Object Coding",
                "Commercial name                          : Dolby Digital Plus with Dolby Atmos"
            ])
        else:
            lines.append(f"Format                                   : {codec_name}")
            if codec_long := stream.get("codec_long_name"):
                lines.append(f"Format/Info                              : {codec_long}")
        
        # Codec ID
        if codec_tag := stream.get("codec_tag_string"):
            if codec_tag != "0x0000":
                lines.append(f"Codec ID                                 : {codec_tag}")
        
        # Duration
        if duration := stream.get("duration"):
            lines.append(f"Duration                                 : {self.format_duration(duration)}")
        
        # Bitrate
        if bitrate := stream.get("bit_rate"):
            lines.append("Bit rate mode                            : Constant")
            lines.append(f"Bit rate                                 : {self.format_bitrate(bitrate)}")
        
        # Channels
        if channels := stream.get("channels"):
            lines.append(f"Channel(s)                               : {channels} channels")
            channel_layout = self._format_audio_channels(stream)
            lines.append(f"Channel layout                           : {channel_layout}")
        
        # Sampling rate
        if sample_rate := stream.get("sample_rate"):
            sample_rate_khz = float(sample_rate) / 1000
            lines.append(f"Sampling rate                            : {sample_rate_khz:.1f} kHz")
        
        # Frame rate (for AC-3 family)
        if codec_name in ["AC3", "EAC3", "DTS"] and sample_rate:
            try:
                frame_rate = float(sample_rate) / 1536
                lines.append(f"Frame rate                               : {frame_rate:.3f} FPS (1536 SPF)")
            except (ValueError, TypeError):
                pass
        
        # Compression mode
        lines.append("Compression mode                         : Lossy")
        
        # Stream size
        if bitrate and duration:
            try:
                stream_size = (float(bitrate) * float(duration)) / 8
                total_size = self.file_path.stat().st_size
                percentage = (stream_size / total_size) * 100
                lines.append(f"Stream size                              : {self.format_size(stream_size)} ({percentage:.0f}%)")
            except (ValueError, TypeError, ZeroDivisionError):
                pass
        
        # Language and flags
        language = stream.get("tags", {}).get("language", "und")
        lines.append(f"Language                                 : {language.capitalize()}")
        
        disposition = stream.get("disposition", {})
        lines.append(f"Default                                  : {'Yes' if disposition.get('default') else 'No'}")
        lines.append(f"Forced                                   : {'Yes' if disposition.get('forced') else 'No'}")
        
        lines.append("")
        return lines
    
    def _build_subtitle_section(self, stream, stream_num):
        """Build subtitle stream information section."""
        lines = [
            "=" * 80,
            f"SUBTITLE #{stream_num}",
            "=" * 80,
            f"ID                                       : {stream.get('index', stream_num)}"
        ]
        
        # Format
        codec_name = stream.get("codec_name", "Unknown").upper()
        lines.append(f"Format                                   : {codec_name}")
        
        # Codec ID
        if codec_tag := stream.get("codec_tag_string"):
            if codec_tag != "0x0000":
                lines.append(f"Codec ID                                 : {codec_tag}")
        
        # Language and title
        tags = stream.get("tags", {})
        language = tags.get("language", "und")
        lines.append(f"Language                                 : {language.capitalize()}")
        
        if title := tags.get("title"):
            lines.append(f"Title                                    : {title}")
        
        # Flags
        disposition = stream.get("disposition", {})
        lines.append(f"Default                                  : {'Yes' if disposition.get('default') else 'No'}")
        lines.append(f"Forced                                   : {'Yes' if disposition.get('forced') else 'No'}")
        
        lines.append("")
        return lines
    
    def generate(self):
        """Generate the complete NFO file."""
        if not self.file_path.exists():
            logger.error(f"File not found: {self.file_path}")
            return False
        
        if not self._run_ffprobe():
            return False
        
        try:
            all_lines = []
            
            # General section
            all_lines.extend(self._build_general_section())
            
            # Video streams
            video_streams = [s for s in self.streams if s.get("codec_type") == "video"]
            for idx, stream in enumerate(video_streams, 1):
                all_lines.extend(self._build_video_section(stream, idx))
            
            # Audio streams
            audio_streams = [s for s in self.streams if s.get("codec_type") == "audio"]
            for idx, stream in enumerate(audio_streams, 1):
                all_lines.extend(self._build_audio_section(stream, idx))
            
            # Subtitle streams
            subtitle_streams = [s for s in self.streams if s.get("codec_type") == "subtitle"]
            for idx, stream in enumerate(subtitle_streams, 1):
                all_lines.extend(self._build_subtitle_section(stream, idx))
            
            # Write to file
            nfo_path = self.file_path.with_suffix(".nfo")
            nfo_path.write_text("\n".join(all_lines), encoding="utf-8")
            logger.info(f"NFO created: {nfo_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error generating NFO: {e}")
            return False

def create_nfo(file_path: str) -> bool:
    """
    Generate a detailed .nfo file for the given media file.
    
    Args:
        file_path: Path to the media file
        
    Returns:
        True if successful, False otherwise
    """
    generator = NFOGenerator(file_path)
    return generator.generate()