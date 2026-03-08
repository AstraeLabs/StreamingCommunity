# 13.02.26

import os
import shutil
import logging
from typing import Dict

from rich.console import Console

from VibraVid.utils import config_manager, os_manager, internet_manager
from VibraVid.utils.http_client import get_headers
from VibraVid.setup import get_wvd_path, get_prd_path
from VibraVid.core.post import join_video, join_audios, join_subtitles
from VibraVid.core.post.helper.nfo import create_nfo
from VibraVid.source.utils.tracker import download_tracker, context_tracker
from VibraVid.source.utils.media_players import MediaPlayers
from VibraVid.cli.run import execute_hooks

from ..drm import DRMManager
from ..parser import ISMParser

from VibraVid.source.N_m3u8 import MediaDownloader


console = Console()
CLEANUP_TMP = config_manager.config.get_bool('DOWNLOAD', 'cleanup_tmp_folder')
EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
SKIP_DOWNLOAD = config_manager.config.get_bool('DOWNLOAD', 'skip_download')
CREATE_NFO_FILES = config_manager.config.get_bool('PROCESS', 'generate_nfo', default=False)
SUBTITLE_FILTER = config_manager.config.get('DOWNLOAD', 'select_subtitle')
MERGE_SUBTITLES = config_manager.config.get_bool('PROCESS', 'merge_subtitle', default=True)
MERGE_AUDIO = config_manager.config.get_bool('PROCESS', 'merge_audio', default=True)


