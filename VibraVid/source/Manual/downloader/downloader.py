# 19.05.25

import os
import re
import shutil
import logging
from datetime import datetime

from rich.console import Console
from rich.table import Table

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

from .segmnets import SegmentDownloader
from ..decrypt.decrypt import Decryptor
from ..utils.object import Stream
from ..utils.merger import FileMerger
from ..utils.file_size import format_bitrate
from .selector import StreamSelector
from ..parser.dash import DashParser
from ..parser.hls import HLSParser
from ...utils.trans_codec import get_audio_codec_name, get_video_codec_name, get_codec_type


logger = logging.getLogger(__name__)
console = Console()
TIMEOUT = config_manager.config.get_int('REQUESTS', 'timeout')
MAX_WORKERS = config_manager.config.get_int('DOWNLOAD', 'thread_count')


class StreamDownloader:
    def __init__(self, parser, segment_downloader: SegmentDownloader, decryptor: Decryptor, output_path: str, temp_dir: str, kid_key=None, download_id=None):
        self.parser = parser
        self.segment_downloader = segment_downloader
        self.decryptor = decryptor
        self.output_path = output_path
        self.temp_dir = temp_dir
        self.kid_key = kid_key
        self.download_id = download_id
        self.output_dir = os.path.dirname(output_path)
        self.output_filename = os.path.basename(output_path)
    
    def download_stream(self, stream: Stream):
        description = stream.get_description()
        
        # Get display info for progress bar
        display_resolution = ""
        display_language = stream.language if stream.language and stream.language != "und" else ""
        
        if stream.type == 'video' and stream.height > 0:
            display_resolution = f"{stream.height}p"
        
        if stream.type == 'subtitle':
            if not stream.segments:
                console.print(f"[red]No subtitle URL found for {description}[/red]")
                return None
            
            clean_name = re.sub(r'[^\w\s-]', '', stream.name).strip().replace(' ', '_')
            if not clean_name:
                clean_name = stream.language
            
            url_lower = stream.segments[0].url.lower()
            ext = '.srt' if '.srt' in url_lower else '.vtt'
            
            subtitle_filename = f"{clean_name}{ext}"
            subtitle_path = os.path.join(self.output_dir, subtitle_filename)
            
            try:
                with create_client(headers=self.segment_downloader.headers, timeout=TIMEOUT, follow_redirects=True) as client:
                    response = client.get(stream.segments[0].url)
                    response.raise_for_status()
                    with open(subtitle_path, 'wb') as f:
                        f.write(response.content)
                    return subtitle_path
                
            except Exception as e:
                console.print(f"[red]Failed to download subtitle: {e}[/red]")
                return None
        
        # Video/Audio handling
        folder_type = stream.type
        seg_dir = os.path.join(self.temp_dir, folder_type, "segment")
        os.makedirs(seg_dir, exist_ok=True)
        
        if not self.segment_downloader.download_all(stream.segments, seg_dir, description, stream_type=stream.type, language=display_language,
            resolution=display_resolution, encryption_method=stream.encryption_method, key_data=stream.key_data, iv=stream.iv,
            decryptor=self.decryptor if stream.encryption_method == 'AES-128' else None
        ):
            console.print("[yellow]⚠ Download incomplete.")
        
        # Merge segments
        merged_file = os.path.join(seg_dir, f"merged_{description}.mp4")
        if not FileMerger.merge(seg_dir, merged_file):
            return None
        
        # Determine final output path for this stream
        if stream.type == 'video':
            final_file = self.output_path
        else:
            base_name = os.path.splitext(self.output_filename)[0]
            final_file = os.path.join(self.output_dir, f"{base_name}_{stream.language}.mp4")
        
        # Store the encrypted file path for later decryption (DASH with DRM)
        if stream.drm.is_encrypted():
            encrypted_file = os.path.join(self.temp_dir, folder_type, f"encrypted_{description}.mp4")
            shutil.move(merged_file, encrypted_file)
            return encrypted_file
        
        elif stream.encryption_method == 'AES-128':
            shutil.move(merged_file, final_file)
            return final_file
        
        else:
            shutil.move(merged_file, final_file)
            return final_file


