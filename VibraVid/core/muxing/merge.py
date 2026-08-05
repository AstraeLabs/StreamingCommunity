# 31.01.24

import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from mutagen.mp4 import MP4, MP4Cover
from rich.console import Console

from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.utils.language import resolve_ietf, resolve_iso639_2
from VibraVid.setup import binary_paths, get_ffmpeg_path, get_mkvmerge_path
from VibraVid.utils import config_manager, internet_manager
from VibraVid.utils.image_cache import get_cached_tmdb_image

from .capture import capture_ffmpeg_real_time
from .helper.audio import check_duration_v_a, detect_audio_offset, get_video_duration, has_audio
from .helper.sub import convert_subtitle, extract_vtt_from_wvtt_mp4, get_subtitle_duration, trim_subtitle_to_duration
from .helper.video import (
    convert_ts_to_mp4,
    detect_ts_timestamp_issues,
    get_stream_codecs,
    is_mpegts_file,
    resolve_compatible_extension,
)

console = Console()
logger = logging.getLogger(__name__)
USE_GPU = config_manager.config.get_bool("PROCESS", "use_gpu")
FORCE_SUBTITLE = config_manager.config.get("PROCESS", "force_subtitle")
SUBTITLE_DISPOSITION_LANGUAGE = config_manager.config.get("PROCESS", "subtitle_disposition_language")
MUX_ENGINE = config_manager.config.get("PROCESS", "engine", default="ffmpeg").lower()
_GPU_TYPE_CACHE = None
_GENERIC_CHAPTER_NAME_RE = re.compile(r"^chapter\s*\d+$", re.IGNORECASE)


def _get_param_video() -> list:
    return config_manager.config.get_list("PROCESS", "param_video")


def _get_param_audio() -> list:
    return config_manager.config.get_list("PROCESS", "param_audio")


def _get_param_final() -> list:
    return config_manager.config.get_list("PROCESS", "param_final")


def _get_audio_order() -> list:
    return config_manager.config.get_list("PROCESS", "audio_order", default=[])


def _get_subtitle_order() -> list:
    return config_manager.config.get_list("PROCESS", "subtitle_order", default=[])


