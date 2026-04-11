# 2.03.26

import os
import json
import shutil
import logging
from typing import Dict, List, Optional

from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.core.ui.tracker import download_tracker
from VibraVid.core.muxing import join_video, join_audios, join_subtitles
from VibraVid.core.muxing.helper.video import get_media_metadata
from VibraVid.cli.run import execute_hooks


console = Console()
logger = logging.getLogger(__name__)

EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
MERGE_SUBTITLES = config_manager.config.get_bool("PROCESS", "merge_subtitle")
MERGE_AUDIO = config_manager.config.get_bool("PROCESS", "merge_audio")
CLEANUP_TMP = config_manager.config.get_bool("DOWNLOAD", "cleanup_tmp_folder")
SKIP_DOWNLOAD = config_manager.config.get_bool("DOWNLOAD", "skip_download")
DEBUG_TRACK_JSON = config_manager.config.get_bool("DEFAULT", "debug_track_json")


class BaseDownloader:
    """
    Shared base for HLS_Downloader and DASH_Downloader.

    Subclasses are responsible for setting up:
        self.output_path = full final file path
        self.filename_base = stem without extension
        self.output_dir = temp working directory
        self.download_id = tracker ID (or None)
        self.last_merge_result = populated by merge methods
        self.copied_subtitles = list staging subtitles for deferred copy
        self.copied_audios = list staging audios for deferred copy
        self.audio_only = True when there is no video track
    """

    def _build_tracks_json(self, streams: list, keys: list, manifest_url: str) -> dict:
        """Build a JSON-serializable dict of selected tracks only. """
        videos = []
        audios: Dict[str, list] = {}
        subtitles: Dict[str, str] = {}

        for s in streams:
            if not getattr(s, "selected", False):
                continue
            stype = getattr(s, "type", "") or ""

            if stype == "video":
                videos.append({
                    "manifest_url": manifest_url,
                    "quality": getattr(s, "resolution", None),
                    "bitrate": getattr(s, "bitrate", None),
                })

            elif stype == "audio":
                lang = (getattr(s, "language", None) or "und").strip()
                entry = {
                    "manifest_url": manifest_url,
                    "codec": getattr(s, "codecs", None),
                    "channels": getattr(s, "channels", None),
                    "bitrate": getattr(s, "bitrate", None),
                    "url": getattr(s, "playlist_url", None) or getattr(s, "url", None),
                }
                audios.setdefault(lang, []).append(entry)

            elif stype == "subtitle":
                lang = getattr(s, "language", None) or "und"
                name = getattr(s, "name", None) or lang
                label = f"{lang} - {name}" if name and name != lang else lang
                url = getattr(s, "playlist_url", None) or getattr(s, "url", None)
                subtitles[label] = url

        return {"videos": videos, "audios": audios, "subtitles": subtitles, "keys": list(keys) if keys else []}

    def _log_tracks_json(self, streams: list, keys: list, manifest_url: str) -> None:
        """Emit a TRACKS_JSON logger.info"""
        from VibraVid.core.ui.tracker import context_tracker
        tracks = self._build_tracks_json(streams, keys, manifest_url)
        payload = {
            "name": context_tracker.title or self.filename_base,
            "type": (context_tracker.media_type or "UNKNOWN").upper(),
            "season": context_tracker.season  or 0,
            "episode": context_tracker.episode or 0,
            "episode_name": context_tracker.episode_name,
            "tracks": tracks,
        }
        if DEBUG_TRACK_JSON:
            logger.info("TRACKS_JSON\n" + json.dumps(payload, indent=4, ensure_ascii=False))

    def _no_media_downloaded(self, status: dict) -> bool:
        """Return True when the download produced absolutely nothing."""
        logger.info(f"Download status: {status}")
        return (
            status.get("video") is None
            and not status.get("audios")
            and not status.get("subtitles")
            and not status.get("external_subtitles")
        )
    
    def _move_to_final_location(self, final_file: str) -> None:
        """
        Move *final_file* to ``self.output_path``.
        Updates ``self.output_path`` when the merge produced a different extension.
        """
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
    
    def _merge_files(self, status: dict) -> Optional[str]:
        """
        Merge downloaded files using FFmpeg.
        Returns the resulting file path, or None on failure.
        """
        if status["video"] is None:
            if status["audios"] or status["subtitles"]:
                self.audio_only = True
                if status["audios"]:
                    self._track_audios_for_copy(status["audios"])
                if status["subtitles"]:
                    self._track_subtitles_for_copy(status["subtitles"])
                return self.output_path
            return None

        video_path = (
            status["video"]["path"]
            if isinstance(status["video"], dict)
            else status["video"].get("path")
        )

        if not os.path.exists(video_path):
            console.print(f"[red]Video file not found: {video_path}")

        audio_tracks: List[Dict] = list(status.get("audios") or [])

        # Append external audios (DASH-specific)
        for ext_audio in status.get("external_audios") or []:
            path = ext_audio.get("path", "")
            if path and os.path.exists(path):
                audio_tracks.append(
                    {
                        "path": path,
                        "name": ext_audio.get("language") or ext_audio.get("file", ""),
                        "size": os.path.getsize(path),
                    }
                )

        if not audio_tracks and not status["subtitles"]:
            console.print("[cyan]\nNo additional tracks to merge, muxing video...")
            merged_file, result_json = join_video(
                video_path=video_path,
                out_path=self.output_path,
            )
            self.last_merge_result = result_json
            return merged_file if os.path.exists(merged_file) else None

        current_file = video_path

        if audio_tracks:
            if MERGE_AUDIO:
                current_file = self._merge_audio_tracks(current_file, audio_tracks)
            else:
                self._track_audios_for_copy(audio_tracks)

        if status["subtitles"]:
            if MERGE_SUBTITLES:
                current_file = self._merge_subtitle_tracks(
                    current_file, status["subtitles"]
                )
            else:
                self._track_subtitles_for_copy(status["subtitles"])

        return current_file

    def _merge_audio_tracks(self, current_file: str, audio_tracks: list) -> str:
        """Merge audio tracks into the video file. Returns the resulting file path (or original on failure)."""
        console.print(f"[cyan]\nMerging [red]{len(audio_tracks)} [cyan]audio track(s)...")
        audio_output = os.path.join(
            self.output_dir, f"{self.filename_base}_with_audio.{EXTENSION_OUTPUT}"
        )
        merged_file, _, result_json = join_audios(
            video_path=current_file,
            audio_tracks=audio_tracks,
            out_path=audio_output,
        )
        self.last_merge_result = result_json
        if os.path.exists(merged_file):
            return merged_file
        
        console.print("[yellow]Audio merge failed, continuing with video only")
        return current_file

    def _merge_subtitle_tracks(self, current_file: str, subtitle_tracks: list) -> str:
        """Merge subtitle tracks into the video file. Returns the resulting file path (or original on failure)."""
        console.print(f"[cyan]\nMerging [red]{len(subtitle_tracks)} [cyan]subtitle track(s)...")
        sub_output = os.path.join(
            self.output_dir, f"{self.filename_base}_final.{EXTENSION_OUTPUT}"
        )
        merged_file, result_json = join_subtitles(
            video_path=current_file,
            subtitles_list=subtitle_tracks,
            out_path=sub_output,
        )
        self.last_merge_result = result_json
        if os.path.exists(merged_file):
            return merged_file
        
        console.print("[yellow]Subtitle merge failed, continuing without subtitles")
        return current_file

    def _track_subtitles_for_copy(self, subtitles_list: list) -> None:
        """Stage subtitle files for deferred copy to final location."""
        for idx, subtitle in enumerate(subtitles_list):
            sub_path = subtitle.get("path")
            if sub_path and os.path.exists(sub_path):
                self.copied_subtitles.append(
                    {
                        "src": sub_path,
                        "language": subtitle.get("language", f"sub{idx}"),
                        "extension": os.path.splitext(sub_path)[1],
                    }
                )

    def _track_audios_for_copy(self, audios_list: list) -> None:
        """Stage audio files for deferred copy to final location."""
        for idx, audio in enumerate(audios_list):
            audio_path = audio.get("path")
            if audio_path and os.path.exists(audio_path):
                self.copied_audios.append(
                    {
                        "src": audio_path,
                        "language": audio.get("language", audio.get("name", f"audio{idx}")),
                        "extension": os.path.splitext(audio_path)[1],
                    }
                )

    def _move_copied_subtitles(self) -> None:
        """Move staged subtitle files to final location."""
        if not self.copied_subtitles:
            return
        
        output_dir = os.path.dirname(self.output_path)
        filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        console.print("[cyan]Copying subtitles to final path...")
        for sub_info in self.copied_subtitles:
            dst = os.path.join(
                output_dir,
                f"{filename_base}.{sub_info['language']}{sub_info['extension']}",
            )

            try:
                shutil.copy2(sub_info["src"], dst)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not copy subtitle {sub_info['language']}: {e}")

    def _move_copied_audios(self) -> None:
        """Move staged audio files to final location."""
        if not self.copied_audios:
            return
        
        output_dir = os.path.dirname(self.output_path)
        filename_base = os.path.splitext(os.path.basename(self.output_path))[0]
        console.print("[cyan]Copying audios to final path...")
        for idx, audio_info in enumerate(self.copied_audios):
            if self.audio_only and idx == 0:
                dst = self.output_path
                move_func = shutil.move
            else:
                dst = os.path.join(
                    output_dir,
                    f"{filename_base}.{audio_info['language']}{audio_info['extension']}",
                )
                move_func = shutil.copy2

            try:
                move_func(audio_info["src"], dst)
            except Exception as e:
                console.print(f"[yellow]Warning: Could not move audio {audio_info['language']}: {e}")

    def _finalize(self, *, final_file: str) -> None:
        """Common tail for start(): move to final location."""
        if final_file and os.path.exists(final_file):
            self._move_to_final_location(final_file)

        dynamic_placeholders = ["%(quality)", "%(language)", "%(video_codec)", "%(audio_codec)"]
        if any(p in self.output_path for p in dynamic_placeholders):
            try:
                metadata = get_media_metadata(self.output_path)
                logger.info(f"Metadata for dynamic rename: {metadata}")
                
                new_path = self.output_path
                replacements = {
                    "quality": metadata.get("quality", ""),
                    "language": metadata.get("language", ""),
                    "video_codec": metadata.get("video_codec", ""),
                    "audio_codec": metadata.get("audio_codec", "")
                }
                
                for key, val in replacements.items():
                    placeholder = f"%({key})"
                    if val:
                        new_path = new_path.replace(placeholder, str(val))
                    else:
                        new_path = new_path.replace(f"[{placeholder}]", "").replace(f"({placeholder})", "").replace(placeholder, "")

                new_path = new_path.replace("  ", " ").replace(" .", ".").strip()
                
                if new_path != self.output_path:
                    new_dir = os.path.dirname(new_path)
                    old_path = self.output_path
                    
                    if not os.path.exists(new_dir):
                        os.makedirs(new_dir, exist_ok=True)
                        
                    os.rename(old_path, new_path)
                    
                    old_dir = os.path.dirname(old_path)
                    if old_dir != new_dir and os.path.exists(old_dir):
                        try:
                            if not os.listdir(old_dir) and any(p in old_dir for p in dynamic_placeholders):
                                os.rmdir(old_dir)
                        except Exception:
                            pass

                    self.output_path = new_path
                    logger.info(f"Dynamic rename applied: {self.output_path}")

            except Exception as e:
                console.print(f"[yellow]Warning: Dynamic rename failed: {e}")

        self._move_copied_subtitles()
        self._move_copied_audios()

        if self.download_id:
            download_tracker.complete_download(self.download_id, success=True, path=os.path.abspath(self.output_path))

        if CLEANUP_TMP:
            shutil.rmtree(self.output_dir, ignore_errors=True)

        execute_hooks("post_run")