# 05.01.26

import os
import json
import shutil
import logging
from typing import Dict

from rich.console import Console

from VibraVid.utils import config_manager, os_manager, internet_manager
from VibraVid.utils.http_client import get_headers
from VibraVid.setup import get_wvd_path, get_prd_path
from VibraVid.core.processors import join_video, join_audios, join_subtitles
from VibraVid.core.processors.helper.nfo import create_nfo
from VibraVid.source.utils.tracker import download_tracker, context_tracker
from VibraVid.source.utils.media_players import MediaPlayers
from VibraVid.cli.run import execute_hooks

from ..parser import MPDParser, DRMSystem
from ..drm import DRMManager

from VibraVid.source.N_m3u8 import MediaDownloader


console = Console()
CLEANUP_TMP = config_manager.config.get_bool('DOWNLOAD', 'cleanup_tmp_folder')
EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
SKIP_DOWNLOAD = config_manager.config.get_bool('DOWNLOAD', 'skip_download')
CREATE_NFO_FILES = config_manager.config.get_bool('PROCESS', 'generate_nfo', default=False)
AUDIO_FILTER = config_manager.config.get('DOWNLOAD', 'select_audio')
SUBTITLE_FILTER = config_manager.config.get('DOWNLOAD', 'select_subtitle')
MERGE_SUBTITLES = config_manager.config.get_bool('PROCESS', 'merge_subtitle', default=True)
MERGE_AUDIO = config_manager.config.get_bool('PROCESS', 'merge_audio', default=True)


