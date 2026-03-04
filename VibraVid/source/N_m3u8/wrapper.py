# 04.01.25

import re
import asyncio
import platform
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime
from contextlib import nullcontext


# External
from rich.console import Console
from rich.progress import Progress, TextColumn


# Internal 
from VibraVid.utils.config import config_manager
from VibraVid.utils.os import internet_manager
from VibraVid.setup import get_ffmpeg_path, get_n_m3u8dl_re_path, get_bento4_decrypt_path, get_shaka_packager_path
from VibraVid.source.utils.tracker import download_tracker, context_tracker
from VibraVid.utils.http_client import create_async_client
from VibraVid.source.utils.trans_codec import get_subtitle_codec_name
from VibraVid.source.Manual.decrypt.decrypt import Decryptor


# Logic
from ..utils.object import StreamInfo, KeysManager
from .pattern import VIDEO_LINE_RE, AUDIO_LINE_RE, SUBTITLE_LINE_RE, SEGMENT_RE, PERCENT_RE, SPEED_RE, SIZE_RE, SUBTITLE_FINAL_SIZE_RE
from .progress_bar import CustomBarColumn, ColoredSegmentColumn, CompactTimeColumn, CompactTimeRemainingColumn, SizeColumn
from .parser import parse_meta_json, LogParser
from .ui import build_table


# Variable
console = Console(force_terminal=True if platform.system().lower() != 'windows' else None)
auto_select_cfg = config_manager.config.get_bool('DOWNLOAD', 'auto_select', default=True)
video_filter = config_manager.config.get("DOWNLOAD", "select_video")
audio_filter = config_manager.config.get("DOWNLOAD", "select_audio")
subtitle_filter = config_manager.config.get("DOWNLOAD", "select_subtitle")
max_speed = config_manager.config.get("DOWNLOAD", "max_speed")
concurrent_download = config_manager.config.get_int("DOWNLOAD", "concurrent_download")
retry_count = config_manager.config.get_int("DOWNLOAD", "retry_count")
request_timeout = config_manager.config.get_int("REQUESTS", "timeout")
thread_count = config_manager.config.get_int("DOWNLOAD", "thread_count")
use_proxy = config_manager.config.get_bool("REQUESTS", "use_proxy")
configuration_proxy = config_manager.config.get_dict("REQUESTS", "proxy", default={})


