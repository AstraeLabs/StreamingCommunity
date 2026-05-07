# 2.03.26

import os
import json
import re
import shutil
import logging
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.core.ui.tracker import download_tracker
from VibraVid.core.muxing import join_video, join_audios, join_subtitles, build_hybrid_output, probe_media_file
from VibraVid.core.muxing.helper.video import get_media_metadata
from VibraVid.core.muxing.helper.video_hybrid import download_other_tracks
from VibraVid.cli.run import execute_hooks


console = Console()
logger = logging.getLogger(__name__)

EXTENSION_OUTPUT = config_manager.config.get("PROCESS", "extension")
MERGE_SUBTITLES = config_manager.config.get_bool("PROCESS", "merge_subtitle")
MERGE_AUDIO = config_manager.config.get_bool("PROCESS", "merge_audio")
CLEANUP_TMP = config_manager.config.get_bool("DOWNLOAD", "cleanup_tmp_folder")
DEBUG_TRACK_JSON = config_manager.config.get_bool("DEFAULT", "debug_track_json")


class BaseDownloader:

    @staticmethod
    def _resolve_url(url_or_path: str) -> str:
        if not url_or_path:
            return url_or_path
        stripped = url_or_path.strip()

        if re.match(r'^[a-zA-Z][a-zA-Z0-9+\-.]*://', stripped):
            return stripped

        path = Path(stripped)
        if path.exists() and path.is_file():
            return path.resolve().as_uri()
        return stripped

    def _extract_season_episode(self, output_path: str) -> tuple:
        """Extract season and episode numbers from output_path using regex patterns."""
        season = 0
        episode = 0
        
        # Try to match S##E## or S#E# pattern
        match = re.search(r'[/\\]S(\d+)[/\\].*?S\d+E(\d+)', output_path)
        if match:
            season = int(match.group(1))
            episode = int(match.group(2))
            return season, episode
        
        # Fallback: try to match just S##E## in filename
        match = re.search(r'S(\d+)E(\d+)', output_path)
        if match:
            season = int(match.group(1))
            episode = int(match.group(2))
        
        return season, episode

    def _build_tracks_json(self, streams: list, keys: list, manifest_url: str) -> dict:
        """Build a JSON-serializable dict of selected tracks only."""
        videos = []
        audios: Dict[str, list] = {}
        subtitles: Dict[str, str] = {}
        other_tracks = []
        for track in list(getattr(self, "other_tracks", []) or []):
            if not isinstance(track, dict):
                continue
            normalized_track = {
                "type": track.get("type"),
                "url": track.get("url"),
                "language": track.get("language"),
            }
            if normalized_track["type"] and normalized_track["url"]:
                other_tracks.append(normalized_track)

        def _to_mbps(value):
            if value is None:
                return None
            try:
                bps = float(value)
            except (TypeError, ValueError):
                return None
            if bps <= 0:
                return None
            mbps = bps / 1_000_000
            return f"{mbps:.3f}".rstrip("0").rstrip(".") + " Mbps"

        for s in streams:
            if not getattr(s, "selected", False):
                continue
            stype = getattr(s, "type", "") or ""

            if stype == "video":
                codec = s.get_short_codec() if hasattr(s, 'get_short_codec') else getattr(s, "codecs", None)
                if not codec:
                    codec = "H.264"
                videos.append({
                    "manifest_url": manifest_url,
                    "quality": getattr(s, "resolution", None),
                    "bitrate": _to_mbps(getattr(s, "bitrate", None)),
                    "codec": codec,
                })

            elif stype == "audio":
                audio_url = getattr(s, "playlist_url", None) or getattr(s, "url", None) or manifest_url
                if audio_url == manifest_url:
                    continue  # same manifest as video, skip
                lang = (getattr(s, "language", None) or "und").strip()
                codec = s.get_short_codec() if hasattr(s, 'get_short_codec') else getattr(s, "codecs", None)
                if not codec:
                    codec = "AAC"
                audio_bitrate = _to_mbps(getattr(s, "bitrate", None))
                if audio_bitrate is None:
                    audio_bitrate = "128kbps"
                entry = {
                    "manifest_url": audio_url,
                    "codec": codec,
                    "bitrate": audio_bitrate,
                }
                audios.setdefault(lang, []).append(entry)

            elif stype == "subtitle":
                lang = getattr(s, "language", None) or "und"
                name = getattr(s, "name", None) or lang
                label = f"{lang} - {name}" if name and name != lang else lang
                url = getattr(s, "playlist_url", None) or getattr(s, "url", None)
                
                if not url and manifest_url:
                    url = manifest_url

                if url == manifest_url:
                    continue
                
                subtitles[label] = url

        formatted_keys = []
        for k in (keys or []):
            if isinstance(k, (list, tuple)) and len(k) == 2:
                formatted_keys.append(f"{k[0]}:{k[1]}")
            else:
                formatted_keys.append(str(k))

        result = {"videos": videos}
        if audios:
            result["audios"] = audios
        result["subtitles"] = subtitles
        result["other_tracks"] = other_tracks
        result["keys"] = formatted_keys
        return result

    def _log_tracks_json(self, streams: list, keys: list, manifest_url: str) -> None:
        """Emit a TRACKS_JSON logger.info"""
        from VibraVid.core.ui.tracker import context_tracker
        tracks = self._build_tracks_json(streams, keys, manifest_url)
        
        # Extract season/episode from output_path if available
        season, episode = 0, 0
        output_path = getattr(self, "output_path", "")
        if output_path:
            season, episode = self._extract_season_episode(output_path)
        
        # Fallback to context_tracker if extraction failed
        if season == 0:
            season = context_tracker.season or 0
        if episode == 0:
            episode = context_tracker.episode or 0
        
        # Determine media type: if season or episode > 0, it's a TV series, otherwise it's a film
        media_type = context_tracker.media_type or "Film"
        if season > 0 or episode > 0:
            media_type = "TV"
        
        payload = {
            "name": context_tracker.title or self.filename_base,
            "type": media_type.upper(),
            "season": season,
            "episode": episode,
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
        video_track = status.get("video")
        audio_tracks: List[Dict] = list(status.get("audios") or [])
        subtitle_tracks: List[Dict] = list(status.get("subtitles") or [])

        # DASH-specific external tracks
        for ext_audio in status.get("external_audios") or []:
            path = ext_audio.get("path", "")
            if path and os.path.exists(path):
                audio_tracks.append(
                    {
                        "path": path,
                        "name": ext_audio.get("language") or ext_audio.get("file", ""),
                        "language": ext_audio.get("language") or ext_audio.get("file", ""),
                        "size": os.path.getsize(path),
                    }
                )

        for ext_sub in status.get("external_subtitles") or []:
            path = ext_sub.get("path", "")
            if path and os.path.exists(path):
                subtitle_tracks.append(
                    {
                        "path": path,
                        "name": ext_sub.get("language") or ext_sub.get("file", ""),
                        "language": ext_sub.get("language") or ext_sub.get("file", ""),
                        "size": os.path.getsize(path),
                    }
                )

        other_track_specs = list(status.get("other_tracks") or [])
        other_track_results = list(status.get("other_tracks_downloaded") or [])

        if other_track_specs and not other_track_results:
            if self.download_id:
                download_tracker.update_status(self.download_id, "Downloading other tracks ...")

            other_track_results = download_other_tracks(
                other_track_specs,
                Path(self.output_dir),
                self.filename_base,
                keys=getattr(getattr(self, "media_downloader", None), "key", None),
                headers=getattr(self, "headers", None) or getattr(self, "mpd_headers", None) or {},
                cookies=getattr(self, "cookies", None) or {},
                max_segments=getattr(self, "max_segments", None),
            )
            status["other_tracks_downloaded"] = other_track_results

        if other_track_specs and self.download_id:
            download_tracker.update_status(self.download_id, "Muxing ...")

        for track in other_track_results:
            track_kind = (track.get("kind") or track.get("type") or "").lower()
            base_kind = track_kind.split(":")[0]   # "video:hdr10" → "video"
            if base_kind == "video":
                continue
            if base_kind == "audio":
                audio_tracks.append(track)
            elif base_kind == "subtitle":
                subtitle_tracks.append(track)

        if video_track is None:
            if audio_tracks or subtitle_tracks:
                self.audio_only = True
                if audio_tracks:
                    self._track_audios_for_copy(audio_tracks)
                if subtitle_tracks:
                    self._track_subtitles_for_copy(subtitle_tracks)
                return self.output_path
            return None

        video_path = (
            video_track["path"]
            if isinstance(video_track, dict)
            else video_track.get("path")
        )

        if not os.path.exists(video_path):
            console.print(f"[red]Video file not found: {video_path}")

        video_probe = video_track.get("probe") if isinstance(video_track, dict) else None
        if not video_probe and video_path and os.path.exists(video_path):
            video_probe = probe_media_file(video_path)

        if other_track_results or (video_probe or {}).get("dolby_vision"):
            hybrid_file = build_hybrid_output(
                video_track=video_track if isinstance(video_track, dict) else {"path": video_path},
                other_videos=[track for track in other_track_results if (track.get("kind") or track.get("type") or "").lower().split(":")[0] == "video"],
                audio_tracks=audio_tracks,
                subtitle_tracks=subtitle_tracks,
                output_path=self.output_path,
                filename_base=self.filename_base,
            )
            if hybrid_file:
                self.last_merge_result = {"hybrid": True, "output": hybrid_file}
                # hybrid_file include già audio e subtitle via mkvmerge:
                # non chiamare join_audios/join_subtitles separatamente
                return hybrid_file


        if not audio_tracks and not subtitle_tracks:
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

        subtitle_tracks = self._prepare_subtitle_tracks_for_merge(subtitle_tracks)
        if subtitle_tracks:
            if MERGE_SUBTITLES:
                current_file = self._merge_subtitle_tracks(
                    current_file, subtitle_tracks
                )
            else:
                self._track_subtitles_for_copy(subtitle_tracks)

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

    def _prepare_subtitle_tracks_for_merge(self, subtitle_tracks: list) -> list:
        """Hook for subclasses to materialize subtitle assets right before subtitle merge."""
        return subtitle_tracks

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