class DASH_Downloader:
    def __init__(self, license_url: str, license_headers: Dict[str, str] = None, mpd_url: str = None, mpd_headers: Dict[str, str] = None, mpd_sub_list: list = None, mpd_audio_list: list = None, output_path: str = None, drm_preference: str = 'widevine', decrypt_preference: str = "bento4", key: str = None, cookies: Dict[str, str] = None):
        """
        Initialize DASH Downloader.
        
        Parameters:
            license_url: URL to obtain DRM license
            mpd_url: URL of the MPD manifest
            mpd_sub_list: List of subtitle dicts (unused with MediaDownloader)
            mpd_audio_list: List of additional audio MPDs with structure [{"url": "...", "language": "...", "headers": {...}}, ...]
            output_path: Full path including filename and extension (e.g., /path/to/video.mp4)
            drm_preference: Preferred DRM system ('widevine', 'playready', 'auto')
        """
        self.mpd_url = str(mpd_url).strip() if mpd_url else None
        self.license_url = str(license_url).strip() if license_url else None
        self.mpd_headers = mpd_headers or get_headers()
        self.license_headers = license_headers
        self.mpd_sub_list = mpd_sub_list or []
        self.mpd_audio_list = mpd_audio_list or []
        self.drm_preference = drm_preference.lower()
        self.key = key
        self.cookies = cookies or {}
        self.decrypt_preference = decrypt_preference.lower()
        self.drm_manager = DRMManager(get_wvd_path(), get_prd_path(), config_manager.config.get_dict('DRM', 'widevine'), config_manager.config.get_dict('DRM', 'playready'))
        
        # Tracking IDs - check context if not provided
        self.download_id = context_tracker.download_id
        self.site_name = context_tracker.site_name
        self.raw_mpd_path = None
        
        # Setup output path
        self.output_path = os_manager.get_sanitize_path(output_path)
        if not self.output_path.endswith(f'.{EXTENSION_OUTPUT}'):
            self.output_path += f'.{EXTENSION_OUTPUT}'
        
        self.filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        self.output_dir = os.path.join(os.path.dirname(self.output_path), self.filename_base + "_dash_temp")
        self.file_already_exists = os.path.exists(self.output_path)
        
        # DRM and state
        self.drm_info = None
        self.decryption_keys = []
        self.media_downloader = None
        self.meta_json = self.meta_selected = self.raw_mpd = None
        self.error = None
        self.last_merge_result = None
        self.media_players = None
        self.copied_subtitles = []
        self.copied_audios = []
        self.audio_only = False
    
    def _setup_drm_info(self, selected_ids, selected_kids, selected_langs, selected_periods):
        """Fetch and setup DRM information."""
        try:
            parser = MPDParser(self.mpd_url, headers=self.mpd_headers)
            parser.parse_from_file(self.raw_mpd)
            
            # Get DRM info
            self.drm_info = parser.get_drm_info(
                self.drm_preference, selected_ids, selected_kids, 
                selected_langs, selected_periods
            )
            return True
        
        except Exception as e:
            console.print(f"[yellow]Warning parsing MPD: {e}")
            return False
    
    def _fetch_decryption_keys(self):
        """Fetch decryption keys based on DRM type."""
        if self.drm_info.get('available_drm_types') and not self.license_url and not self.key:
            console.print("[yellow]DRM detected but missing both license_url and key. Cannot proceed.")
            self.error = "Missing license_url and key for DRM-protected content"
            return False

        drm_type = self.drm_info['selected_drm_type']
        if self.download_id:
            download_tracker.update_status(self.download_id, f"Fetching {drm_type} keys ...")

        keys = self._get_keys_for_drm_info(self.drm_info, self.license_url, self.license_headers, self.key)
        if keys:
            self.decryption_keys = keys
            return True
        self.error = "Failed to fetch decryption keys"
        return False

    def _get_keys_for_drm_info(self, drm_info: dict, license_url: str, license_headers: dict, key: str) -> list:
        """Dispatch Widevine/PlayReady key fetch for a given drm_info block. Returns key list or []."""
        drm_type = drm_info["selected_drm_type"]
        try:
            if drm_type == DRMSystem.WIDEVINE:
                return self.drm_manager.get_wv_keys(drm_info.get("widevine_pssh", []), license_url, license_headers, key) or []
            elif drm_type == DRMSystem.PLAYREADY:
                return self.drm_manager.get_pr_keys(drm_info.get("playready_pssh", []), license_url, license_headers, key) or []
            else:
                console.print(f"[red]Unsupported DRM type: {drm_type}")
                return []
        except Exception as e:
            console.print(f"[red]Error fetching keys: {e}")
            return []

    def _parse_meta_items(self, data: list, selected_ids: list, selected_kids: list, selected_langs: list, selected_periods: list) -> None:
        """Parse a list of meta track items into the provided accumulator lists (in-place)."""
        for item in data:
            if str(item.get("GroupId", "")).lower().startswith(("image", "thumb")):
                continue
            self._extract_ids(item, selected_ids)
            if lang := item.get("Language"):
                selected_langs.append(lang.lower())
            if pid := item.get("PeriodId"):
                selected_periods.append(str(pid))
            self._extract_kids_from_encryptinfo(item, selected_kids)

    def _extract_selected_track_info(self):
        """Extract selected track information from metadata files."""
        selected_ids, selected_kids, selected_langs, selected_periods = [], [], [], []
        has_video_in_selected = False

        # For Manual downloader, extract from raw MPD if available
        if hasattr(self.media_downloader, 'get_selected_ids_from_mpd') and self.raw_mpd:
            return self.media_downloader.get_selected_ids_from_mpd(self.raw_mpd)

        # 1. Process meta_selected first if it exists
        if self.meta_selected and os.path.exists(self.meta_selected):
            try:
                with open(self.meta_selected, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                for item in data:
                    if item.get("Resolution") or item.get("MediaType") == "VIDEO":
                        has_video_in_selected = True
                self._parse_meta_items(data, selected_ids, selected_kids, selected_langs, selected_periods)
            except Exception as e:
                console.print(f"[yellow]Warning reading {self.meta_selected}: {e}")

        # 2. Process meta_json for best video ONLY if no video was found in meta_selected
        force_best = getattr(self.media_downloader, "force_best_video", False)
        if not has_video_in_selected and force_best and os.path.exists(self.meta_json):
            try:
                with open(self.meta_json, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                self._parse_meta_items(self._find_best_video(data), selected_ids, selected_kids, selected_langs, selected_periods)
            except Exception as e:
                console.print(f"[yellow]Warning reading {self.meta_json}: {e}")

        return (list(dict.fromkeys(selected_ids)), list(dict.fromkeys(selected_kids)), list(dict.fromkeys(selected_langs)), list(dict.fromkeys(selected_periods)))
    
    def _find_best_video(self, data):
        """Find best video track based on bandwidth."""
        videos = [
            item for item in data 
            if not str(item.get("GroupId", "")).lower().startswith(("image", "thumb"))
            and (item.get("Resolution") or item.get("MediaType") == "VIDEO") 
            and item.get("Bandwidth")
        ]
        return [max(videos, key=lambda x: x.get("Bandwidth", 0))] if videos else []
    
    def _extract_ids(self, item, selected_ids):
        """Extract IDs from item, prioritizing specific ID over GroupId."""
        extracted = []
        tid = item.get("Id", "")
        if tid:
            tid_s = str(tid)
            selected_ids.append(tid_s)
            extracted.append(tid_s)
            if ":" in tid_s:
                part = tid_s.split(":")[-1]
                selected_ids.append(part)
                extracted.append(part)
            if "-" in tid_s:
                part = tid_s.split("-")[-1]
                selected_ids.append(part)
                extracted.append(part)

        elif gid := item.get("GroupId"):
            gid_s = str(gid)
            selected_ids.append(gid_s)
            extracted.append(gid_s)
    
    def _extract_kids_from_encryptinfo(self, item, selected_kids):
        """Extract KIDs from EncryptInfo in MediaInit or MediaSegments."""
        playlist = item.get("Playlist", {})
        for part in playlist.get("MediaParts", []):

            # Check MediaInit for KID (common in PlutoTV and others)
            if init := part.get("MediaInit"):
                if kid_val := init.get("EncryptInfo", {}).get("KID"):
                    selected_kids.append(kid_val.lower().replace("-", ""))
            
            # Check MediaSegments for KID
            for seg in part.get("MediaSegments", []):
                if kid_val := seg.get("EncryptInfo", {}).get("KID"):
                    selected_kids.append(kid_val.lower().replace("-", ""))
    
    def _fetch_keys_for_audio(self, audio_url: str, audio_headers: dict, audio_meta_json: str, audio_meta_selected: str, audio_raw_mpd: str, audio_license_url: str = None, audio_license_headers: dict = None) -> list:
        """Full DRM flow for an extra audio MPD. Returns key list or []."""
        selected_ids, selected_kids, selected_langs, selected_periods = [], [], [], []

        for meta_path in [audio_meta_selected, audio_meta_json]:
            if not meta_path or not os.path.exists(meta_path):
                continue
            try:
                with open(meta_path, "r", encoding="utf-8-sig") as f:
                    self._parse_meta_items(json.load(f), selected_ids, selected_kids, selected_langs, selected_periods)
            except Exception as e:
                console.print(f"[yellow]Warning reading audio metadata {meta_path}: {e}")

        for lst in (selected_ids, selected_kids, selected_langs, selected_periods):
            lst[:] = list(dict.fromkeys(lst))

        try:
            parser = MPDParser(audio_url, headers=audio_headers)
            parser.parse_from_file(audio_raw_mpd)
            drm_info = parser.get_drm_info(self.drm_preference, selected_ids, selected_kids, selected_langs, selected_periods)
        except Exception as e:
            console.print(f"[yellow]Warning parsing audio MPD for DRM: {e}")
            return []

        if not drm_info or not drm_info.get("available_drm_types"):
            return []

        # Priority: audio-specific license > main license
        effective_license_url     = audio_license_url or self.license_url
        effective_license_headers = audio_license_headers or self.license_headers
        return self._get_keys_for_drm_info(drm_info, effective_license_url, effective_license_headers, self.key)

    def _download_extra_audios(self) -> list:
        """
        Download extra audio tracks from separate MPD URLs.
        For each audio:
          1. Creates dedicated MediaDownloader (audio-only, no video; subtitles included)
          2. Runs parser_stream() to get metadata + KIDs
          3. Fetches DRM keys specific to this audio KID
          4. Downloads with correct keys
          5. Moves result to main temp dir
        Returns a list of dicts compatible with status['external_audios'].
        """
        external_audios = []
        external_subtitles = []  # subtitles found in extra audio MPDs

        for audio_spec in self.mpd_audio_list:
            audio_url             = audio_spec.get("url")
            audio_language        = audio_spec.get("language", "und")
            audio_headers         = audio_spec.get("headers") or self.mpd_headers
            audio_license_url     = audio_spec.get("license_url") or self.license_url
            audio_license_headers = audio_spec.get("license_headers")

            if not audio_url:
                console.print(f"[yellow]Skipping extra audio '{audio_language}': missing url")
                continue
            
            # Dedicated temp di
            audio_temp_dir = os.path.join(self.output_dir, f"audio_{audio_language}_temp")
            os_manager.create_path(audio_temp_dir)
            audio_filename = self.filename_base

            try:
                audio_downloader = MediaDownloader(
                    url=audio_url,
                    output_dir=audio_temp_dir,
                    filename=audio_filename,
                    headers=audio_headers,
                    cookies=self.cookies,
                    decrypt_preference=self.decrypt_preference,
                    download_id=None,
                    site_name=self.site_name,
                )
                audio_downloader.license_url = audio_license_url
                audio_downloader.drm_type    = self.drm_preference

                # Drop video; for extra audios, select best audio regardless of language tag
                # !!!!!! forse da cambiare in futuro
                audio_downloader.custom_filters = {
                    "video": "false",
                    "audio": "for=best",
                    "subtitle": SUBTITLE_FILTER,
                }

                # --- Parse for extra audios  ---
                if self.download_id:
                    download_tracker.update_status(self.download_id, f"Parsing audio {audio_language}...")
                console.print(f"\n[dim]Parsing DASH for audio {audio_language} ...")
                audio_downloader.parser_stream(show_table=False)

                # Get metadata paths for DRM extraction
                a_meta_json, a_meta_selected, _, a_raw_mpd, _ = audio_downloader.get_metadata()

                # --- Fetch DRM keys specific to this audio's KID ---
                audio_keys = self._fetch_keys_for_audio(
                    audio_url, audio_headers,
                    a_meta_json, a_meta_selected, a_raw_mpd,
                    audio_license_url=audio_license_url,
                    audio_license_headers=audio_license_headers,
                )

                if not audio_keys:
                    console.print(f"[yellow]Warning: No keys found for audio {audio_language}, skipping...")
                    continue

                audio_downloader.set_key(audio_keys)

                if self.download_id:
                    download_tracker.update_status(self.download_id, f"Downloading audio {audio_language}...")
                
                console.print(f"\n[dim]Starting download for audio {audio_language}...")
                audio_status = audio_downloader.start_download()

                if audio_status.get("error"):
                    console.print(f"[yellow]Error downloading audio {audio_language}: {audio_status['error']}")
                    continue

                # --- Collect and rename result ---
                for audio_file in audio_status.get("audios", []):
                    fpath = audio_file.get("path")
                    if fpath and os.path.exists(fpath):
                        ext = os.path.splitext(fpath)[1]
                        final_path = os.path.join(self.output_dir, f"{self.filename_base}.{audio_language}{ext}")
                        try:
                            shutil.move(fpath, final_path)
                            external_audios.append({
                                "file":     os.path.basename(final_path),
                                "language": audio_language,
                                "path":     final_path,
                            })
                        except Exception as e:
                            console.print(f"[yellow]Could not move audio {audio_language}: {e}")

                # --- Collect subtitles downloaded from this MPD ---
                for sub_file in audio_status.get("subtitles", []):
                    fpath = sub_file.get("path")
                    if fpath and os.path.exists(fpath):
                        ext = os.path.splitext(fpath)[1]
                        sub_lang = sub_file.get("language") or sub_file.get("name") or audio_language
                        final_sub_path = os.path.join(self.output_dir, f"{self.filename_base}.{sub_lang}{ext}")
                        try:
                            shutil.move(fpath, final_sub_path)
                            external_subtitles.append({
                                "path":     final_sub_path,
                                "language": sub_lang,
                                "name":     sub_lang,
                                "size":     os.path.getsize(final_sub_path),
                            })
                            console.print(f"[dim]Extra subtitle [cyan]{sub_lang}[/cyan] from {audio_language} MPD ready.")
                        except Exception as e:
                            console.print(f"[yellow]Could not move subtitle {sub_lang}: {e}")

            except Exception as e:
                console.print(f"[yellow]Warning on extra audio {audio_language}: {e}")

            finally:
                # Copy log files before cleanup
                try:
                    for log_file in os.listdir(audio_temp_dir):
                        if 'parsing' in log_file or 'log' in log_file:
                            src = os.path.join(audio_temp_dir, log_file)
                            dst = os.path.join(self.output_dir, f"{audio_language}_{log_file}")
                            if os.path.isfile(src):
                                shutil.copy2(src, dst)
                except Exception:
                    pass
                
                shutil.rmtree(audio_temp_dir, ignore_errors=True)

        return external_audios, external_subtitles

    def start(self):
        """Main execution flow for downloading DASH content."""
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
            url=self.mpd_url,
            output_dir=self.output_dir,
            filename=self.filename_base,
            headers=self.mpd_headers,
            cookies=self.cookies,
            decrypt_preference=self.decrypt_preference,
            download_id=self.download_id,
            site_name=self.site_name,
        )
        
        # Store DRM info for later use in manual decryption
        self.media_downloader.license_url = self.license_url
        self.media_downloader.drm_type = self.drm_preference
        
        if self.mpd_sub_list and SUBTITLE_FILTER != "false":
            console.print(f"[dim]Adding {len(self.mpd_sub_list)} external subtitle(s) to the downloader.")
            self.media_downloader.external_subtitles = self.mpd_sub_list

        if self.mpd_audio_list and AUDIO_FILTER != "false":
            console.print(f"[dim]Adding {len(self.mpd_audio_list)} extra audio track(s) to the downloader.")
        
        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing DASH...")
        
        console.print("[dim]Parsing DASH ...")
        self.media_downloader.parser_stream()
        
        # Get metadata
        self.meta_json, self.meta_selected, _, self.raw_mpd, _ = self.media_downloader.get_metadata()
        
        # Extract selected track info
        selected_info = self._extract_selected_track_info()
        
        # Fetch DRM info
        if not self._setup_drm_info(*selected_info):
            logging.error("Failed to fetch DRM info")
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="DRM parsing failed")
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

        # Download extra audio tracks (separate MPDs, one per language)
        if self.mpd_audio_list:
            if self.download_id:
                download_tracker.update_status(self.download_id, f"Downloading {len(self.mpd_audio_list)} extra audio track(s)...")
            
            extra_audios, extra_subtitles = self._download_extra_audios()
            status["external_audios"] = extra_audios
            if extra_subtitles:
                existing_sub_paths = {s.get("path") for s in status.get("subtitles", [])}
                for sub in extra_subtitles:
                    if sub.get("path") not in existing_sub_paths:
                        status["subtitles"].append(sub)
                        existing_sub_paths.add(sub.get("path"))

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
        
        # Move subtitle files if any were copied without merging
        self._move_copied_subtitles()
        
        # Move audio files if any were copied without merging
        self._move_copied_audios()
        
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
        
        # If no additional tracks, just mux video
        audio_tracks = status['audios'] or []
        external_audios = status.get('external_audios', [])
        
        # Convert external_audios format to match audio_tracks format
        if external_audios:
            for ext_audio in external_audios:
                audio_tracks.append({
                    'path': ext_audio.get('path'),
                    'name': ext_audio.get('language') or ext_audio.get('file'),
                    'size': os.path.getsize(ext_audio.get('path')) if os.path.exists(ext_audio.get('path')) else 0
                })
        
        if not audio_tracks and not status['subtitles']:
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
        if audio_tracks:
            if MERGE_AUDIO:
                current_file = self._merge_audio_tracks(current_file, audio_tracks)
            else:
                self._track_audios_for_copy(audio_tracks)
        
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
    
    def _move_copied_subtitles(self):
        """Move tracked subtitle files to final output directory."""
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