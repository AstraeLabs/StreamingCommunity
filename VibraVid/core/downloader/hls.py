# 17.10.24

import os
import re
import shutil
import logging
from typing import Any, Dict, Optional

from rich.console import Console

from VibraVid.utils import config_manager, os_manager, internet_manager
from VibraVid.utils.http_client import get_headers
from VibraVid.setup import get_wvd_path, get_prd_path
from VibraVid.core.processors import join_video, join_audios, join_subtitles
from VibraVid.core.processors.helper.nfo import create_nfo
from VibraVid.source.utils.tracker import download_tracker, context_tracker
from VibraVid.source.utils.media_players import MediaPlayers
from VibraVid.cli.run import execute_hooks

from VibraVid.source.N_m3u8 import MediaDownloader
from VibraVid.core.parser.m3u8 import M3U8Parser
from VibraVid.core.drm.manager import DRMManager


console = Console()
CLEANUP_TMP = config_manager.config.get_bool('DOWNLOAD', 'cleanup_tmp_folder')
EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
SKIP_DOWNLOAD = config_manager.config.get_bool('DOWNLOAD', 'skip_download')
CREATE_NFO_FILES = config_manager.config.get_bool('PROCESS', 'generate_nfo', default=False)
MERGE_SUBTITLES = config_manager.config.get_bool('PROCESS', 'merge_subtitle', default=True)
MERGE_AUDIO = config_manager.config.get_bool('PROCESS', 'merge_audio', default=True)


