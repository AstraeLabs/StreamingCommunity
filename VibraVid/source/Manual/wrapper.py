# 19.05.25

import os
import shutil
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from xml.etree import ElementTree as ET

from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import get_headers
from VibraVid.source.utils.tracker import download_tracker
from VibraVid.source.utils.media_players import MediaPlayers
from VibraVid.source.utils.object import StreamInfo, KeysManager

from .downloader.downloader import Downloader as ManualDownloader
from .utils.object import Stream, Segment
from .utils.file_size import format_bitrate


console = Console()
logger = logging.getLogger(__name__)
auto_select_cfg = config_manager.config.get_bool('DOWNLOAD', 'auto_select', default=True)
video_filter = config_manager.config.get("DOWNLOAD", "select_video")
audio_filter = config_manager.config.get("DOWNLOAD", "select_audio")
subtitle_filter = config_manager.config.get("DOWNLOAD", "select_subtitle")
cleanup_enabled = config_manager.config.get_bool('DOWNLOAD', 'cleanup_tmp_folder')


class MediaDownloader:
    def __init__(self, url: str, output_dir: str, filename: str, headers: Optional[Dict] = None, key: Optional[str] = None, cookies: Optional[Dict] = None, decrypt_preference: str = "bento4", download_id: str = None, site_name: str = None):
        console.print("[red]You are using the Manual MediaDownloader wrapper. This is intended for testing and may not have all features of the N_m3u8DL-RE wrapper. Please use N_m3u8DL-RE for best performance and compatibility.")
        self.url = url
        self.output_dir = Path(output_dir)
        self.filename = filename
        self.headers = headers or get_headers()
        self.key = key
        self.cookies = cookies or {}
        self.decrypt_preference = decrypt_preference.strip().lower()
        self.download_id = download_id
        self.site_name = site_name
        if self.decrypt_preference != "bento4":
            raise ValueError(
                f"Manual MediaDownloader only supports 'bento4' decryption. "
                f"Got: '{self.decrypt_preference}'. Please use bento4 or switch to N_m3u8DL-RE."
            )
        
        # Stream data
        self.streams: List[StreamInfo] = []
        self.external_subtitles = []
        self.status = None
        self.manifest_type = "Unknown"
        
        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir_type = (
            "Movie" if config_manager.config.get("OUTPUT", "movie_folder_name") in str(self.output_dir)
            else "TV" if config_manager.config.get("OUTPUT", "serie_folder_name") in str(self.output_dir)
            else "Anime" if config_manager.config.get("OUTPUT", "anime_folder_name") in str(self.output_dir)
            else "other"
        )
        extension = config_manager.config.get("PROCESS", "extension", default="mp4")
        self.output_path = str(self.output_dir / f"{self.filename}.{extension}")
        
        # Initialize Manual downloader
        self.manual_downloader = ManualDownloader(
            manifest_url=self.url,
            outpath=self.output_path,
            headers=self.headers,
            kid_key=self.key,
            download_id=self.download_id
        )
        self.external_subtitles = []
        
        # Track in GUI if ID is provided
        if self.download_id:
            download_tracker.start_download(
                self.download_id, 
                self.filename, 
                self.site_name or "Unknown", 
                self.output_dir_type
            )
        self.media_players = MediaPlayers(str(self.output_dir))
    
    def parser_stream(self) -> List[StreamInfo]:
        """
        Parse the manifest and return stream information.
        """
        if self.download_id:
            download_tracker.update_status(self.download_id, "parsing")
        
        if not self.manual_downloader.parse_streams():
            logger.error("Failed to parse streams")
            return []
        
        # Add external subtitles to display
        if self.external_subtitles:
            for ext_sub in self.external_subtitles:
                ext_stream = Stream('subtitle', f"*EXT {ext_sub.get('name', ext_sub.get('language', 'unknown'))}")
                ext_stream.language = ext_sub.get('language', 'unknown')
                ext_stream.name = f"*EXT {ext_sub.get('name', ext_sub.get('language', 'unknown'))}"
                ext_stream.selected = True
                ext_stream.segments = [Segment(ext_sub.get('url', ''), 0, 'subtitle')]
                self.manual_downloader.streams.append(ext_stream)
                self.manual_downloader.selected_streams.append(ext_stream)
        
        # Set manifest type
        self.manifest_type = self.manual_downloader.manifest_type.upper()
        
        # Convert Manual Stream objects to StreamInfo objects
        self.streams = []
        for stream in self.manual_downloader.streams:
            stream_info = self._convert_stream_to_streaminfo(stream)
            self.streams.append(stream_info)
        
        logger.info(f"Parsed {len(self.streams)} streams ({self.manifest_type})")
        return self.streams
    
    def _convert_stream_to_streaminfo(self, stream) -> StreamInfo:
        """Convert Manual Stream object to StreamInfo object"""
        stream_type = stream.type.capitalize()
        bandwidth_str = format_bitrate(stream.bitrate) if stream.bitrate > 0 else "N/A"
        
        # Determine extension
        if stream.type == 'video':
            extension = 'mp4'
        elif stream.type == 'audio':
            extension = 'm4a'
        elif stream.type == 'subtitle':
            extension = 'srt'
        else:
            extension = ''
        
        stream_info = StreamInfo(type_=stream_type, language=stream.language, resolution=stream.resolution if stream.type == 'video' else "", codec=stream.codecs,
            bandwidth=bandwidth_str, raw_bandwidth=str(stream.bitrate), name=stream.name, selected=stream.selected, extension=extension, total_duration=stream.duration, descriptor="Manual"
        )
        
        return stream_info
    
    def set_key(self, keys):
        """Set decryption key(s)"""
        if not keys:
            return
        
        # If KeysManager, store it
        if isinstance(keys, KeysManager):
            self.key = keys
            self.manual_downloader.kid_key = keys
            logger.info(f"Decryption keys updated: {len(keys)} keys")
            return
        
        # If list or string, create KeysManager
        if isinstance(keys, (list, str)):
            self.key = KeysManager(keys)
            self.manual_downloader.kid_key = self.key
            logger.info(f"Decryption keys updated: {len(self.key)} keys")
            return
    
    def start_download(self) -> Dict[str, Any]:
        """
        Start the download process.
        """
        if self.download_id:
            download_tracker.update_status(self.download_id, "downloading")
        self.media_players.create()
        
        # Check if already downloaded
        if os.path.exists(self.output_path):
            logger.info(f"File already exists: {self.output_path}")
            self.status = self._build_status_from_existing()
            self.manual_downloader.close_logging()
            return self.status
        
        # Start download
        try:
            success = self.manual_downloader.download()
            
            if not success:
                logger.error("Download failed")
                if self.download_id:
                    download_tracker.update_status(self.download_id, "failed")
                self.manual_downloader.close_logging()
                return {"error": "Download failed"}
            
        except KeyboardInterrupt:
            logger.warning("Download cancelled by user")
            if self.download_id:
                download_tracker.update_status(self.download_id, "cancelled")
            self.manual_downloader.close_logging()
            return {"error": "cancelled"}
        
        except Exception as e:
            logger.exception(f"Download error: {e}")
            if self.download_id:
                download_tracker.update_status(self.download_id, "failed")
            self.manual_downloader.close_logging()
            return {"error": str(e)}
        
        # Build status from downloaded results
        self.status = self._build_status()
        if self.download_id:
            download_tracker.update_status(self.download_id, "completed")
        
        # Clean up media player ignore files
        self.media_players.remove()
        self.manual_downloader.close_logging()
        
        # Clean up temp directory
        if cleanup_enabled:
            try:
                temp_dir = self.manual_downloader.temp_dir
                parent_temp_dir = os.path.dirname(temp_dir)
                
                # Clean up temp_download directory
                if os.path.exists(temp_dir):
                    shutil.rmtree(temp_dir, ignore_errors=True)
                
                # Clean up parent _dash_temp or _hls_temp directory if empty or contains only logs
                if os.path.exists(parent_temp_dir) and parent_temp_dir != str(self.output_dir):
                    remaining_items = os.listdir(parent_temp_dir)
                    if not remaining_items or (len(remaining_items) == 1 and 'log' in remaining_items):
                        shutil.rmtree(parent_temp_dir, ignore_errors=True)

            except Exception as e:
                print(f"Failed to cleanup temp directory: {e}")
        
        return self.status
    
    def _build_status(self) -> Dict[str, Any]:
        """Build status dictionary from download results"""
        status = {'video': None, 'audios': [], 'subtitles': [], 'external_subtitles': self.external_subtitles}
        results = self.manual_downloader.downloaded_results
        
        if not results:
            logger.warning("No download results found")

            # If output file exists, assume it's the video
            if os.path.exists(self.output_path):
                logger.info(f"Using existing output file as video: {self.output_path}")
                status['video'] = {
                    'path': self.output_path,
                    'size': os.path.getsize(self.output_path)
                }
            return status
        
        # Process results
        logger.info(f"Processing {len(results)} downloaded results")
        for file_path, stream in results:
            if not os.path.exists(file_path):
                logger.warning(f"Downloaded file not found: {file_path}")
                continue
            
            file_size = os.path.getsize(file_path)
            logger.info(f"Processing {stream.type}: {file_path} ({file_size} bytes)")
            
            if stream.type == 'video':
                status['video'] = {'path': file_path, 'size': file_size}
                logger.info(f"Video track added: {file_path}")
            
            elif stream.type == 'audio':
                status['audios'].append({'path': file_path, 'name': stream.language or stream.name, 'size': file_size})
                logger.info(f"Audio track added: {stream.language or stream.name}")
            
            elif stream.type == 'subtitle':
                status['subtitles'].append({'path': file_path, 'language': stream.language or stream.name, 'name': stream.name or stream.language, 'size': file_size})
                logger.info(f"Subtitle track added: {stream.language or stream.name}")
        
        # Post-process subtitles to handle duplicates (CC vs full)
        status['subtitles'] = self._deduplicate_subtitles(status['subtitles'])
        
        return status
    
    def _deduplicate_subtitles(self, subtitles_list):
        """Rename subtitles with _forced, _caption suffixes based on type"""
        if not subtitles_list:
            return subtitles_list
        
        result = []
        for sub in subtitles_list:

            # Check if metadata/name indicates type
            is_forced = 'forced' in sub['name'].lower() or 'forced' in sub['language'].lower()
            is_captions = '[CC]' in sub['name'] or '[cc]' in sub['name'] or '_captions' in sub['name'].lower()
            
            # Extract base language
            lang = sub['language'].lower().split(' - ')[0].strip()
            lang = lang.replace('forced-', '').replace('-forced', '').strip()
            
            if is_forced:
                sub['language'] = f"{lang}_forced"
                sub['name'] = f"{lang}_forced"
            elif is_captions:
                sub['language'] = f"{lang}_caption"
                sub['name'] = f"{lang}_caption"
            
            result.append(sub)
        
        return result
    
    def _build_status_from_existing(self) -> Dict[str, Any]:
        """Build status from existing file"""
        status = {'video': None, 'audios': [], 'subtitles': [], 'external_subtitles': []}
        if os.path.exists(self.output_path):
            status['video'] = {
                'path': self.output_path,
                'size': os.path.getsize(self.output_path)
            }
        
        # Check for additional audio/subtitle files
        base_name = os.path.splitext(self.filename)[0]
        for file in self.output_dir.iterdir():
            if not file.is_file():
                continue
            
            file_str = str(file)
            file_lower = file_str.lower()
            
            # Audio files
            if any(file_lower.endswith(ext) for ext in ['.m4a', '.aac', '.mp3']):
                if base_name in file.stem:
                    status['audios'].append({'path': file_str, 'name': file.stem.replace(base_name + '_', ''), 'size': file.stat().st_size})
            
            # Subtitle files
            elif any(file_lower.endswith(ext) for ext in ['.srt', '.vtt', '.ass']):
                if base_name in file.stem:
                    status['subtitles'].append({'path': file_str, 'language': file.stem.replace(base_name + '_', ''), 'name': file.stem.replace(base_name + '_', ''), 'size': file.stat().st_size})
        
        return status
    
    def get_status(self) -> Dict[str, Any]:
        """Get current download status"""
        return self.status if self.status else self._build_status_from_existing()
    
    def get_metadata(self) -> tuple:
        """
        Get metadata file paths (for compatibility with N_m3u8DL wrapper).
        """
        raw_m3u8 = None
        raw_mpd = None
        
        # Get raw manifest path from manual downloader
        if hasattr(self.manual_downloader, 'raw_manifest_path') and self.manual_downloader.raw_manifest_path:
            if self.manifest_type == "DASH":
                raw_mpd = self.manual_downloader.raw_manifest_path
            else:
                raw_m3u8 = self.manual_downloader.raw_manifest_path
        
        return None, None, raw_m3u8, raw_mpd
    
    def determine_decryption_tool(self) -> str:
        """
        Determine which decryption tool to use.
        """
        return "bento4"
    
    def add_external_subtitle(self, subtitle_info: Dict[str, str]):
        """
        Add external subtitle for download.
        
        Args:
            subtitle_info: Dict with 'url', 'language', 'name' keys
        """
        self.external_subtitles.append(subtitle_info)
        logger.info(f"Added external subtitle: {subtitle_info.get('language', 'unknown')}")
    
    def get_selected_ids_from_mpd(self, raw_mpd_path: str) -> tuple:
        """
        Extract selected track IDs from raw MPD file based on downloaded streams.
        Returns (selected_ids, selected_kids, selected_langs, selected_periods)
        """
        if not os.path.exists(raw_mpd_path):
            logger.warning(f"Raw MPD not found: {raw_mpd_path}")
            return ([], [], [], [])
        
        try:
            tree = ET.parse(raw_mpd_path)
            root = tree.getroot()
            
            # Define namespaces
            ns = {
                'mpd': 'urn:mpeg:dash:schema:mpd:2011',
                'cenc': 'urn:mpeg:cenc:2013'
            }
            
            selected_ids = []
            selected_kids = []
            selected_langs = []
            selected_periods = []
            
            # Get selected video resolution from manual downloader
            selected_video = [s for s in self.manual_downloader.selected_streams if s.type == 'video']
            selected_audio = [s for s in self.manual_downloader.selected_streams if s.type == 'audio']
            
            # Find matching representations in MPD
            for period in root.findall('.//mpd:Period', ns):
                period_id = period.get('id', 'default')
                
                for adaptation_set in period.findall('.//mpd:AdaptationSet', ns):
                    mime_type = adaptation_set.get('mimeType', '')
                    lang = adaptation_set.get('lang', 'und')
                    
                    # Extract KID from ContentProtection
                    for cp in adaptation_set.findall('.//mpd:ContentProtection', ns):
                        kid = cp.get('{urn:mpeg:cenc:2013}default_KID')
                        if kid:
                            kid_clean = kid.lower().replace('-', '')
                            if kid_clean not in selected_kids:
                                selected_kids.append(kid_clean)
                    
                    # Check representations
                    for rep in adaptation_set.findall('.//mpd:Representation', ns):
                        rep_id = rep.get('id')
                        height = rep.get('height')
                        
                        # Match video streams
                        if 'video' in mime_type and selected_video:
                            for v_stream in selected_video:

                                # Match by resolution
                                if height and str(v_stream.height) == height:
                                    if rep_id not in selected_ids:
                                        selected_ids.append(rep_id)
                                    if period_id not in selected_periods:
                                        selected_periods.append(period_id)
                        
                        # Match audio streams
                        elif 'audio' in mime_type and selected_audio:
                            for a_stream in selected_audio:

                                # Match by language
                                if a_stream.language.lower() == lang.lower():
                                    if rep_id not in selected_ids:
                                        selected_ids.append(rep_id)
                                    if lang.lower() not in selected_langs:
                                        selected_langs.append(lang.lower())
                                    if period_id not in selected_periods:
                                        selected_periods.append(period_id)
            
            logger.info(f"Extracted from MPD: {len(selected_ids)} IDs, {len(selected_kids)} KIDs, {len(selected_langs)} langs")
            return (selected_ids, selected_kids, selected_langs, selected_periods)
            
        except Exception as e:
            logger.error(f"Failed to parse MPD: {e}")
            return ([], [], [], [])