def display_streams(all_streams):
    """Display unified stream table with selection markers"""
    table = Table(show_header=True, header_style="cyan")
    table.add_column("#", style="yellow")
    table.add_column("Type", style="cyan")
    table.add_column("Sel", style="green")
    table.add_column("Resolution", style="magenta")
    table.add_column("Bitrate", style="blue")
    table.add_column("Codec", style="white")
    table.add_column("Language", style="green")
    table.add_column("Duration", style="yellow")
    table.add_column("Segments", style="white")
    
    # Sort streams: Video first (by resolution), then audio (by bitrate), then subtitle
    def sort_key(stream):
        if stream.type == 'video' and stream.height > 0:
            return (0, -stream.height, -stream.bitrate)
        elif stream.type == 'audio':
            return (1, 0, -stream.bitrate)
        elif stream.type == 'subtitle':
            return (2, 0, -stream.bitrate)
        else:
            return (3, 0, -stream.bitrate)
    
    sorted_streams = sorted(all_streams, key=sort_key)
    
    for idx, stream in enumerate(sorted_streams, 1):
        sel_mark = "X" if stream.selected else ""
        resolution = stream.resolution if stream.type == 'video' else "-"
        language = stream.language if stream.type in ['audio', 'subtitle'] else "-"
        
        # Transcode codec names
        readable_codecs = ""
        if "," in stream.codecs:
            codec_parts = []
            for raw_codec in stream.codecs.split(","):
                codec_type = get_codec_type(raw_codec)
                if codec_type == "Audio":
                    codec_parts.append(get_audio_codec_name(raw_codec))
                elif codec_type == "Video":
                    codec_parts.append(get_video_codec_name(raw_codec))
                else:
                    codec_parts.append(raw_codec)
            readable_codecs = ", ".join(codec_parts)
        else:
            codec_type = get_codec_type(stream.codecs)
            if codec_type == "Audio":
                readable_codecs = get_audio_codec_name(stream.codecs)
            elif codec_type == "Video":
                readable_codecs = get_video_codec_name(stream.codecs)
            else:
                readable_codecs = stream.codecs
        
        table.add_row(
            str(idx),
            stream.get_type_display(),
            sel_mark,
            resolution,
            format_bitrate(stream.bitrate),
            readable_codecs,
            language,
            stream.get_duration_display(),
            str(len(stream.segments))
        )
    
    console.print(table)