class HLS_Downloader:
    def __init__(self, m3u8_url: str, license_url: str, output_path: Optional[str] = None, headers: Optional[Dict[str, str]] = None, license_headers: Optional[Dict[str, str]] = None, drm_preference: str = 'widevine', decrypt_preference: str = "shaka", key: str = None, cookies: Optional[Dict[str, str]] = None):
        """
        Args:
            m3u8_url: Source M3U8 playlist URL
            license_url: License URL for DRM content
            output_path: Full path including filename and extension (e.g., /path/to/video.mp4)
            headers: Headers for M3U8 requests
            license_headers: Headers for license requests (optional, uses headers if not provided)
            drm_preference: Preferred DRM system ('widevine', 'playready', 'auto')
            decrypt_preference: Decryption tool preference ('bento4', 'shaka')
            key: Optional manual decryption key
            cookies: Optional cookies for requests
        """
        self.m3u8_url = str(m3u8_url).strip()
        self.license_url = str(license_url).strip() if license_url else None
        self.headers = headers or get_headers()
        self.license_headers = license_headers or self.headers
        self.drm_preference = drm_preference.lower()
        self.decrypt_preference = decrypt_preference.lower()
        self.key = key
        self.cookies = cookies or {}

        # Sanitize and validate output path
        if not output_path:
            output_path = f"download.{EXTENSION_OUTPUT}"
        
        self.output_path = os_manager.get_sanitize_path(output_path)
        if not self.output_path.endswith(f'.{EXTENSION_OUTPUT}'):
            self.output_path += f'.{EXTENSION_OUTPUT}'
        
        # Extract directory and filename components ONCE
        self.filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        self.output_dir = os.path.join(os.path.dirname(self.output_path), self.filename_base + "_hls_temp")
        self.file_already_exists = os.path.exists(self.output_path)
        
        # Tracking IDs - check context if not provided
        self.download_id = context_tracker.download_id
        self.site_name = context_tracker.site_name

        # Status tracking
        self.error = None
        self.last_merge_result = None
        self.media_players = None
        self.copied_subtitles = []
        self.copied_audios = []
        self.audio_only = False

    def start(self) -> Dict[str, Any]:
        """Main execution flow for downloading HLS content"""
        if self.file_already_exists:
            console.print("[yellow]File already exists.")
            return self.output_path, False
        
        # Setup media downloader
        self.media_downloader = MediaDownloader(
            url=self.m3u8_url,
            output_dir=self.output_dir,
            filename=self.filename_base,
            headers=self.headers,
            download_id=self.download_id,
            site_name=self.site_name,
            decrypt_preference=self.decrypt_preference
        )

        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing HLS ...")
            
        if context_tracker.should_print:
            console.print("[dim]Parsing HLS ...")
        self.media_downloader.parser_stream()

        # Initialize DRM Manager once for both Widevine and PlayReady
        drm_manager = DRMManager(get_wvd_path(), get_prd_path(), config_manager.config.get_dict('DRM', 'widevine'), config_manager.config.get_dict('DRM', 'playready'))

        # Handle DRM extraction and key fetching
        if self.license_url or self.key:
            m3u8_content = None
            if hasattr(self.media_downloader, 'raw_m3u8') and self.media_downloader.raw_m3u8:
                try:
                    raw_m3u8_path = self.media_downloader.raw_m3u8
                    if raw_m3u8_path.exists() and raw_m3u8_path.stat().st_size > 0:
                        with open(raw_m3u8_path, 'r', encoding='utf-8') as f:
                            m3u8_content = f.read()
                except Exception:
                    pass
            
            # Initialize parser with cached content if available, or URL for fetching
            m3u8_parser = M3U8Parser(self.m3u8_url, self.headers, content=m3u8_content)
            if not m3u8_content:
                m3u8_parser.fetch()
            
            drm_info = m3u8_parser.get_drm_info()
            widevine_pssh = drm_info.get("widevine", [])
            playready_pssh = drm_info.get("playready", [])
            
            # Determine which DRM to use based on preference and availability
            selected_drm = None
            selected_pssh = None
            
            if self.drm_preference == 'widevine' and widevine_pssh:
                selected_drm = 'widevine'
                selected_pssh = widevine_pssh
            elif self.drm_preference == 'playready' and playready_pssh:
                selected_drm = 'playready'
                selected_pssh = playready_pssh
            
            # If no PSSH in master, check variant playlists
            if not selected_pssh:
                master_lines = m3u8_parser.content.splitlines()
                variant_urls = []
                base_url = self.m3u8_url.split('?')[0].rsplit('/', 1)[0]
                url_params = self.m3u8_url.split('?', 1)[1] if '?' in self.m3u8_url else ""

                for line in master_lines:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        if line.startswith('http'):
                            variant_urls.append(line)
                        else:
                            # Build absolute URL from relative path
                            full_rel = f"{base_url}/{line}"
                            if url_params and '?' not in full_rel:
                                full_rel = f"{full_rel}?{url_params}"
                            variant_urls.append(full_rel)

                # Also check EXT-X-I-FRAME-STREAM-INF
                iframe_matches = re.findall(r'#EXT-X-I-FRAME-STREAM-INF:.*?URI="(.*?)"', m3u8_parser.content)
                for iframe_rel in iframe_matches:
                    if iframe_rel.startswith('http'):
                        variant_urls.append(iframe_rel)
                    else:
                        full_rel = f"{base_url}/{iframe_rel}"
                        if url_params and '?' not in full_rel:
                            full_rel = f"{full_rel}?{url_params}"
                        variant_urls.append(full_rel)

                for v_url in variant_urls[:5]: # Check first few variants
                    console.print(f"[dim]Checking variant for DRM: {v_url[:60]}...")
                    variant_parser = M3U8Parser(v_url, self.headers)
                    variant_parser.fetch()
                    variant_drm = variant_parser.get_drm_info()

                    # Check preference order
                    if self.drm_preference == 'widevine' and variant_drm.get("widevine"):
                        widevine_pssh.extend(variant_drm["widevine"])
                        selected_drm = 'widevine'
                        selected_pssh = widevine_pssh
                        break
                    elif self.drm_preference == 'playready' and variant_drm.get("playready"):
                        playready_pssh.extend(variant_drm["playready"])
                        selected_drm = 'playready'
                        selected_pssh = playready_pssh
                        break

            # Extract keys for selected DRM
            if selected_pssh:
                kids = m3u8_parser.get_kids(selected_pssh)
                pssh_dicts = []
                for i, pssh_item in enumerate(selected_pssh):
                    if isinstance(pssh_item, dict):
                        entry = {'pssh': pssh_item['pssh'], 'type': pssh_item.get('type', selected_drm.capitalize())}
                    else:
                        entry = {'pssh': pssh_item, 'type': selected_drm.capitalize()}
                    
                    if i < len(kids):
                        entry['kid'] = kids[i]
                    pssh_dicts.append(entry)
                
                # Fetch decryption keys using DRM Manager
                if selected_drm == 'widevine':
                    keys = drm_manager.get_wv_keys(pssh_dicts, self.license_url, self.license_headers, self.key)
                elif selected_drm == 'playready':
                    keys = drm_manager.get_pr_keys(pssh_dicts, self.license_url, self.license_headers, self.key)
                else:
                    keys = None

                if keys:
                    self.media_downloader.set_key(keys)
                else:
                    console.print(f"[red]Warning: No {selected_drm.upper()} decryption keys found")
            elif self.license_url and not self.key:
                console.print("[red]Error: DRM detected but no PSSH found and no manual key provided")
        
        # Create output directory
        os_manager.create_path(self.output_dir)

        if SKIP_DOWNLOAD:
            console.print("[yellow]Skipping download as per configuration.")
            return self.output_path, False
        
        # Create media player ignore files to prevent media scanners
        try:
            self.media_players = MediaPlayers(self.output_dir)
            self.media_players.create()
        except Exception:
            pass
        
        if self.download_id:
            download_tracker.update_status(self.download_id, "Downloading ...")
        
        if context_tracker.should_print:
            console.print("[dim]\nStarting download ...")
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

        # Merge files using FFmpeg
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
        
        # Move subtitle files if any were copied without merging
        self._move_copied_subtitles()
        
        # Move audio files if any were copied without merging
        self._move_copied_audios()
        
        # Print summary and cleanup
        if context_tracker.should_print:
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
    
    def _merge_files(self, status) -> Optional[str]:
        """Merge downloaded files using FFmpeg"""
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
        
        video_path = status['video'].get('path')
        
        if not os.path.exists(video_path):
            console.print(f"[red]Video file not found: {video_path}, continuing with available tracks.")
        
        # If no additional tracks, mux video using join_video
        if not status['audios'] and not status['subtitles']:
            if context_tracker.should_print:
                console.print("[dim]\nNo additional tracks, muxing video...[/dim]")
            merged_file, result_json = join_video(
                video_path=video_path,
                out_path=self.output_path,
                log_path=os.path.join(self.output_dir, "video_mux.log")
            )
            self.last_merge_result = result_json
            if os.path.exists(merged_file):
                return merged_file
            else:
                self.error = "Video mux failed"
                return None
        
        current_file = video_path
        
        # Merge or track audio tracks
        if status['audios']:
            if MERGE_AUDIO:
                if context_tracker.should_print:
                    console.print(f"[dim]\nMerging [bold]{len(status['audios'])}[/bold] audio track(s)...")
                audio_output = os.path.join(self.output_dir, f"{self.filename_base}_with_audio.{EXTENSION_OUTPUT}")
                
                merged_file, use_shortest, result_json = join_audios(
                    video_path=current_file,
                    audio_tracks=status['audios'],
                    out_path=audio_output,
                    log_path=os.path.join(self.output_dir, "audio_merge.log")
                )
                self.last_merge_result = result_json
                
                if os.path.exists(merged_file):
                    current_file = merged_file
                else:
                    console.print("[yellow]Audio merge failed, continuing with video only")
            else:
                console.print("[cyan]Track audio tracks.")
                self._track_audios_for_copy(status['audios'])
        
        # Merge subtitles if enabled and present
        if status['subtitles']:
            if MERGE_SUBTITLES:
                if context_tracker.should_print:
                    console.print(f"[dim]\nMerging [bold]{len(status['subtitles'])}[/bold] subtitle track(s)...")
                sub_output = os.path.join(self.output_dir, f"{self.filename_base}_final.{EXTENSION_OUTPUT}")
                
                merged_file, result_json = join_subtitles(
                    video_path=current_file,
                    subtitles_list=status['subtitles'],
                    out_path=sub_output,
                    log_path=os.path.join(self.output_dir, "sub_merge.log")
                )
                self.last_merge_result = result_json
                
                if os.path.exists(merged_file):
                    current_file = merged_file
                else:
                    console.print("[yellow]Subtitle merge failed, continuing without subtitles")
            else:
                self._track_subtitles_for_copy(status['subtitles'])

        return current_file
    
    def _track_subtitles_for_copy(self, subtitles_list):
        """Track subtitle paths for later copying to final location."""
        for idx, subtitle in enumerate(subtitles_list):
            sub_path = subtitle.get('path')
            if sub_path and os.path.exists(sub_path):
                language = subtitle.get('language', f'sub{idx}')
                extension = os.path.splitext(sub_path)[1]
                self.copied_subtitles.append({
                    'src': sub_path,
                    'language': language,
                    'extension': extension
                })

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

    def _move_copied_subtitles(self):
        """Move tracked subtitle files to final output directory if copied_subtitles exits."""
        if not self.copied_subtitles:
            return
        
        output_dir = os.path.dirname(self.output_path)
        filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        console.print("[cyan]Copy the subtitles to the final path.")
        
        for sub_info in self.copied_subtitles:
            src_path = sub_info['src']
            language = sub_info['language']
            extension = sub_info['extension']
            
            # final name
            dst_path = os.path.join(output_dir, f"{filename_base}.{language}{extension}")
            
            try:
                shutil.copy2(src_path, dst_path)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not move subtitle {language}: {e}")

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

    def _print_summary(self):
        """Print download summary"""
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