class ISM_Downloader:
    def __init__(self, license_url: str, license_headers: Dict[str, str] = None, ism_url: str = None, ism_headers: Dict[str, str] = None, ism_sub_list: list = None, output_path: str = None, drm_preference: str = 'playready', decrypt_preference: str = "bento4", key: str = None, cookies: Dict[str, str] = None):
        """
        Initialize ISM Downloader.
        
        Parameters:
            license_url: URL to obtain DRM license
            ism_url: URL of the ISM manifest
            ism_sub_list: List of subtitle dicts (unused with MediaDownloader)
            output_path: Full path including filename and extension (e.g., /path/to/video.mp4)
            drm_preference: Preferred DRM system ('playready', 'auto')
        """
        self.ism_url = str(ism_url).strip() if ism_url else None
        self.license_url = str(license_url).strip() if license_url else None
        self.ism_headers = ism_headers or get_headers()
        self.license_headers = license_headers
        self.ism_sub_list = ism_sub_list or []
        self.drm_preference = drm_preference.lower()
        self.key = key
        self.cookies = cookies or {}
        self.decrypt_preference = decrypt_preference.lower()
        self.drm_manager = DRMManager(get_wvd_path(), get_prd_path(), config_manager.config.get_dict('DRM', 'widevine'), config_manager.config.get_dict('DRM', 'playready'))
        
        # Tracking IDs - check context if not provided
        self.download_id = context_tracker.download_id
        self.site_name = context_tracker.site_name
        self.raw_ism = None
        
        # Setup output path
        self.output_path = os_manager.get_sanitize_path(output_path)
        if not self.output_path.endswith(f'.{EXTENSION_OUTPUT}'):
            self.output_path += f'.{EXTENSION_OUTPUT}'
        
        self.filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        self.output_dir = os.path.join(os.path.dirname(self.output_path), self.filename_base + "_ism_temp")
        self.file_already_exists = os.path.exists(self.output_path)
        
        # DRM and state
        self.drm_info = None
        self.decryption_keys = []
        self.media_downloader = None
        self.meta_json = self.meta_selected = None
        self.error = None
        self.last_merge_result = None
        self.media_players = None
        self.copied_subtitles = []
        self.copied_audios = []
        self.audio_only = False
    
    def _setup_drm_info(self):
        """Fetch and setup DRM information using ISMParser."""
        try:
            parser = ISMParser(self.ism_url, headers=self.ism_headers)
            parser.parse_from_file(self.raw_ism)
            
            # Get DRM info
            self.drm_info = parser.get_drm_info(self.drm_preference)
            return True
        
        except Exception as e:
            console.print(f"[yellow]Warning parsing ISM: {e}")
            return False
    
    def _fetch_decryption_keys(self):
        """Fetch decryption keys based on DRM type."""
        if len(self.drm_info.get('available_drm_types', [])) > 0 and (self.license_url is None or self.license_url == "") or len(self.drm_info.get('available_drm_types', [])) > 0 and (self.key is None or self.key == ""):
            if (len(self.drm_info.get('available_drm_types', [])) > 0 and (not self.license_url or self.license_url == "") and (not self.key or self.key == "")):
                console.print("[yellow]DRM detected but missing both license_url and key. Cannot proceed.")
                self.error = "Missing license_url and key for DRM-protected content"
                return False
            
        drm_type = self.drm_info['selected_drm_type']
        try:
            if drm_type == 'playready':
                keys = self.drm_manager.get_pr_keys(self.drm_info.get('playready_pssh', []), self.license_url, self.license_headers, self.key)
            else:
                console.print(f"[red]Unsupported DRM type: {drm_type}")
                self.error = f"Unsupported DRM type: {drm_type}"
                return False
        
            if keys:
                self.decryption_keys = keys
                return True
        
            else:
                self.error = "Failed to fetch decryption keys"
                return False
            
        except Exception as e:
            console.print(f"[red]Error fetching keys: {e}")
            self.error = f"Key fetch error: {e}"
            return False
    
    def start(self):
        """Main execution flow for downloading ISM content."""
        if self.file_already_exists:
            console.print("[yellow]File already exists.")
            return self.output_path, False
        
        # Create output directory
        os_manager.create_path(self.output_dir)
        
        # Create media player ignore files
        try:
            self.media_players = MediaPlayers(self.output_dir)
            self.media_players.create()
        except Exception:
            pass
        
        # Initialize MediaDownloader
        self.media_downloader = MediaDownloader(
            url=self.ism_url,
            output_dir=self.output_dir,
            filename=self.filename_base,
            headers=self.ism_headers,
            cookies=self.cookies,
            decrypt_preference=self.decrypt_preference,
            download_id=self.download_id,
            site_name=self.site_name
        )
        
        # Store DRM info for later use in manual decryption
        self.media_downloader.license_url = self.license_url
        self.media_downloader.drm_type = self.drm_preference
        
        if self.ism_sub_list and SUBTITLE_FILTER != "false":
            console.print(f"[dim]Adding {len(self.ism_sub_list)} external subtitle(s) to the downloader.")
            self.media_downloader.external_subtitles = self.ism_sub_list
        
        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing ISM ...")
        
        # Parse streams using N_m3u8dl (creates meta.json and raw.ism)
        console.print("[dim]Parsing ISM ...")
        self.media_downloader.parser_stream()
        
        # Get metadata paths
        meta_json_path, meta_selected_path, _, _, raw_ism = self.media_downloader.get_metadata()
        self.meta_json = meta_json_path
        self.meta_selected = meta_selected_path
        self.raw_ism = raw_ism
        
        # Parse ISM and setup DRM info
        if not self._setup_drm_info():
            logging.error("Failed to parse ISM")
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="ISM parsing failed")
            return None, True
        
        # Fetch decryption keys if DRM protected
        if self.drm_info and self.drm_info['available_drm_types']:
            if not self._fetch_decryption_keys():
                logging.error(f"Failed to fetch decryption keys: {self.error}")
                if self.download_id:
                    download_tracker.complete_download(self.download_id, success=False, error=self.error)
                return None, True
            
        if SKIP_DOWNLOAD:
            console.print("[yellow]Skipping download as per configuration.")
            return self.output_path, False
        
        # Set keys and start download
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")
        
        if context_tracker.should_print:
            console.print("[dim]\nStarting download ...")
        self.media_downloader.set_key(self.decryption_keys)
        status = self.media_downloader.start_download()
        
        # Check for cancellation
        if status.get('error') == 'cancelled':
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="cancelled")
            return None, True

        # Check if any media was downloaded
        if self._no_media_downloaded(status):
            logging.error("No media downloaded")
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="No media downloaded")
            return None, True
        
        # Merge files
        if self.download_id:
            download_tracker.update_status(self.download_id, "Muxing ...")
            
        final_file = self._merge_files(status)
        if not final_file:
            if self.download_id and download_tracker.is_stopped(self.download_id):
                download_tracker.complete_download(self.download_id, success=False, error="cancelled")
                return None, True
                
            logging.error("Merge operation failed")
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="Merge failed")
            return None, True
        
        # Move to final location if needed
        if final_file and os.path.exists(final_file):
            self._move_to_final_location(final_file)
        
        self._move_copied_audios()
        self._move_copied_subtitles()
        
        # Print summary and cleanup
        self._print_summary()
        
        if CREATE_NFO_FILES:
            create_nfo(self.output_path)
        if self.download_id:
            download_tracker.complete_download(self.download_id, success=True, path=os.path.abspath(self.output_path))
            
        if CLEANUP_TMP:
            shutil.rmtree(self.output_dir, ignore_errors=True)
        
        execute_hooks('post_run')
        return self.output_path, False
    
    def _no_media_downloaded(self, status):
        """Check if no media was downloaded."""
        return (status.get('video') is None and status.get('audios') == [] and status.get('subtitles') == [] and status.get('external_subtitles') == [])
    
    def _move_to_final_location(self, final_file):
        """Move file to final output path, updating self.output_path if the extension changed."""
        # If the merge produced a different extension (e.g. .mp4 instead of .mkv), update self.output_path to match before renaming.
        final_ext = os.path.splitext(final_file)[1].lower()
        desired_ext = os.path.splitext(self.output_path)[1].lower()
        if final_ext != desired_ext:
            base = os.path.splitext(self.output_path)[0]
            self.output_path = base + final_ext

        if os.path.abspath(final_file) != os.path.abspath(self.output_path):
            try:
                if os.path.exists(self.output_path):
                    os.remove(self.output_path)
                os.rename(final_file, self.output_path)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not move file: {e}")
                self.output_path = final_file
    
    def _merge_files(self, status):
        """Merge downloaded files using FFmpeg."""
        if status['video'] is None:
            if status['audios'] or status['subtitles']:
                
                # Handle audio-only or subtitle-only case
                self.audio_only = True
                if status['audios']:
                    self._track_audios_for_copy(status['audios'])
                if status['subtitles']:
                    self._track_subtitles_for_copy(status['subtitles'])
                return self.output_path
            return None
        
        video_path = status['video']['path']
        
        if not os.path.exists(video_path):
            console.print(f"[red]Video file not found: {video_path}, continuing with available tracks.")

        video_path = status['video']['path']
        
        if not os.path.exists(video_path):
            console.print(f"[red]Video file not found: {video_path}")
            self.error = "Video file missing"
            return None
        
        # If no additional tracks, just mux video
        if not status['audios'] and not status['subtitles']:
            console.print("[cyan]\nNo additional tracks to merge, muxing video...")
            merged_file, result_json = join_video(
                video_path=video_path,
                out_path=self.output_path,
                log_path=os.path.join(self.output_dir, "video_mux.log")
            )
            self.last_merge_result = result_json
            return merged_file if os.path.exists(merged_file) else None
        
        current_file = video_path
        
        # Merge or track audio tracks
        if status['audios']:
            if MERGE_AUDIO:
                current_file = self._merge_audio_tracks(current_file, status['audios'])
            else:
                self._track_audios_for_copy(status['audios'])
        
        # Merge or track subtitle tracks
        if status['subtitles']:
            if MERGE_SUBTITLES:
                current_file = self._merge_subtitle_tracks(current_file, status['subtitles'])
            else:
                self._track_subtitles_for_copy(status['subtitles'])
        
        return current_file
    
    def _merge_audio_tracks(self, current_file, audio_tracks):
        """Merge audio tracks with current video."""
        console.print(f"[cyan]\nMerging [red]{len(audio_tracks)} [cyan]audio track(s)...")
        audio_output = os.path.join(self.output_dir, f"{self.filename_base}_with_audio.{EXTENSION_OUTPUT}")
        
        merged_file, _, result_json = join_audios(
            video_path=current_file,
            audio_tracks=audio_tracks,
            out_path=audio_output,
            log_path=os.path.join(self.output_dir, "audio_merge.log")
        )
        self.last_merge_result = result_json
        
        if os.path.exists(merged_file):
            return merged_file
        else:
            console.print("[yellow]Audio merge failed, continuing with video only")
            return current_file
    
    def _merge_subtitle_tracks(self, current_file, subtitle_tracks):
        """Merge subtitle tracks with current video."""
        console.print(f"[cyan]\nMerging [red]{len(subtitle_tracks)} [cyan]subtitle track(s)...")
        sub_output = os.path.join(self.output_dir, f"{self.filename_base}_final.{EXTENSION_OUTPUT}")
        
        merged_file, result_json = join_subtitles(
            video_path=current_file,
            subtitles_list=subtitle_tracks,
            out_path=sub_output,
            log_path=os.path.join(self.output_dir, "sub_merge.log")
        )
        self.last_merge_result = result_json
        
        if os.path.exists(merged_file):
            return merged_file
        else:
            console.print("[yellow]Subtitle merge failed, continuing without subtitles")
            return current_file
    
    def _track_audios_for_copy(self, audios_list):
        """Track audio paths for later copying to final location."""
        for idx, audio in enumerate(audios_list):
            audio_path = audio.get('path')
            if audio_path and os.path.exists(audio_path):
                language = audio.get('language', audio.get('name', f'audio{idx}'))
                extension = os.path.splitext(audio_path)[1]
                self.copied_audios.append({
                    'src': audio_path,
                    'language': language,
                    'extension': extension
                })
    
    def _track_subtitles_for_copy(self, subtitles_list):
        """Track subtitle paths for later copying to final location."""
        for idx, subtitle in enumerate(subtitles_list):
            subtitle_path = subtitle.get('path')
            if subtitle_path and os.path.exists(subtitle_path):
                language = subtitle.get('language', f'sub{idx}')
                extension = os.path.splitext(subtitle_path)[1]
                self.copied_subtitles.append({
                    'src': subtitle_path,
                    'language': language,
                    'extension': extension
                })
    
    def _move_copied_audios(self):
        """Move tracked audio files to final output directory if copied_audios exists."""
        if not self.copied_audios:
            return
        
        output_dir = os.path.dirname(self.output_path)
        filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        console.print("[cyan]Copy the audios to the final path.")
        
        for idx, audio_info in enumerate(self.copied_audios):
            src_path = audio_info['src']
            language = audio_info['language']
            extension = audio_info['extension']
            
            if self.audio_only and idx == 0:
                dst_path = self.output_path
                move_func = shutil.move
            else:
                # final name
                dst_path = os.path.join(output_dir, f"{filename_base}.{language}{extension}")
                move_func = shutil.copy2
            
            try:
                move_func(src_path, dst_path)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not move audio {language}: {e}")
    
    def _move_copied_subtitles(self):
        """Move tracked subtitle files to final output directory if copied_subtitles exists."""
        if not self.copied_subtitles:
            return
        
        output_dir = os.path.dirname(self.output_path)
        filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        console.print("[cyan]Copy the subtitles to the final path.")
        
        for subtitle_info in self.copied_subtitles:
            src_path = subtitle_info['src']
            language = subtitle_info['language']
            extension = subtitle_info['extension']
            
            # final name
            dst_path = os.path.join(output_dir, f"{filename_base}.{language}{extension}")
            
            try:
                shutil.copy2(src_path, dst_path)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not move subtitle {language}: {e}")
    
    def _print_summary(self):
        """Print download summary."""
        if not os.path.exists(self.output_path):
            return
        
        file_size = internet_manager.format_file_size(os.path.getsize(self.output_path))
        duration = 'N/A'
        
        if self.last_merge_result and isinstance(self.last_merge_result, dict):
            duration = self.last_merge_result.get('time', 'N/A')
        
        console.print("\n[green]Output:")
        console.print(f"  [cyan]Path: [red]{os.path.abspath(self.output_path)}")
        console.print(f"  [cyan]Size: [red]{file_size}")
        console.print(f"  [cyan]Duration: [red]{duration}")