class Downloader:
    def __init__(self, manifest_url, outpath="Video/temp.mp4", headers=None, kid_key=None, download_id=None):
        self.manifest_url = manifest_url
        self.output_path = os.path.abspath(outpath)
        self.headers = headers or get_headers()
        self.kid_key = kid_key
        self.download_id = download_id
        
        # Determine manifest type
        if ".mpd" in manifest_url.lower() or "?type=dash" in manifest_url.lower():
            self.manifest_type = "dash"
        else:
            self.manifest_type = "hls"
        
        # Setup directories
        self.output_dir = os.path.dirname(self.output_path)
        self.temp_dir = os.path.join(self.output_dir, "temp_download")
        self.log_dir = os.path.join(self.temp_dir, "log")
        self._setup_directories()
        self._setup_logging()
        
        # Initialize components
        if self.manifest_type == "dash":
            provided_kid = None
            if self.kid_key:
                parts = self.kid_key.split(':')
                if len(parts) >= 1:
                    provided_kid = parts[0]

            self.parser = DashParser(self.manifest_url, self.headers, provided_kid)
        else:
            self.parser = HLSParser(self.manifest_url, self.headers, None)
        
        self.segment_downloader = SegmentDownloader(headers=self.headers, max_workers=MAX_WORKERS, download_id=self.download_id)
        self.decryptor = Decryptor()
        self.stream_orchestrator = StreamDownloader(self.parser, self.segment_downloader, self.decryptor, self.output_path, self.temp_dir, self.kid_key, self.download_id)
        self.streams = []
        self.selected_streams = []
        self.downloaded_results = []
        self.encrypted_files = []
        self.raw_manifest_path = None

    def _setup_directories(self):
        os.makedirs(self.output_dir, exist_ok=True)
        os.makedirs(self.temp_dir, exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)
        
        for folder in ['video', 'audio']:
            for subfolder in ['segment']:
                os.makedirs(os.path.join(self.temp_dir, folder, subfolder), exist_ok=True)

    def _setup_logging(self):
        log_file = os.path.join(self.log_dir, f"download_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
        self.log_handler = logging.FileHandler(log_file, encoding='utf-8')
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[self.log_handler],
            force=True
        )
        global logger
        logger = logging.getLogger("Downloader")
        self.logger = logger

    def close_logging(self):
        """Close logging handlers to release file locks"""
        if hasattr(self, 'log_handler'):
            self.log_handler.close()
            root_logger = logging.getLogger()
            root_logger.removeHandler(self.log_handler)
            
            # Clean up all handlers of our specific logger too
            for handler in self.logger.handlers[:]:
                handler.close()
                self.logger.removeHandler(handler)

    def parse_streams(self):
        if not self.parser.fetch_manifest():
            console.print("[red]Failed to fetch manifest.")
            return False
        
        self._save_raw_manifest()
        self.streams = self.parser.parse_streams()
        if not self.streams:
            console.print("[red]No streams found[/red]")
            return False
        
        # Apply selection filters
        self._apply_selections()
        self.streams.sort(key=lambda s: {'video': 0, 'audio': 1, 'subtitle': 2}.get(s.type, 3))
        display_streams(self.streams)
        return True
    
    def _save_raw_manifest(self):
        """Save raw manifest content to file"""
        try:
            if self.manifest_type == "dash":
                raw_file = os.path.join(self.temp_dir, "raw.mpd")
            else:
                raw_file = os.path.join(self.temp_dir, "raw.m3u8")
            
            with open(raw_file, 'w', encoding='utf-8') as f:
                if hasattr(self.parser, 'mpd_content'):
                    f.write(self.parser.mpd_content)
                elif hasattr(self.parser, 'master_content'):
                    f.write(self.parser.master_content)
            
            self.raw_manifest_path = raw_file
        except Exception as e:
            logger.warning(f"Failed to save raw manifest: {e}")
            self.raw_manifest_path = None
    
    def _apply_selections(self):
        """Apply selection filters to streams"""
        video_filter = config_manager.config.get('DOWNLOAD', 'select_video')
        StreamSelector.select_video(self.streams, video_filter)
        
        audio_filter = config_manager.config.get('DOWNLOAD', 'select_audio')
        StreamSelector.select_audio(self.streams, audio_filter)
        
        subtitle_filter = config_manager.config.get('DOWNLOAD', 'select_subtitle')
        if subtitle_filter != "false":
            StreamSelector.select_subtitle(self.streams, subtitle_filter)
        
        # Get list of selected streams
        self.selected_streams: list[Stream] = [s for s in self.streams if s.selected]

    def download(self):
        if not self.selected_streams:
            console.print("[yellow]⚠ No streams selected.")
            return False
    
        # Prepare selected streams (fetch segments for HLS)
        for stream in self.selected_streams:
            if not stream.segments and hasattr(stream, 'playlist_url') and stream.playlist_url:
                segments, bandwidth, enc_method, key_uri, iv, duration = self.parser.fetch_segments(stream.playlist_url)
                stream.segments = segments
                stream.duration = duration
                if bandwidth and stream.bitrate == 0:
                    stream.bitrate = bandwidth

                if enc_method:
                    stream.encryption_method = enc_method
                    stream.key_uri = key_uri
                    stream.iv = iv

                    # Download encryption key
                    try:
                        with create_client(headers=self.headers, timeout=TIMEOUT, follow_redirects=True) as client:
                            response = client.get(key_uri)
                            response.raise_for_status()
                            stream.key_data = response.content
                    except Exception as e:
                        self.logger.error(f"Failed to download key: {e}")
        
        # Download streams
        download_order = ['subtitle', 'video', 'audio']
        for stream_type in download_order:
            type_streams = [s for s in self.selected_streams if s.type == stream_type]
            for stream in type_streams:
                result = self.stream_orchestrator.download_stream(stream)
                if result:
                    self.downloaded_results.append((result, stream))
                    if stream.drm.is_encrypted():
                        self.encrypted_files.append((result, stream))
        
        # Decrypt all encrypted files at once (DASH with DRM)
        if self.encrypted_files:
            self._decrypt_all()
        
        return True
    
    def _decrypt_all(self):
        """Decrypt all encrypted files"""
        for encrypted_path, stream in self.encrypted_files:
            key_pair = stream.drm.get_key_pair()
            if not key_pair and self.kid_key:
                logger.info("No key pair in stream, using fallback kid_key")
                
                # Find key by KID
                key_pair = self.kid_key.find_key_by_kid(stream.drm.kid)
                if key_pair:
                    kid, key = key_pair.split(':', 1)
                    stream.drm.set_kid(kid)
                    stream.drm.key = key
                    key_pair = stream.drm.get_key_pair()
            
            if key_pair:
                # Determine final output path
                if stream.type == 'video':
                    final_file = self.output_path
                else:
                    base_name = os.path.splitext(os.path.basename(self.output_path))[0]
                    final_file = os.path.join(self.output_dir, f"{base_name}_{stream.language}.mp4")
                
                if self.decryptor.decrypt(encrypted_path, [key_pair], final_file):
                    logger.info(f"Decryption successful: {final_file}")
                    for i, (path, s) in enumerate(self.downloaded_results):
                        if path == encrypted_path:
                            self.downloaded_results[i] = (final_file, s)
                            logger.info(f"Updated downloaded_results[{i}] to {final_file}")
                            break

                else:
                    console.print(f"[yellow]Keeping encrypted file: {encrypted_path}.")
            else:
                console.print(f"[yellow]⚠ No decryption key for {stream.get_description()}.")

    def get_status(self):
        if not self.downloaded_results:
            return None
        
        videos = [(f, s) for f, s in self.downloaded_results if s.type == 'video']
        audios = [(f, s) for f, s in self.downloaded_results if s.type == 'audio']
        subtitles = [(f, s) for f, s in self.downloaded_results if s.type == 'subtitle']
        return {'video': videos, 'audio': audios, 'subtitle': subtitles}