class MediaDownloader:
    def __init__(self, url: str, output_dir: str, filename: str, headers: Optional[Dict] = None, key: Optional[str] = None, cookies: Optional[Dict] = None, decrypt_preference: str = "shaka", download_id: str = None, site_name: str = None):
        self.url = url
        self.output_dir = Path(output_dir)
        self.filename = filename
        self.headers = headers or {}
        self.key = key
        self.cookies = cookies or {}
        self.decrypt_preference = decrypt_preference.strip().lower()
        self.download_id = download_id
        self.site_name = site_name
        self.streams = []
        self.external_subtitles = []
        self.force_best_video = False
        self.meta_json_path, self.meta_selected_path, self.raw_m3u8, self.raw_mpd, self.raw_ism = None, None, None, None, None 
        self.status = None
        self.manifest_type = "Unknown"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir_type = "Movie" if config_manager.config.get("OUTPUT", "movie_folder_name") in str(self.output_dir) else "TV" if config_manager.config.get("OUTPUT", "serie_folder_name") in str(self.output_dir) else "Anime" if config_manager.config.get("OUTPUT", "anime_folder_name") in str(self.output_dir) else "other"

        # Track in GUI if ID is provided
        if self.download_id:
            download_tracker.start_download(self.download_id, self.filename, self.site_name or "Unknown", self.output_dir_type)

    def _normalize_filter(self, filter_value: str) -> str:
        """Normalize filter ensuring values are quoted if they contain special characters"""
        if not filter_value:
            return filter_value
        
        parts, normalized_parts, special_chars = filter_value.split(':'), [], '|=.*+?[]{}()^$'
        for part in parts:
            if '=' in part:
                key, val = part.split('=', 1)
                val = val.strip("'\"")
                normalized_parts.append(f'{key}="{val}"' if any(c in val for c in special_chars) else f'{key}={val}')
            else:
                normalized_parts.append(part)
        
        return ':'.join(normalized_parts)

    def _get_common_args(self) -> List[str]:
        """Get common command line arguments for N_m3u8DL-RE"""
        cmd = []
        if self.headers:
            cmd.extend([item for k, v in self.headers.items() for item in ["--header", f"{k}: {v}"]])

        if self.cookies and (cookie_str := "; ".join(f"{k}={v}" for k, v in self.cookies.items())):
            cmd.extend(["--header", f"Cookie: {cookie_str}"])

        if use_proxy and (proxy_url := configuration_proxy.get("https") or configuration_proxy.get("http")):
            cmd.extend(["--use-system-proxy", "false", "--custom-proxy", proxy_url])
        
        if auto_select_cfg:
            cmd.extend(["--force-ansi-console", "--no-ansi-color"])
        return cmd
    
    def determine_decryption_tool(self) -> str:
        """Determine decryption tool based on preference and availability"""
        if self.decrypt_preference == "bento4":
            return get_bento4_decrypt_path()
        if self.decrypt_preference == "shaka":
            return get_shaka_packager_path()

    def _match_external_subtitle_lang(self, ext_lang: str) -> bool:
        """Check if external subtitle language matches filter"""
        if not ext_lang or not subtitle_filter:
            return False
        
        try:
            if lang_match := re.search(r"lang=['\"]([^'\"]+)['\"]", subtitle_filter):
                return any(t.lower() == ext_lang.lower() or ext_lang.lower().startswith(t.lower()) or t.lower() in ext_lang.lower() for t in [x.strip() for x in lang_match.group(1).split('|') if x.strip()])
            return any(t.lower() in ext_lang.lower() for t in re.findall(r"[A-Za-z]{2,}", subtitle_filter))
        except Exception:
            return False

    def parser_stream(self, show_table: bool = True) -> List[StreamInfo]:
        """Analyze playlist and display table of available streams"""
        analysis_path = self.output_dir / "analysis_temp"
        analysis_path.mkdir(exist_ok=True)
        if self.download_id:
            download_tracker.update_status(self.download_id, "Parsing...")

        # Normalize filter values
        filters = getattr(self, 'custom_filters', None)
        norm_v = self._normalize_filter(filters['video'] if filters and filters.get('video') else video_filter)
        norm_a = self._normalize_filter(filters['audio'] if filters and filters.get('audio') else audio_filter)
        norm_s = self._normalize_filter(filters['subtitle'] if filters and filters.get('subtitle') else subtitle_filter)
        
        cmd = [
            get_n_m3u8dl_re_path(), 
            "--write-meta-json", 
            "--no-log", 
            "--save-dir", str(analysis_path), 
            "--tmp-dir", str(analysis_path),
            "--save-name", "temp_analysis", 
            "--select-video", norm_v, 
            "--select-audio", norm_a, 
            "--select-subtitle", norm_s, 
            "--skip-download"
        ]
        cmd.extend(self._get_common_args())
        cmd.append(self.url)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors='replace', bufsize=1, universal_newlines=True)
        
        # Save parsing log
        log_path = self.output_dir / f"{self.filename}_parsing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        with open(log_path, 'w', encoding='utf-8', errors='replace') as log_file:
            log_file.write(f"Command: {' '.join(cmd)}\n{'='*80}\n\n")
            log_parser = LogParser()
            for line in proc.stdout:
                if line := line.rstrip():
                    log_parser.parse_line(line)
                    log_file.write(line + "\n")
                    log_file.flush()
            proc.wait()
        
        analysis_dir = analysis_path / "temp_analysis"
        self.meta_json_path = analysis_dir / "meta.json"
        self.meta_selected_path = analysis_dir / "meta_selected.json"
        self.raw_m3u8 = analysis_dir / "raw.m3u8"
        self.raw_mpd = analysis_dir / "raw.mpd"
        self.raw_ism = analysis_dir / "raw.ism"
        
        # Determine manifest type
        self.manifest_type = "DASH" if self.raw_mpd.exists() else "HLS" if self.raw_m3u8.exists() else "ISM" if self.raw_ism.exists() else "Unknown"
        
        if self.meta_json_path.exists():
            self.streams = parse_meta_json(str(self.meta_json_path), str(self.meta_selected_path))

            # Check if video needs to be forced
            try:
                has_video = any(s.type == "Video" for s in self.streams)
                video_selected = any(s.type == "Video" and s.selected for s in self.streams)
                if has_video and not video_selected:
                    console.print("[yellow]No video matched select_video filter; forcing 'best' for download[/yellow]")
                    self.force_best_video = True
            except Exception:
                self.force_best_video = False

            # Add external subtitles to stream list
            for ext_sub in self.external_subtitles:
                ext_lang = ext_sub.get('language', '') or ''
                selected = self._match_external_subtitle_lang(ext_lang)
                ext_type = ext_sub.get('type') or ext_sub.get('format') or 'srt'
                ext_sub['_selected'] = selected
                ext_sub['_ext'] = ext_type
                self.streams.append(StreamInfo(type_="Subtitle [red]*EXT", language=ext_sub.get('language', ''), name=ext_sub.get('name', ''), selected=selected, extension=ext_type))

            if show_table:
                selected_set = {i for i, s in enumerate(self.streams) if getattr(s, 'selected', False)}
                console.print(build_table(self.streams, selected_set, 0, window_size=len(self.streams), highlight_cursor=False))
            return self.streams
        
        return []

    def get_metadata(self) -> tuple:
        """Get paths to metadata files"""
        return str(self.meta_json_path), str(self.meta_selected_path), str(self.raw_m3u8), str(self.raw_mpd), str(self.raw_ism)
    
    def set_key(self, key):
        """Set decryption key"""
        if isinstance(key, KeysManager):
            self.key = key.get_keys_list()
        else:
            self.key = key
    
    async def _download_external_subtitles(self):
        """Download external subtitles using httpx"""
        if not self.external_subtitles:
            return []
        
        downloaded = []
        async with create_async_client(headers=self.headers) as client:
            for idx, sub in enumerate(self.external_subtitles):
                try:
                    if not sub.get('_selected', True):
                        continue

                    url, lang = sub['url'], sub.get('language', 'unknown')
                    sub_type = sub.get('_ext') or sub.get('type') or sub.get('format') or 'srt'
                    original_type = sub.get('type')

                    # Handle 'captions' type getting mapped to wrong extension
                    if sub_type == 'captions':
                        sub_type = 'vtt'
                    
                    # Determine filename suffix
                    fname_suffix = lang
                    if original_type == 'captions' or original_type == 'closed_captions':
                        fname_suffix = f"{lang}_captions"
                    
                    sub_path = self.output_dir / f"{self.filename}.{fname_suffix}.{sub_type}"
                    response = await client.get(url)
                    response.raise_for_status()

                    with open(sub_path, 'wb') as f:
                        f.write(response.content)
                    downloaded.append({'path': str(sub_path), 'language': lang, 'type': sub_type, 'size': len(response.content)})
                    
                    # Update download progress for external subtitle
                    if self.download_id and download_tracker:
                        track_key = f"subtitle_{fname_suffix}"
                        download_tracker.update_status(self.download_id, "downloading")
                        download_tracker.update_progress(
                            self.download_id, 
                            track_key, 
                            progress=100.0,
                            size=f"{len(response.content) / 1024:.2f}KB",
                            speed="N/A",  # Too fast/small to calculate meaningful speed
                            segments="1/1",
                            status="completed"
                        )

                except Exception as e:
                    console.log(f"[red]Failed to download external subtitle: {e}[/red]")
                    if self.download_id and download_tracker:
                        download_tracker.update_progress(
                            self.download_id,
                            f"subtitle_{lang}_{idx}",
                            status="failed"
                        )
        return downloaded

    def start_download(self) -> Dict[str, Any]:
        """Start the download process"""
        filters = getattr(self, 'custom_filters', None)
        
        # Determine filters
        norm_v = self._normalize_filter(filters['video'] if filters and 'video' in filters else ("best" if getattr(self, "force_best_video", False) else video_filter))
        norm_a = self._normalize_filter(filters['audio'] if filters and 'audio' in filters else audio_filter)
        norm_s = self._normalize_filter(filters['subtitle'] if filters and 'subtitle' in filters else subtitle_filter)

        # Build command
        cmd = [
            get_n_m3u8dl_re_path(), 
            "--save-name", self.filename, 
            "--save-dir", str(self.output_dir), 
            "--tmp-dir", str(self.output_dir),
            "--ffmpeg-binary-path", get_ffmpeg_path(), 
            "--decryption-binary-path", self.determine_decryption_tool(),
            "--write-meta-json", "false", 
            "--binary-merge",
            "--del-after-done",
            "--auto-subtitle-fix", "false",
            "--check-segments-count", "false",
            "--mp4-real-time-decryption", "false"
        ]

        if auto_select_cfg:
            cmd.append("--no-log")
            if norm_v == "false":
                cmd.extend(["--drop-video", "all"])
            else:
                if norm_v:
                    cmd.extend(["--select-video", norm_v])
                else:
                    console.print("[dim]No video filter selected.")
            
            if norm_a == "false":
                cmd.extend(["--drop-audio", "all"])
            else:
                if norm_a:
                    cmd.extend(["--select-audio", norm_a])
                else:
                    console.print("[dim]No audio filter selected.")

            if norm_s == "false":
                cmd.extend(["--drop-subtitle", "all"])
            else:
                if norm_s:
                    cmd.extend(["--select-subtitle", norm_s])
                else:
                    console.print("[dim]No subtitle filter selected.")
        else:
            cmd.extend(["--log-level", "ERROR"])
        
        cmd.extend(self._get_common_args())

        # Add optional parameters
        if concurrent_download:
            cmd.append("--concurrent-download")
        if thread_count > 0:
            cmd.extend(["--thread-count", str(thread_count)])
        if request_timeout > 0:
            cmd.extend(["--http-request-timeout", str(request_timeout)])
        if retry_count > 0:
            cmd.extend(["--download-retry-count", str(retry_count)])
        if max_speed and str(max_speed).lower() != "false":
            cmd.extend(["--max-speed", max_speed])
        if self.key:
            keys_list = self.key.get_keys_list() if isinstance(self.key, KeysManager) else ([self.key] if isinstance(self.key, str) else self.key)
            for single_key in keys_list:
                cmd.extend(["--key", single_key])
        
        cmd.append(self.url)
        
        # Download external subtitles
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            external_subs = loop.run_until_complete(self._download_external_subtitles())
        finally:
            loop.close()
        
        log_parser = LogParser(show_warnings=False)
        log_path = self.output_dir / f"{self.filename}_download_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        subtitle_sizes = {}
        
        with open(log_path, 'w', encoding='utf-8', errors='replace') as log_file:
            log_file.write(f"Command: {' '.join(cmd)}\n{'='*80}\n\n")
            
            # In interactive mode (auto_select=false), don't use progress bar - just run n3u8dl directly
            if not auto_select_cfg:
                proc = subprocess.Popen(cmd)
                if self.download_id:
                    download_tracker.register_process(self.download_id, proc)
                proc.wait()

            else:
                progress_ctx = nullcontext() if context_tracker.is_gui else Progress(
                    TextColumn("[purple]{task.description}", justify="left"), CustomBarColumn(bar_width=40), ColoredSegmentColumn(),
                    TextColumn("[dim][[/dim]"), CompactTimeColumn(), TextColumn("[dim]<[/dim]"), CompactTimeRemainingColumn(), TextColumn("[dim]][/dim]"),
                    SizeColumn(), TextColumn("[dim]@[/dim]"), TextColumn("[red]{task.fields[speed]}[/red]", justify="right"), 
                    console=console,
                    refresh_per_second=10.0
                )
                
                with progress_ctx as progress:
                    tasks = {}
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors='replace', bufsize=1, universal_newlines=True)
                    
                    # Register process for potential termination
                    if self.download_id:
                        download_tracker.register_process(self.download_id, proc)

                    with proc:
                        for line in proc.stdout:
                            if self.download_id and download_tracker.is_stopped(self.download_id):
                                proc.terminate()
                                break
                            
                            log_file.write(line)
                            log_parser.parse_line(line)
                            self._parse_progress_line(line, progress, tasks, subtitle_sizes)
                        
                        # Ensure all tasks are complete
                        if progress:
                            for task_id in tasks.values():
                                progress.update(task_id, completed=100)
        
        # Check if we were cancelled
        if self.download_id and download_tracker.is_stopped(self.download_id):
            return {"error": "cancelled"}

        # Check for key retrieval errors (Succedde spesso quando parsa m3u8 che hanno bisogna di licenza, ma non ho ancora trovato un caso per implementare license per quel cazzo di m3u8 quindi amen va su failed).
        if any("Failed to get KEY" in error for error in log_parser.errors):
            self.status = {"error": "key_error", "message": "Failed to retrieve decryption key"}
            if self.download_id:
                download_tracker.complete_download(self.download_id, success=False, error="Failed to get decryption key")
            return self.status

        self.status = self._get_download_status(subtitle_sizes, external_subs)

        if self.key:
            # IL 99% delle volte n3u8dl riesce a fare tutto ma in quel 1% sti cazzi meglio fare double check anche se si perde tempo.
            self._manual_decrypt_check(self.status)

        return self.status

    def _manual_decrypt_check(self, status: Dict[str, Any]):
        """Check and manually decrypt files if they are still encrypted after download"""
        decryptor = Decryptor(preference=self.decrypt_preference, license_url=getattr(self, 'license_url', None), drm_type=getattr(self, 'drm_type', None))
        
        # Prepare targets with their respective stream types
        targets = []
        if status.get('video'):
            targets.append((status['video'], "video"))
        if status.get('audios'):
            for audio in status['audios']:
                targets.append((audio, "audio"))
            
        keys = self.key.get_keys_list() if isinstance(self.key, KeysManager) else ([self.key] if isinstance(self.key, str) else self.key)
        for target, stream_type in targets:
            file_path = Path(target['path'])
            if not file_path.exists():
                continue
                
            # Check if still encrypted
            console.print(f"[cyan]Check file [red]{file_path.name} [cyan]is still encrypted...")
            if decryptor.detect_encryption(str(file_path)):
                
                # Decrypt to a temporary file
                temp_output = file_path.with_suffix(file_path.suffix + ".decrypted")
                
                if decryptor.decrypt(str(file_path), keys, str(temp_output), stream_type=stream_type):
                    try:
                        # Replace the old file with the decrypted one
                        file_path.unlink()
                        temp_output.rename(file_path)
                        
                        # Update status with new size
                        target['size'] = file_path.stat().st_size
                    except Exception as e:
                        console.print(f"[red]Failed to replace encrypted file: {e}[/red]")
                        if temp_output.exists():
                            temp_output.unlink()
                else:
                    if temp_output.exists():
                        temp_output.unlink()
                    console.print(f"[red]Manual decryption failed for: {file_path.name}[/red]")

    def _update_task(self, progress, tasks: dict, key: str, label: str, line: str):
        """Generic task update helper"""
        if key not in tasks:
            if progress:
                tasks[key] = progress.add_task(f"[yellow]{self.manifest_type} {label}", total=100, segment="0/0", speed="0Bps", size="0B/0B")
            else:
                tasks[key] = "gui_only"
        
        task = tasks[key]
        cur_segment, cur_percent, cur_speed, cur_size = None, None, None, None

        if m := SEGMENT_RE.search(line):
            cur_segment = m.group(0)
            if progress and task != "gui_only":
                progress.update(task, segment=cur_segment)

        if m := PERCENT_RE.search(line):
            try:
                cur_percent = float(m.group(1))
                if progress and task != "gui_only":
                    progress.update(task, completed=cur_percent)
            except Exception:
                pass

        if m := SPEED_RE.search(line):
            cur_speed = m.group(1)
            if progress and task != "gui_only":
                progress.update(task, speed=cur_speed)

        if m := SIZE_RE.search(line):
            cur_size = f"{m.group(1)}/{m.group(2)}"
            if progress and task != "gui_only":
                progress.update(task, size=cur_size)

        if self.download_id:
            download_tracker.update_progress(self.download_id, key, cur_percent, cur_speed, cur_size, cur_segment)
        return task

    def _parse_progress_line(self, line: str, progress, tasks: dict, subtitle_sizes: dict):
        """Parse a progress line and update progress bars"""
        if line.startswith("Vid"):
            res = (VIDEO_LINE_RE.search(line).group(1) if VIDEO_LINE_RE.search(line) else next((s.resolution or s.extension or "main" for s in self.streams if s.type == "Video"), "main"))
            self._update_task(progress, tasks, f"video_{res}", f"[cyan]Vid [red]{res}", line)

        elif line.startswith("Aud"):
            if m := AUDIO_LINE_RE.search(line):
                bitrate, lang_name = m.group(1).strip(), m.group(2).strip()
                display = lang_name if any(c.isalpha() for c in lang_name) else next((s.language or s.name or bitrate for s in self.streams if s.type == "Audio" and s.bandwidth and bitrate in s.bandwidth), bitrate)
                self._update_task(progress, tasks, f"audio_{lang_name}_{bitrate}", f"[cyan]Aud [red]{display}", line)

        elif line.startswith("Sub"):
            if m := SUBTITLE_LINE_RE.search(line):
                lang, codec = m.group(1).strip(), m.group(2).strip()
                
                # SHIT Attempt to fix find actual language from streams if codec seems to be a tech type | TO REWRITE
                display_lang = lang
                if any(x in lang.lower() for x in ['stpp', 'ttml', 'vtt', 'srt']) or any(x in codec.lower() for x in ['stpp', 'ttml', 'vtt', 'srt']):
                    for s in self.streams:

                        # Check if codec matches and lang matches (as sub-string)
                        if s.type.lower().startswith('subtitle') and s.codec and (any(x in s.codec.lower() for x in ['stpp', 'ttml', 'vtt', 'srt'])):
                            s_lang = (s.language or "").lower()
                            p_lang = lang.lower()
                            if s_lang == p_lang or s_lang in p_lang or p_lang in s_lang:
                                if s.language:
                                    display_lang = s.language
                                    break
                
                # If still using tech name for display_lang, try to clean it
                display_lang = get_subtitle_codec_name(display_lang)
                task = self._update_task(progress, tasks, f"sub_{lang}_{codec}", f"[cyan]Sub [red]{display_lang}", line)

                if fm := SUBTITLE_FINAL_SIZE_RE.search(line):
                    final_size = fm.group(1)
                    if progress:
                        progress.update(task, size=final_size, completed=100)
                    subtitle_sizes[f"{lang}: {codec}"] = final_size
                
                elif not SIZE_RE.search(line):
                    if sm := re.search(r"(\d+\.\d+(?:B|KB|MB|GB))\s*$", line):
                        subtitle_sizes[f"{lang}: {codec}"] = sm.group(1)

    def _extract_language_from_filename(self, filename: str, base_name: str) -> str:
        """Extract language from filename"""
        stem = filename[len(base_name):].lstrip('.') if filename.startswith(base_name) else filename
        return stem.rsplit('.', 1)[0].split('.')[0] if '.' in stem else stem

    def _get_download_status(self, subtitle_sizes: dict, external_subs: list) -> Dict[str, Any]:
        """Get final download status"""
        status = {'video': None, 'audios': [], 'subtitles': [], 'external_subtitles': external_subs, 'external_audios': []}
        exts = {
            'video': ['.mp4', '.mkv', '.m4v', '.ts', '.mov', '.webm'], 
            'audio': ['.m4a', '.aac', '.mp3', '.ts', '.mp4', '.wav', '.webm'], 
            'subtitle': ['.srt', '.vtt', '.ass', '.sub', '.ssa', '.m4s', '.ttml', '.xml']
        }
        
        # Find video
        for ext in exts['video']:
            if (f := self.output_dir / f"{self.filename}{ext}").exists():
                status['video'] = {'path': str(f), 'size': f.stat().st_size}
                break
        
        # Process downloaded subtitle metadata
        downloaded_subs = [{
            'lang': (d_name.split(':', 1)[0] if ':' in d_name else d_name).strip(), 
            'name': (d_name.split(':', 1)[1] if ':' in d_name else d_name).strip(),
            'size': sz, 'used': False} 
            for d_name, size_str in subtitle_sizes.items() 
                if (sz := internet_manager.format_file_size(size_str))
        ]

        def norm_lang(lang):
            return set(lang.lower().replace('-', '.').split('.'))
        seen_langs = {}

        # Scan files
        for f in sorted(list(self.output_dir.iterdir())):
            if not f.is_file():
                continue
            
            # Audio
            if any(f.name.lower().endswith(e) for e in exts['audio']):
                if status['video'] and f.name == Path(status['video']['path']).name:
                    continue

                name = f.stem[len(self.filename):].lstrip('.') if f.stem.lower().startswith(self.filename.lower()) else f.stem
                status['audios'].append({'path': str(f), 'name': name, 'size': f.stat().st_size})
            
            # Subtitle
            elif any(f.name.lower().endswith(e) for e in exts['subtitle']):
                ext_lang = self._extract_language_from_filename(f.stem, self.filename)
                f_size = f.stat().st_size
                best_sub, min_diff = None, float('inf')
                
                # Find best match
                f_lang_tokens = norm_lang(ext_lang)
                for sub in downloaded_subs:
                    if sub.get('used'):
                        continue

                    s_lang_tokens = norm_lang(sub['lang'])
                    overlap = f_lang_tokens & s_lang_tokens
                    diff = abs(sub['size'] - f_size)
                    if (not f_lang_tokens or not s_lang_tokens or overlap) or not downloaded_subs:
                        if diff < min_diff and diff <= 2048:
                            min_diff, best_sub = diff, sub
                
                # Determine display name
                if best_sub:
                    lang, name = best_sub['lang'], best_sub['name']
                    best_sub['used'] = True
                    final_name = f"{lang} - {name}" if seen_langs.get(lang) and name and name != lang else lang
                    seen_langs[lang] = seen_langs.get(lang, 0) + 1
                else:
                    final_name = ext_lang

                status['subtitles'].append({'path': str(f), 'language': final_name, 'name': final_name, 'size': f_size})
        
        return status
    
    def get_status(self) -> Dict[str, Any]:
        """Get current download status"""
        return self.status if self.status else self._get_download_status({}, [])