def _normalize_lang_token(value: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return ""

    # Strip common subtitle flags to match entries such as "ita_forced" or "eng_cc".
    for suffix in ("_forced", "_cc", "_sdh"):
        if raw.endswith(suffix):
            raw = raw[: -len(suffix)]
            break

    return raw.replace("_", "-")


def _sort_tracks_by_order(
    tracks: list[dict[str, Any]], order_config: list[str], lang_fields: list[str]
) -> list[dict[str, Any]]:
    if not tracks or not order_config:
        return tracks

    normalized_order: list[str] = []
    for item in order_config:
        token = _normalize_lang_token(str(item))
        if token:
            normalized_order.append(token)

    if not normalized_order:
        return tracks

    for track in tracks:
        logger.info(f"Track before sorting: {track}")

    priority_map = {lang: idx for idx, lang in enumerate(normalized_order)}

    def _rank(track: dict[str, Any]) -> int:
        candidates: list[str] = []

        for field in lang_fields:
            field_val = track.get(field)
            if field_val:
                candidates.append(str(field_val))

        for raw in candidates:
            normalized = _normalize_lang_token(raw)
            if normalized in priority_map:
                return priority_map[normalized]

            base = normalized.split("-", 1)[0]
            if base in priority_map:
                return priority_map[base]

            iso_code = resolve_iso639_2(normalized)
            if iso_code in priority_map:
                return priority_map[iso_code]

            if len(iso_code) == 3 and iso_code != "und":
                iso_base = iso_code[:2]
                if iso_base in priority_map:
                    return priority_map[iso_base]

        return len(priority_map) + 1

    indexed = list(enumerate(tracks))
    indexed.sort(key=lambda pair: (_rank(pair[1]), pair[0]))
    return [track for _, track in indexed]


def detect_gpu_device_type() -> str:
    """
    Detects the GPU device type available on the system.

    Returns:
        str: The type of GPU device detected ('cuda', 'vaapi', 'qsv', or 'none').
    """
    global _GPU_TYPE_CACHE
    if _GPU_TYPE_CACHE is not None:
        return _GPU_TYPE_CACHE

    os_type = binary_paths._detect_system()

    try:
        if os_type == "linux":
            result = subprocess.run(["lspci"], capture_output=True, text=True, check=True)
            output = result.stdout.lower()

        elif os_type == "windows":
            try:
                result = subprocess.run(
                    ["wmic", "path", "win32_videocontroller", "get", "name"], capture_output=True, text=True, check=True
                )
                output = result.stdout.lower()
            except (subprocess.CalledProcessError, FileNotFoundError):
                try:
                    result = subprocess.run(
                        [
                            "powershell",
                            "-Command",
                            "Get-WmiObject win32_videocontroller | Select-Object -ExpandProperty Name",
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    output = result.stdout.lower()
                except (subprocess.CalledProcessError, FileNotFoundError):
                    return "none"

        elif os_type == "darwin":
            result = subprocess.run(
                ["system_profiler", "SPDisplaysDataType"], capture_output=True, text=True, check=True
            )
            output = result.stdout.lower()

        else:
            _GPU_TYPE_CACHE = "none"
            return _GPU_TYPE_CACHE

        if "nvidia" in output:
            _GPU_TYPE_CACHE = "cuda"
            return _GPU_TYPE_CACHE
        elif "intel" in output:
            _GPU_TYPE_CACHE = "qsv"
            return _GPU_TYPE_CACHE
        elif "amd" in output or "ati" in output:
            _GPU_TYPE_CACHE = "vaapi"
            return _GPU_TYPE_CACHE
        else:
            _GPU_TYPE_CACHE = "none"
            return _GPU_TYPE_CACHE

    except (subprocess.CalledProcessError, FileNotFoundError):
        _GPU_TYPE_CACHE = "none"
        return _GPU_TYPE_CACHE


def add_encoding_params(ffmpeg_cmd: list[str]):
    """
    Add encoding parameters to the FFmpeg command.

    Logic:
        - If PARAM_FINAL is set (non-empty), it takes full precedence (e.g. ["-c", "copy"]).
        - Otherwise, PARAM_VIDEO and PARAM_AUDIO from config are used directly.
          The user is responsible for setting the correct encoder in param_video
        - If PARAM_VIDEO or PARAM_AUDIO are empty, safe defaults are applied.

    Parameters:
        ffmpeg_cmd (List[str]): FFmpeg command list to extend in-place.
    """
    param_final = _get_param_final()
    param_video = _get_param_video()
    param_audio = _get_param_audio()

    if param_final:
        ffmpeg_cmd.extend(param_final)
        return

    if param_video:
        ffmpeg_cmd.extend(param_video)
    else:
        logger.warning("No video encoding parameters set in config. Using default: libx265 with CRF 22.")
        ffmpeg_cmd.extend(["-c:v", "libx265", "-crf", "22", "-preset", "medium"])

    if param_audio:
        ffmpeg_cmd.extend(param_audio)
    else:
        logger.warning("No audio encoding parameters set in config. Using default: libopus with 128k bitrate.")
        ffmpeg_cmd.extend(["-c:a", "libopus", "-b:a", "128k"])


def _is_video_copied() -> bool:
    """
    Return True if the effective config stream-copies the video track (rather than re-encoding it)
    """
    param_final = _get_param_final()
    if param_final:
        return "copy" in param_final

    param_video = _get_param_video()
    if param_video:
        return "copy" in param_video

    return False  # default re-encodes to libx265


def _maybe_tag_hevc_for_mkv(ffmpeg_cmd: list[str], video_path: str, out_path: str, video_copied: bool) -> None:
    """Matroska rejects the MP4 'dvh1'/'dvhe' fourCC tags (Dolby Vision HEVC) when the video is stream-copied, failing the whole mux with:"""
    if not video_copied:
        return

    if os.path.splitext(out_path)[1].lower() != ".mkv":
        return

    try:
        codecs = get_stream_codecs(video_path)
    except Exception:
        return

    if any(s.get("codec_type") == "video" and s.get("codec_name") == "hevc" for s in codecs):
        ffmpeg_cmd.extend(["-tag:v", "hvc1"])
        logger.info("Normalizing HEVC video tag to 'hvc1' for Matroska output (Dolby Vision dvh1 compatibility).")


def _apply_compatible_extension(video_path: str, out_path: str) -> str:
    """
    Checks codec compatibility between the source video and the desired output path extension.
    If not compatible, returns the most compatible extension instead.

    Parameters:
        video_path (str): Source file used to probe codecs.
        out_path (str): Desired output path.

    Returns:
        str: Output path with a guaranteed compatible extension.
    """
    base, ext = os.path.splitext(out_path)
    desired_ext = ext.lstrip(".")
    compatible_ext = resolve_compatible_extension(video_path, desired_ext)

    if compatible_ext != desired_ext:
        out_path = f"{base}.{compatible_ext}"

    return out_path


def _build_global_metadata_flags() -> list:
    """
    Build FFmpeg ``-metadata key=value`` flags for the output container, sourcing data from ``context_tracker``.
    """
    flags: list = []
    title = (context_tracker.title or "").strip()
    media_type = (context_tracker.media_type or "").upper().strip()
    season = context_tracker.season or 0
    episode = context_tracker.episode or 0
    episode_name = (context_tracker.episode_name or "").strip()
    site_name = (context_tracker.site_name or "").strip()

    is_episode = (
        media_type in ("EPISODE", "TV", "SERIES", "SHOW", "SERIE", "OVA", "ONA", "TV SHORT", "SPECIAL")
        or season > 0
        or episode > 0
    )
    if is_episode:
        ep_title = episode_name or title
        if ep_title:
            flags += ["-metadata", f"title={ep_title}"]
        if title:
            flags += ["-metadata", f"show={title}"]
        if season:
            flags += ["-metadata", f"season_number={season}"]
        if episode:
            flags += ["-metadata", f"episode_sort={episode}"]
        if episode_name:
            flags += ["-metadata", f"episode_id={episode_name}"]
    else:
        if title:
            flags += ["-metadata", f"title={title}"]

    if site_name:
        flags += ["-metadata", f"comment={site_name}"]

    flags += ["-metadata", "encoder=VibraVid"]
    return flags


def _mkvmerge_lang_flag(track: dict[str, Any], default: str = "und") -> str:
    """mkverge store the language in the IETF format (e.g. es-419) rather than ISO 639-2 (e.g. spa)."""
    raw = track.get("language") or track.get("name") or track.get("lang") or default
    return resolve_ietf(str(raw))


def _mkvmerge_track_name(track: dict[str, Any]) -> str:
    return track.get("name") or track.get("language") or track.get("lang") or ""


def _mkvmerge_add_track(
    kind: str,
    path: str,
    language: str,
    track_name: str = "",
    *,
    default: bool = False,
    forced: bool | None = None,
    hearing_impaired: bool | None = None,
) -> list[str]:
    """Build the mkvmerge args that append ONE track (audio/subtitle) from its own input file."""
    args = ["--language", f"0:{language}"]
    if track_name:
        args += ["--track-name", f"0:{track_name}"]
    if forced is not None:
        args += ["--forced-track", f"0:{'yes' if forced else 'no'}"]
    if hearing_impaired is not None:
        args += ["--hearing-impaired-flag", f"0:{'yes' if hearing_impaired else 'no'}"]
    args += ["--default-track", f"0:{'yes' if default else 'no'}"]
    if kind == "audio":
        args += ["--audio-tracks", "0", "--no-video", "--no-subtitles", path]
    else:
        args += ["--subtitle-tracks", "0", "--no-video", "--no-audio", path]
    return args


def _strip_drm_boxes(src_path: str) -> str:
    """Strips DRM boxes (enca/encv/sinf/pssh) from an ISOBMFF/MP4 file using ffmpeg -c copy."""
    base, ext = os.path.splitext(src_path)
    if ext.lower() in (".mkv", ".mka", ".webm"):
        return src_path

    out_path = f"{base}_nodrm{ext}"

    if os.path.exists(out_path):
        logger.info(f"[strip_drm_boxes] cached strip already exists: {os.path.basename(out_path)}")
        return out_path

    cmd = [get_ffmpeg_path(), "-i", src_path, "-c", "copy", "-map", "0", out_path, "-y"]
    logger.info(f"[strip_drm_boxes] stripping DRM boxes: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            logger.warning(f"[strip_drm_boxes] ffmpeg strip failed (rc={result.returncode}), falling back to original. stderr: {result.stderr[-400:]}")
            return src_path
    except Exception as exc:
        logger.warning(f"[strip_drm_boxes] exception during strip, falling back: {exc}")
        return src_path

    logger.info(f"[strip_drm_boxes] strip OK: {os.path.basename(out_path)}")
    return out_path


def _sort_chapters(chapters: list) -> list:
    """Return chapters ordered by their start time."""
    return sorted(chapters, key=lambda c: c["seconds"])


def _dedupe_chapters(sorted_chapters: list) -> list:
    """Drop chapters sharing a start time with the previous one"""
    deduped = []
    for ch in sorted_chapters:
        if deduped and ch["seconds"] == deduped[-1]["seconds"]:
            if _GENERIC_CHAPTER_NAME_RE.match(deduped[-1].get("name", "")) and not _GENERIC_CHAPTER_NAME_RE.match(
                ch.get("name", "")
            ):
                deduped[-1] = ch
            continue
        deduped.append(ch)
    return deduped


def _write_ffmetadata_chapters(chapters: list) -> str:
    sorted_chs = _dedupe_chapters(_sort_chapters(chapters))
    lines = [";FFMETADATA1", ""]
    for i, ch in enumerate(sorted_chs):
        start_ms = ch["seconds"] * 1000
        end_ms = sorted_chs[i + 1]["seconds"] * 1000 - 1 if i + 1 < len(sorted_chs) else start_ms + 999999000
        end_ms = max(end_ms, start_ms + 1)  # guarantee END > START even if callers skip _dedupe_chapters
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start_ms}", f"END={end_ms}", f"title={ch['name']}", ""]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ffmeta", delete=False, encoding="utf-8") as f:
        f.write("\n".join(lines))
        return f.name


def _write_ogm_chapters(chapters: list) -> str:
    sorted_chs = _dedupe_chapters(_sort_chapters(chapters))
    lines = []
    for i, ch in enumerate(sorted_chs, 1):
        h, rem = divmod(int(ch["seconds"]), 3600)
        m, s = divmod(rem, 60)
        lines += [f"CHAPTER{i:02d}={h:02d}:{m:02d}:{s:02d}.000", f"CHAPTER{i:02d}NAME={ch['name']}"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ogm", delete=False, encoding="utf-8") as f:
        f.write("\n".join(lines))
        return f.name


def _format_timestamp(seconds) -> str:
    """Format a seconds value as HH:MM:SS for display."""
    return internet_manager.format_time(seconds, add_hours=True)


def inject_chapters(file_path: str, chapters: list | None = None):
    """
    Injects chapters into an already-muxed file.

    Parameters:
        file_path (str): The path to the already-muxed media file.
        chapters (list): Queued chapters, e.g. from a downloader's ``.chapters`` attribute. Each entry is ``{"name": str, "seconds": int}``.

    Returns:
        tuple: (file_path, result_json)
    """
    if not chapters:
        return file_path, {}

    chapters = _sort_chapters(chapters)
    if chapters[0]["seconds"] > 0:
        chapters = [{"name": "Intro", "seconds": 0}] + chapters

    logger.info(f"Injecting {len(chapters)} chapter(s) as final mux step")
    console.print(f"[cyan]\nInject [red]{len(chapters)} [cyan]chapter(s)...")
    for chapter in chapters:
        console.print(f"[yellow]    - [red]{_format_timestamp(chapter['seconds'])}[cyan]: [red]{chapter.get('name', '')}")

    base, ext = os.path.splitext(file_path)
    tmp_out = f"{base}_chapters{ext}"

    if (MUX_ENGINE == "mkvmerge" or ext.lower() == ".mkv") and get_mkvmerge_path():
        chapter_file = _write_ogm_chapters(chapters)
        cmd = [get_mkvmerge_path(), "-o", tmp_out, "--chapters", chapter_file, file_path]
        logger.info(f"Running chapter injection (mkvmerge) command: {' '.join(cmd)}")
        total_duration = get_video_duration(file_path)
        result_json = capture_ffmpeg_real_time(cmd, "[yellow]MKVMERGE [cyan]Add chapters", total_duration)
    else:
        chapter_file = _write_ffmetadata_chapters(chapters)
        ffmpeg_cmd = [get_ffmpeg_path()]
        if is_mpegts_file(file_path):
            ffmpeg_cmd += ["-f", "mpegts"]

        ffmpeg_cmd += [
            "-i",
            file_path,
            "-f",
            "ffmetadata",
            "-i",
            chapter_file,
            "-map",
            "0",
            "-map_metadata",
            "1",
            "-c",
            "copy",
            tmp_out,
            "-y",
        ]

        logger.info(f"Running chapter injection (ffmpeg) command: {' '.join(ffmpeg_cmd)}")
        total_duration = get_video_duration(file_path)
        result_json = capture_ffmpeg_real_time(ffmpeg_cmd, "[yellow]FFMPEG [cyan]Add chapters", total_duration)
        if context_tracker.should_print:
            print()

    try:
        os.unlink(chapter_file)
    except OSError:
        pass

    if not (os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0):
        logger.warning("[inject_chapters] chapter injection failed, keeping file without chapters")
        return file_path, result_json

    try:
        os.replace(tmp_out, file_path)
    except OSError as e:
        logger.warning(f"[inject_chapters] could not replace original file: {e}")
        return tmp_out, result_json

    return file_path, result_json


def embed_poster(file_path: str, image_url: str | None = None):
    """
    Embeds a poster/still image into an already-muxed file, as the final muxing step.

    Parameters:
        file_path (str): The path to the already-muxed media file.
        image_url (str): URL of the poster/still image to embed, or None to no-op.

    Returns:
        tuple: (file_path, result_json)
    """
    if not image_url:
        return file_path, {}

    image_bytes = get_cached_tmdb_image(image_url)
    if not image_bytes:
        logger.warning("[embed_poster] could not download image, skipping")
        return file_path, {}

    base, ext = os.path.splitext(file_path)
    ext_lower = ext.lower()

    if ext_lower in (".mp4", ".m4v"):
        try:
            audio = MP4(file_path)
            if audio.tags is None:
                audio.add_tags()
            audio.tags["covr"] = [MP4Cover(image_bytes, imageformat=MP4Cover.FORMAT_JPEG)]
            audio.save()
            logger.info(f"Poster embedded (MP4 covr): {image_url}")
        except Exception as e:
            logger.warning(f"[embed_poster] MP4 tagging failed: {e}")
        return file_path, {}

    if ext_lower != ".mkv":
        logger.debug(f"[embed_poster] poster embedding not supported for extension '{ext}', skipping")
        return file_path, {}

    # Fixed filename "cover.jpg" (not a random tempfile name)
    tmp_cover_dir = tempfile.mkdtemp()
    tmp_cover_path = os.path.join(tmp_cover_dir, "cover.jpg")
    with open(tmp_cover_path, "wb") as f:
        f.write(image_bytes)

    tmp_out = f"{base}_poster{ext}"

    try:
        if MUX_ENGINE == "mkvmerge":
            cmd = [
                get_mkvmerge_path(),
                "-o",
                tmp_out,
                "--attachment-mime-type",
                "image/jpeg",
                "--attachment-name",
                "cover.jpg",
                "--attach-file",
                tmp_cover_path,
                file_path,
            ]
            logger.info(f"Running poster embedding (mkvmerge) command: {' '.join(cmd)}")
            total_duration = get_video_duration(file_path)
            result_json = capture_ffmpeg_real_time(cmd, "[yellow]MKVMERGE [cyan]Embed poster", total_duration)
        else:
            cmd = [
                get_ffmpeg_path(),
                "-i",
                file_path,
                "-attach",
                tmp_cover_path,
                "-metadata:s:t",
                "mimetype=image/jpeg",
                "-metadata:s:t",
                "filename=cover.jpg",
                "-map",
                "0",
                "-c",
                "copy",
                tmp_out,
                "-y",
            ]
            logger.info(f"Running poster embedding (ffmpeg) command: {' '.join(cmd)}")
            total_duration = get_video_duration(file_path)
            result_json = capture_ffmpeg_real_time(cmd, "[yellow]FFMPEG [cyan]Embed poster", total_duration)
            if context_tracker.should_print:
                print()
    finally:
        shutil.rmtree(tmp_cover_dir, ignore_errors=True)

    if not (os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 0):
        logger.warning("[embed_poster] poster embedding failed, keeping file without poster")
        return file_path, result_json

    try:
        os.replace(tmp_out, file_path)
    except OSError as e:
        logger.warning(f"[embed_poster] could not replace original file: {e}")
        return tmp_out, result_json

    return file_path, result_json


def _prepare_audio_tracks(
    video_path: str, audio_tracks: list[dict[str, str]], limit_duration_diff: float = 3
) -> tuple[list[dict[str, str]], bool]:
    """
    Validate, order, TS-convert and offset-detect audio tracks before muxing.

    Returns:
        tuple: (valid_audio_tracks, use_shortest)
    """
    use_shortest = False

    audio_order = _get_audio_order()
    if audio_order:
        audio_tracks = _sort_tracks_by_order(audio_tracks, audio_order, ["language", "name", "lang"])
        logger.info(f"Applying configured audio order: {audio_order}")

    # Check and convert audio tracks if TS with issues
    temp_audio_paths = []
    for audio_track in audio_tracks:
        audio_path = audio_track.get("path")
        if audio_path.lower().endswith(".ts") and detect_ts_timestamp_issues(audio_path):
            temp_audio_path = audio_path + ".temp.m4a"
            if convert_ts_to_mp4(audio_path, temp_audio_path):
                audio_track["path"] = temp_audio_path
                temp_audio_paths.append(temp_audio_path)
            else:
                console.print(f"[red]Failed to convert audio TS {audio_path} to M4A")

    # Validation audio track
    valid_audio_tracks = []
    for audio_track in audio_tracks:
        audio_path = audio_track.get("path")
        audio_lang = audio_track.get("name", "unknown")
        audio_duration = get_video_duration(audio_path)
        if audio_duration is None:
            logger.warning(f"Audio duration is None for file: {audio_path}. Skipping track '{audio_lang}'.")
            console.print(f"[yellow]    WARN [cyan]Audio lang [red]{audio_lang} [cyan]has no readable duration — [red]skipped.")
            continue
        valid_audio_tracks.append(audio_track)
    audio_tracks = valid_audio_tracks

    if not audio_tracks:
        logger.warning("_prepare_audio_tracks: no valid audio tracks remaining after duration check.")
        return [], False

    # Check duration differences
    reference_audio: str | None = None
    reference_duration: float | None = None

    for audio_track in audio_tracks:
        audio_path = audio_track.get("path")
        audio_lang = audio_track.get("name", "unknown")

        _, diff, video_duration, audio_duration = check_duration_v_a(video_path, audio_path)
        diff_sec = round(video_duration - audio_duration)
        diff_str = f"+{diff_sec}s" if diff_sec >= 0 else f"{diff_sec}s"
        console.print(f"[yellow]    - [cyan]Audio lang [red]{audio_lang}, [cyan]Video: [red]{round(video_duration)}s, [cyan]Diff: [red]{diff_str}")

        if diff > limit_duration_diff:
            logger.warning(f"Duration difference for '{audio_lang}' exceeds limit ({diff:.2f}s > {limit_duration_diff}s). This track will be included with -shortest, but consider fixing the source files.")
            shortest_dur = round(min(video_duration, audio_duration))
            console.print(f"[yellow]      -> [cyan]shortest will be applied: output trimmed to ~{shortest_dur}s")
            use_shortest = True

        if reference_audio is None:
            reference_audio = audio_path
            reference_duration = audio_duration
            audio_track["_offset_sec"] = 0.0
            logger.info(f"Audio offset reference track: {audio_lang}")
        else:
            dur_gap = abs(audio_duration - reference_duration) if reference_duration is not None else 0.0
            if dur_gap <= limit_duration_diff:
                logger.info(f"Skipping offset detection for '{audio_lang}': duration matches reference (gap {dur_gap:.2f}s <= {limit_duration_diff}s).")
                audio_track["_offset_sec"] = 0.0
                continue

            offset = detect_audio_offset(reference_audio, audio_path)
            if offset is None:
                logger.warning(f"Offset detection failed for '{audio_lang}'.")
                audio_track["_offset_sec"] = 0.0
            else:
                audio_track["_offset_sec"] = offset
                if abs(offset) >= 0.05:
                    direction = "Early" if offset > 0 else "Late"
                    console.print(f"[yellow]    OFFSET [cyan]{audio_lang}: [red]{offset:+.3f}s [cyan]({direction})")

    return audio_tracks, use_shortest


def _prepare_subtitle_tracks(
    video_path: str, subtitles_list: list[dict[str, str]], limit_duration_diff: float = 3
) -> list[dict[str, str]]:
    """
    Order, dedup, convert (wvtt-mp4/format) and trim subtitle tracks before muxing.

    Returns:
        list: the processed subtitle tracks (empty list if none survive preprocessing).
    """
    video_duration = get_video_duration(video_path)
    subtitle_order = _get_subtitle_order()
    if subtitle_order:
        subtitles_list = _sort_tracks_by_order(subtitles_list, subtitle_order, ["language", "lang", "name"])
        logger.info(f"Applying configured subtitle order: {subtitle_order}")

    # De-duplicate by resolved path (guards against any upstream double-add)
    seen_paths: set = set()
    deduped: list[dict[str, str]] = []
    for sub in subtitles_list:
        canonical = os.path.normcase(os.path.abspath(sub.get("path", "")))
        if canonical in seen_paths:
            logger.warning(f"join_subtitles: duplicate subtitle path skipped -> {os.path.basename(sub.get('path', ''))}")
            continue
        seen_paths.add(canonical)
        deduped.append(sub)
    subtitles_list = deduped

    # Pre-process: wvtt-mp4 -> vtt extraction + normal conversion
    processed: list[dict[str, str]] = []
    for subtitle in subtitles_list:
        original_path = subtitle["path"]
        if subtitle.get("is_wvtt_mp4"):
            # Extract plain VTT from the fMP4 container (defined in helper/sub.py)
            vtt_path = str(Path(original_path).with_suffix(".vtt"))
            extracted = extract_vtt_from_wvtt_mp4(original_path, vtt_path)
            if extracted and os.path.exists(extracted) and os.path.getsize(extracted) > 0:
                logger.info(f"join_subtitles: wvtt extracted -> {os.path.basename(extracted)}")
                sub = dict(subtitle)
                sub["path"] = extracted
                sub["is_wvtt_mp4"] = False
                processed.append(sub)
            else:
                logger.error(f"join_subtitles: wvtt extraction failed for {os.path.basename(original_path)}, skipping")
            continue

        # Normal subtitle: fix extension / convert format
        corrected_path = convert_subtitle(original_path, FORCE_SUBTITLE)
        if not corrected_path:
            corrected_path = original_path
        sub = dict(subtitle)
        sub["path"] = corrected_path
        processed.append(sub)

    if not processed:
        logger.warning("_prepare_subtitle_tracks: no valid subtitle tracks to mux after pre-processing")
        return []

    # Trim subtitles that overrun the video
    if video_duration:
        for subtitle in processed:
            sub_path = subtitle["path"]
            sub_duration = get_subtitle_duration(sub_path)
            if not sub_duration:
                continue

            diff = sub_duration - video_duration
            if diff > limit_duration_diff:
                logger.warning(f"Subtitle '{subtitle.get('language', 'unknown')}' duration ({sub_duration:.2f}s) exceeds video ({video_duration:.2f}s) by {diff:.2f}s > {limit_duration_diff}s limit — trimming to match.")
                console.print(f"[yellow]    - [cyan]Subtitle [red]{subtitle.get('language', 'unknown')} [cyan]trimming to [red]~{round(video_duration)}s [cyan]to keep container duration accurate")
                subtitle["path"] = trim_subtitle_to_duration(sub_path, video_duration)

    return processed


def join_media(
    video_path: str,
    audio_tracks: list[dict[str, str]],
    subtitle_tracks: list[dict[str, str]],
    out_path: str,
    limit_duration_diff: float = 3,
    chapters: list | None = None,
):
    """
    Mux video + audio tracks + subtitle tracks in a single ffmpeg or mkvmerge

    Parameters:
        video_path (str): The path to the (video-only) source file.
        audio_tracks (list[dict[str, str]]): A list of dicts with 'path' and 'name' keys. May be empty.
        subtitle_tracks (list[dict[str, str]]): A list of dicts with 'path', 'language', and optionally 'is_wvtt_mp4' keys. May be empty.
        out_path (str): The path to save the output file.
        limit_duration_diff (float): Maximum duration difference in seconds (audio vs video, and subtitle vs video) before -shortest / trimming kicks in.
        chapters (list, optional): Chapters to bake into this same merge command (avoids a second full-file
            remux pass via inject_chapters()). Each entry is ``{"name": str, "seconds": int}``.

    Returns:
        tuple: (out_path, result_json)
    """
    use_shortest = False
    if audio_tracks:
        console.print(f"[cyan]\nMerging [red]{len(audio_tracks)} [cyan]audio track(s)...")
        audio_tracks, use_shortest = _prepare_audio_tracks(video_path, audio_tracks, limit_duration_diff)
    if subtitle_tracks:
        console.print(f"[cyan]\nMerging [red]{len(subtitle_tracks)} [cyan]subtitle track(s)...")
        subtitle_tracks = _prepare_subtitle_tracks(video_path, subtitle_tracks, limit_duration_diff)

    if chapters:
        chapters = _dedupe_chapters(_sort_chapters(chapters))
        if chapters[0]["seconds"] > 0:
            chapters = [{"name": "Intro", "seconds": 0}] + chapters

    if MUX_ENGINE == "mkvmerge":
        base, _ = os.path.splitext(out_path)
        out_path = base + ".mkv"
        return _join_media_mkvmerge(video_path, audio_tracks, subtitle_tracks, out_path, chapters)

    out_path = _apply_compatible_extension(video_path, out_path)
    return _join_media_ffmpeg(video_path, audio_tracks, subtitle_tracks, out_path, use_shortest, chapters)


def _join_media_ffmpeg(
    video_path: str,
    audio_tracks: list[dict[str, str]],
    subtitle_tracks: list[dict[str, str]],
    out_path: str,
    use_shortest: bool,
    chapters: list | None = None,
):
    ffmpeg_cmd = [get_ffmpeg_path()]

    if USE_GPU:
        gpu_type_hwaccel = detect_gpu_device_type()
        console.print(f"\n[yellow]FFMPEG [cyan]Detected GPU for join: [red]{gpu_type_hwaccel}")
        ffmpeg_cmd.extend(["-hwaccel", gpu_type_hwaccel])

    has_ts_issues = detect_ts_timestamp_issues(video_path)
    if has_ts_issues:
        logger.info("[join_media] Detected timestamp issues, adding -fflags +genpts")
        ffmpeg_cmd.extend(["-fflags", "+genpts+igndts+discardcorrupt", "-avoid_negative_ts", "make_zero"])

    if is_mpegts_file(video_path):
        ffmpeg_cmd.extend(["-f", "mpegts"])
    ffmpeg_cmd.extend(["-i", video_path])

    _OFFSET_THR = 0.05
    for audio_track in audio_tracks:
        audio_path = audio_track.get("path")
        offset_sec = audio_track.get("_offset_sec", 0.0)

        if is_mpegts_file(audio_path):
            ffmpeg_cmd.extend(["-f", "mpegts"])

        if offset_sec > _OFFSET_THR:
            ffmpeg_cmd.extend(["-itsoffset", f"{offset_sec:.3f}"])
            logger.info(f"Applying -itsoffset {offset_sec:.3f} to '{audio_track.get('name')}'")
        elif offset_sec < -_OFFSET_THR:
            ss_val = abs(offset_sec)
            ffmpeg_cmd.extend(["-ss", f"{ss_val:.3f}"])
            logger.info(f"Applying -ss {ss_val:.3f} to '{audio_track.get('name')}'")

        ffmpeg_cmd.extend(["-i", audio_path])

    for subtitle in subtitle_tracks:
        ffmpeg_cmd += ["-i", subtitle["path"]]

    chapter_file = None
    chapter_input_idx = None
    if chapters:
        chapter_file = _write_ffmetadata_chapters(chapters)
        chapter_input_idx = 1 + len(audio_tracks) + len(subtitle_tracks)
        ffmpeg_cmd += ["-f", "ffmetadata", "-i", chapter_file]

    ffmpeg_cmd.extend(["-map", "0:v"])
    if not audio_tracks and has_audio(video_path):
        ffmpeg_cmd.extend(["-map", "0:a"])
    for i in range(1, len(audio_tracks) + 1):
        ffmpeg_cmd.extend(["-map", f"{i}:a"])
    sub_input_base = len(audio_tracks) + 1
    for j in range(len(subtitle_tracks)):
        ffmpeg_cmd.extend(["-map", f"{sub_input_base + j}:s"])

    for i, audio_track in enumerate(audio_tracks):
        lang_source = audio_track.get("language") or audio_track.get("name", "unknown")
        lang_code = resolve_iso639_2(lang_source)
        track_title = audio_track.get("name") or lang_source

        ffmpeg_cmd.extend([f"-metadata:s:a:{i}", f"language={lang_code}"])
        ffmpeg_cmd.extend([f"-metadata:s:a:{i}", f"title={track_title}"])
        ffmpeg_cmd.extend([f"-metadata:s:a:{i}", f"handler_name={track_title}"])

        # First (highest-priority) audio track is default; others reset to 0
        ffmpeg_cmd.extend([f"-disposition:a:{i}", "default" if i == 0 else "0"])

    output_ext = os.path.splitext(out_path)[1].lower()
    if output_ext == ".mp4":
        subtitle_codec = "mov_text"
    elif output_ext == ".mkv":
        subtitle_codec = "srt"
    else:
        subtitle_codec = "copy"

    for idx, subtitle in enumerate(subtitle_tracks):
        sub_path = subtitle["path"]
        sub_ext = os.path.splitext(sub_path)[1].lower().lstrip(".")
        lang_display = subtitle.get("lang", subtitle.get("language", "unknown"))

        # ISO 639-2: resolve_iso639_2 already strips _forced/_cc/_sdh via split("_")
        # e.g. "ita_forced" -> "ita",  "en-US" -> "eng",  "ita" -> "ita"
        lang_iso = resolve_iso639_2(lang_display)

        console.print(f"[yellow]    - [cyan]Subtitle lang [red]{lang_display}.{sub_ext}")

        if output_ext == ".mp4":
            ffmpeg_cmd += [f"-c:s:{idx}", "mov_text"]
        elif output_ext == ".mkv":
            if sub_ext in ("srt", "vtt"):
                ffmpeg_cmd += [f"-c:s:{idx}", "srt"]
            elif sub_ext in ("ass", "ssa"):
                ffmpeg_cmd += [f"-c:s:{idx}", "ass"]
            else:
                ffmpeg_cmd += [f"-c:s:{idx}", "copy"]
        else:
            ffmpeg_cmd += [f"-c:s:{idx}", "copy"]

        ffmpeg_cmd += [f"-metadata:s:s:{idx}", f"title={lang_display}"]
        ffmpeg_cmd += [f"-metadata:s:s:{idx}", f"language={lang_iso}"]
        ffmpeg_cmd += [f"-metadata:s:s:{idx}", f"handler_name={lang_display}"]

    if chapters:
        console.print(f"[cyan]\nAdding [red]{len(chapters)} [cyan]chapter(s) inline...")
        for chapter in chapters:
            console.print(f"[yellow]    - [red]{_format_timestamp(chapter['seconds'])}[cyan]: [red]{chapter.get('name', '')}")

    add_encoding_params(ffmpeg_cmd)
    if subtitle_tracks:
        ffmpeg_cmd.extend(["-c:s", subtitle_codec])
    _maybe_tag_hevc_for_mkv(ffmpeg_cmd, video_path, out_path, _is_video_copied())

    if use_shortest:
        ffmpeg_cmd.extend(["-shortest", "-strict", "experimental"])

    # Disposizioni subtitle
    # Passo 1: reset everything to 0
    for idx in range(len(subtitle_tracks)):
        ffmpeg_cmd.extend([f"-disposition:s:{idx}", "0"])

    # Passo 2: auto-flags (forced / hearing_impaired) da suffissi nel nome lingua
    # "_forced" -> forced,  "_cc" o "_sdh" -> hearing_impaired
    for idx, subtitle in enumerate(subtitle_tracks):
        lang_lower = subtitle.get("language", "").lower()
        is_forced = "_forced" in lang_lower or bool(subtitle.get("forced"))
        is_hi = "_sdh" in lang_lower or "_cc" in lang_lower or bool(subtitle.get("sdh")) or bool(subtitle.get("cc"))
        if is_forced or is_hi:
            disp_parts = []
            if is_forced:
                disp_parts.append("forced")
            if is_hi:
                disp_parts.append("hearing_impaired")
            ffmpeg_cmd.extend([f"-disposition:s:{idx}", "+".join(disp_parts)])

    # Passo 3: config-driven default (SUBTITLE_DISPOSITION_LANGUAGE, es. "ita_forced")
    # Questo sovrascrive l'eventuale flag precedente per la traccia corrispondente.
    if SUBTITLE_DISPOSITION_LANGUAGE and subtitle_tracks:
        config_lang = SUBTITLE_DISPOSITION_LANGUAGE.lower().strip()
        for idx, subtitle in enumerate(subtitle_tracks):
            subtitle_lang = subtitle.get("language", "").lower()
            if subtitle_lang == config_lang:
                console.print(f"[yellow]    Setting disposition: [red]{subtitle.get('language')}")
                disp = "default"
                if "_forced" in config_lang:
                    disp += "+forced"
                if "_sdh" in config_lang or "_cc" in config_lang:
                    disp += "+hearing_impaired"
                ffmpeg_cmd.extend([f"-disposition:s:{idx}", disp])
                break

    ffmpeg_cmd.extend(_build_global_metadata_flags())
    if chapter_input_idx is not None:
        ffmpeg_cmd.extend(["-map_metadata", str(chapter_input_idx)])
    ffmpeg_cmd.extend([out_path, "-y"])

    total_duration = get_video_duration(video_path)
    logger.info(f"Running Join Media command: {' '.join(ffmpeg_cmd)}")
    result_json = capture_ffmpeg_real_time(ffmpeg_cmd, "[yellow]FFMPEG [cyan]Join media", total_duration)
    if context_tracker.should_print:
        print()

    if chapter_file:
        try:
            os.unlink(chapter_file)
        except OSError:
            pass

    return out_path, result_json


def _join_media_mkvmerge(
    video_path: str,
    audio_tracks: list[dict[str, str]],
    subtitle_tracks: list[dict[str, str]],
    out_path: str,
    chapters: list | None = None,
):
    video_path = _strip_drm_boxes(video_path)

    title = (context_tracker.title or "").strip()
    cmd = [get_mkvmerge_path(), "--output", out_path]
    if title:
        cmd += ["--title", title]

    chapter_file = _write_ogm_chapters(chapters) if chapters else None
    if chapter_file:
        cmd += ["--chapters", chapter_file]

    cmd += ["--no-audio", "--no-subtitles", "--no-attachments", video_path]

    for i, audio_track in enumerate(audio_tracks):
        lang_code = _mkvmerge_lang_flag(audio_track)
        track_name = _mkvmerge_track_name(audio_track)
        clean_audio_path = _strip_drm_boxes(audio_track["path"])
        cmd += _mkvmerge_add_track("audio", clean_audio_path, lang_code, track_name, default=(i == 0))

    config_lang = (SUBTITLE_DISPOSITION_LANGUAGE or "").lower().strip()
    default_assigned = False
    for subtitle in subtitle_tracks:
        sub_path = subtitle["path"]
        sub_ext = os.path.splitext(sub_path)[1].lower().lstrip(".")
        lang_display = subtitle.get("lang", subtitle.get("language", "unknown"))
        lang_iso = resolve_ietf(lang_display)  # mkvmerge language-ietf: keep region, strip flags
        lang_lower = subtitle.get("language", "").lower()

        is_forced = "_forced" in lang_lower or bool(subtitle.get("forced"))
        is_hi = "_sdh" in lang_lower or "_cc" in lang_lower or bool(subtitle.get("sdh")) or bool(subtitle.get("cc"))
        console.print(f"[yellow]    - [cyan]Subtitle lang [red]{lang_display}.{sub_ext}")

        # default track: segui SUBTITLE_DISPOSITION_LANGUAGE, altrimenti mai default
        is_default = bool(config_lang and not default_assigned and lang_lower == config_lang)
        if is_default:
            console.print(f"[yellow]    Setting disposition: [red]{lang_display}")
            default_assigned = True

        cmd += _mkvmerge_add_track(
            "subtitle", sub_path, lang_iso, lang_display, default=is_default, forced=is_forced, hearing_impaired=is_hi
        )

    if chapters:
        console.print(f"[cyan]\nAdding [red]{len(chapters)} [cyan]chapter(s) inline...")
        for chapter in chapters:
            console.print(f"[yellow]    - [red]{_format_timestamp(chapter['seconds'])}[cyan]: [red]{chapter.get('name', '')}")

    logger.info(f"Running Join Media (mkvmerge) command: {' '.join(cmd)}")
    total_duration = get_video_duration(video_path)
    result_json = capture_ffmpeg_real_time(cmd, "[yellow]MKVMERGE [cyan]Join media", total_duration)

    if chapter_file:
        try:
            os.unlink(chapter_file)
        except OSError:
            pass

    return out_path, result_json
