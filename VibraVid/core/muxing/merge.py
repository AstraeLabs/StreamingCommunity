# 31.01.24

import os
import subprocess
import tempfile
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from rich.console import Console

from VibraVid.utils import config_manager
from VibraVid.setup import binary_paths, get_ffmpeg_path, get_mkvmerge_path
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.core.utils.language import resolve_iso639_2

from .helper.video import detect_ts_timestamp_issues, convert_ts_to_mp4, resolve_compatible_extension, is_mpegts_file
from .helper.audio import check_duration_v_a, has_audio, get_video_duration, detect_audio_offset
from .helper.sub import convert_subtitle, extract_vtt_from_wvtt_mp4
from .capture import capture_ffmpeg_real_time


console = Console()
logger = logging.getLogger(__name__)
USE_GPU = config_manager.config.get_bool("PROCESS", "use_gpu")
FORCE_SUBTITLE = config_manager.config.get("PROCESS", "force_subtitle")
SUBTITLE_DISPOSITION_LANGUAGE = config_manager.config.get("PROCESS", "subtitle_disposition_language")
_GPU_TYPE_CACHE = None
MUX_ENGINE = config_manager.config.get("PROCESS", "engine", default="ffmpeg").lower()


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
            raw = raw[:-len(suffix)]
            break

    return raw.replace("_", "-")


def _sort_tracks_by_order(tracks: List[Dict[str, Any]], order_config: List[str], lang_fields: List[str]) -> List[Dict[str, Any]]:
    if not tracks or not order_config:
        return tracks

    normalized_order: List[str] = []
    for item in order_config:
        token = _normalize_lang_token(str(item))
        if token:
            normalized_order.append(token)

    if not normalized_order:
        return tracks
    
    for track in tracks:
        logger.info(f"Track before sorting: {track}")

    priority_map = {lang: idx for idx, lang in enumerate(normalized_order)}

    def _rank(track: Dict[str, Any]) -> int:
        candidates: List[str] = []

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
        if os_type == 'linux':
            result = subprocess.run(['lspci'], capture_output=True, text=True, check=True)
            output = result.stdout.lower()

        elif os_type == 'windows':
            try:
                result = subprocess.run(['wmic', 'path', 'win32_videocontroller', 'get', 'name'], capture_output=True, text=True, check=True)
                output = result.stdout.lower()
            except (subprocess.CalledProcessError, FileNotFoundError):
                try:
                    result = subprocess.run(['powershell', '-Command', 'Get-WmiObject win32_videocontroller | Select-Object -ExpandProperty Name'], capture_output=True, text=True, check=True)
                    output = result.stdout.lower()
                except (subprocess.CalledProcessError, FileNotFoundError):
                    return 'none'

        elif os_type == 'darwin':
            result = subprocess.run(['system_profiler', 'SPDisplaysDataType'], capture_output=True, text=True, check=True)
            output = result.stdout.lower()

        else:
            _GPU_TYPE_CACHE = 'none'
            return _GPU_TYPE_CACHE

        if 'nvidia' in output:
            _GPU_TYPE_CACHE = 'cuda'
            return _GPU_TYPE_CACHE
        elif 'intel' in output:
            _GPU_TYPE_CACHE = 'qsv'
            return _GPU_TYPE_CACHE
        elif 'amd' in output or 'ati' in output:
            _GPU_TYPE_CACHE = 'vaapi'
            return _GPU_TYPE_CACHE
        else:
            _GPU_TYPE_CACHE = 'none'
            return _GPU_TYPE_CACHE

    except (subprocess.CalledProcessError, FileNotFoundError):
        _GPU_TYPE_CACHE = 'none'
        return _GPU_TYPE_CACHE


def add_encoding_params(ffmpeg_cmd: List[str]):
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
        ffmpeg_cmd.extend(['-c:v', 'libx265', '-crf', '22', '-preset', 'medium'])

    if param_audio:
        ffmpeg_cmd.extend(param_audio)
    else:
        logger.warning("No audio encoding parameters set in config. Using default: libopus with 128k bitrate.")
        ffmpeg_cmd.extend(['-c:a', 'libopus', '-b:a', '128k'])


def _apply_compatible_extension(video_path: str, out_path: str) -> str:
    """
    Checks codec compatibility between the source video and the desired output path extension.
    If not compatible, returns the most compatible extension instead.

    Parameters:
        - video_path (str): Source file used to probe codecs.
        - out_path (str): Desired output path.

    Returns:
        str: Output path with a guaranteed compatible extension.
    """
    base, ext = os.path.splitext(out_path)
    desired_ext = ext.lstrip('.')
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

    is_episode = media_type in ("EPISODE", "TV", "SERIES", "SHOW")
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


def _mkvmerge_lang_flag(track: Dict[str, Any], default: str = "und") -> str:
    raw = track.get("language") or track.get("name") or track.get("lang") or default
    return resolve_iso639_2(str(raw))


def _mkvmerge_track_name(track: Dict[str, Any]) -> str:
    return track.get("name") or track.get("language") or track.get("lang") or ""


def _strip_drm_boxes(src_path: str) -> str:
    base, ext = os.path.splitext(src_path)
    out_path = f"{base}_nodrm{ext}"

    if os.path.exists(out_path):
        logger.info(f"[strip_drm_boxes] cached strip already exists: {os.path.basename(out_path)}")
        return out_path

    cmd = [get_ffmpeg_path(), "-i", src_path, "-c", "copy", "-map", "0", out_path, "-y"]
    logger.info(f"[strip_drm_boxes] stripping DRM boxes: {' '.join(cmd)}")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            logger.warning(f"[strip_drm_boxes] ffmpeg strip failed (rc={result.returncode}), falling back to original. stderr: {result.stderr[-400:]}")
            return src_path
    except Exception as exc:
        logger.warning(f"[strip_drm_boxes] exception during strip, falling back: {exc}")
        return src_path

    logger.info(f"[strip_drm_boxes] strip OK: {os.path.basename(out_path)}")
    return out_path



def _write_ffmetadata_chapters(chapters: list) -> str:
    sorted_chs = sorted(chapters, key=lambda c: c["seconds"])
    lines = [";FFMETADATA1", ""]
    for i, ch in enumerate(sorted_chs):
        start_ms = ch["seconds"] * 1000
        end_ms = sorted_chs[i + 1]["seconds"] * 1000 - 1 if i + 1 < len(sorted_chs) else start_ms + 999999000
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start_ms}", f"END={end_ms}", f"title={ch['name']}", ""]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ffmeta", delete=False, encoding="utf-8") as f:
        f.write("\n".join(lines))
        return f.name


def _write_ogm_chapters(chapters: list) -> str:
    sorted_chs = sorted(chapters, key=lambda c: c["seconds"])
    lines = []
    for i, ch in enumerate(sorted_chs, 1):
        h, rem = divmod(ch["seconds"], 3600)
        m, s = divmod(rem, 60)
        lines += [f"CHAPTER{i:02d}={h:02d}:{m:02d}:{s:02d}.000", f"CHAPTER{i:02d}NAME={ch['name']}"]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".ogm", delete=False, encoding="utf-8") as f:
        f.write("\n".join(lines))
        return f.name


def join_video(video_path: str, out_path: str):
    """
    Mux video file using FFmpeg.

    Parameters:
        - video_path (str): The path to the video file.
        - out_path (str): The path to save the output file.

    Returns:
        tuple: (out_path, result_json)
    """
    if MUX_ENGINE == "mkvmerge":
        base, _ = os.path.splitext(out_path)
        out_path = base + ".mkv"
        return _join_video_mkvmerge(video_path, out_path)

    out_path = _apply_compatible_extension(video_path, out_path)
    ffmpeg_cmd = [get_ffmpeg_path()]

    if USE_GPU:
        gpu_type_hwaccel = detect_gpu_device_type()
        console.print(f'\n[yellow]FFMPEG [cyan]Detected GPU for video join: [red]{gpu_type_hwaccel}')
        ffmpeg_cmd.extend(['-hwaccel', gpu_type_hwaccel])
    
    # Detect timestamp issues and add regeneration flag
    has_ts_issues = detect_ts_timestamp_issues(video_path)
    if has_ts_issues:
        logger.info("[join_video] Detected timestamp issues, adding -fflags +genpts")
        ffmpeg_cmd.extend(['-fflags', '+genpts+igndts+discardcorrupt', '-avoid_negative_ts', 'make_zero'])

    if is_mpegts_file(video_path):
        ffmpeg_cmd.extend(['-f', 'mpegts'])

    ffmpeg_cmd.extend(['-i', video_path])
    chapter_file = None
    if context_tracker.chapters:
        chapter_file = _write_ffmetadata_chapters(context_tracker.chapters)
        ffmpeg_cmd.extend(['-f', 'ffmetadata', '-i', chapter_file])
    add_encoding_params(ffmpeg_cmd)
    
    if chapter_file:
        ffmpeg_cmd.extend(['-map_metadata', '1'])
    ffmpeg_cmd.extend(_build_global_metadata_flags())
    ffmpeg_cmd.extend([out_path, '-y'])

    total_duration = get_video_duration(video_path)
    logger.info(f"Running Join Video command: {' '.join(ffmpeg_cmd)}")
    result_json = capture_ffmpeg_real_time(ffmpeg_cmd, '[yellow]FFMPEG [cyan]Join video', total_duration)
    if context_tracker.should_print:
        print()

    if chapter_file:
        try:
            os.unlink(chapter_file)
        except OSError:
            pass

    return out_path, result_json

def _join_video_mkvmerge(video_path: str, out_path: str):
    video_path = _strip_drm_boxes(video_path)

    title = (context_tracker.title or "").strip()
    cmd = [
        get_mkvmerge_path(),
        "--output", out_path,
    ]
    if title:
        cmd += ["--title", title]

    chapter_file = None
    if context_tracker.chapters:
        chapter_file = _write_ogm_chapters(context_tracker.chapters)
        cmd += ["--chapters", chapter_file]

    cmd += [
        "--no-audio",
        "--no-subtitles",
        "--no-attachments",
        video_path,
    ]

    logger.info(f"Running Join Video (mkvmerge) command: {' '.join(cmd)}")
    total_duration = get_video_duration(video_path)
    result_json = capture_ffmpeg_real_time(cmd, '[yellow]MKVMERGE [cyan]Join video', total_duration)
    
    if chapter_file:
        try:
            os.unlink(chapter_file)
        except OSError:
            pass

    return out_path, result_json


def join_audios(video_path: str, audio_tracks: List[Dict[str, str]], out_path: str, limit_duration_diff: float = 3):
    """
    Joins audio tracks with a video file using FFmpeg.

    Parameters:
        - video_path (str): The path to the video file.
        - audio_tracks (list[dict[str, str]]): A list of dicts with 'path' and 'name' keys.
        - out_path (str): The path to save the output file.
        - limit_duration_diff (float): Maximum duration difference in seconds.

    Returns:
        tuple: (out_path, use_shortest, result_json)
    """
    use_shortest = False

    audio_order = _get_audio_order()
    if audio_order:
        audio_tracks = _sort_tracks_by_order(audio_tracks, audio_order, ["language", "name", "lang"])
        logger.info(f"Applying configured audio order: {audio_order}")

    # Check and convert audio tracks if TS with issues
    temp_audio_paths = []
    for audio_track in audio_tracks:
        audio_path = audio_track.get('path')
        if audio_path.lower().endswith('.ts') and detect_ts_timestamp_issues(audio_path):
            temp_audio_path = audio_path + '.temp.m4a'
            if convert_ts_to_mp4(audio_path, temp_audio_path):
                audio_track['path'] = temp_audio_path
                temp_audio_paths.append(temp_audio_path)
            else:
                console.print(f'[red]Failed to convert audio TS {audio_path} to M4A')

    # Validation audio track
    valid_audio_tracks = []
    for audio_track in audio_tracks:
        audio_path = audio_track.get('path')
        audio_lang = audio_track.get('name', 'unknown')
        audio_duration = get_video_duration(audio_path)
        if audio_duration is None:
            logger.warning(f"Audio duration is None for file: {audio_path}. Skipping track '{audio_lang}'.")
            console.print(f'[yellow]    WARN [cyan]Audio lang [red]{audio_lang} [cyan]has no readable duration — [red]skipped.')
            continue
        valid_audio_tracks.append(audio_track)
    audio_tracks = valid_audio_tracks

    if not audio_tracks:
        logger.warning("join_audios: no valid audio tracks remaining after duration check.")
        merged_only, mv_result = join_video(video_path, out_path)
        return merged_only, False, mv_result

    # Check duration differences
    reference_audio: Optional[str] = None

    for audio_track in audio_tracks:
        audio_path = audio_track.get('path')
        audio_lang = audio_track.get('name', 'unknown')

        _, diff, video_duration, audio_duration = check_duration_v_a(video_path, audio_path)
        diff_str = (f"+{(video_duration - audio_duration):.2f}s" if (video_duration - audio_duration) >= 0 else f"{(video_duration - audio_duration):.2f}s")
        console.print(f'[yellow]    - [cyan]Audio lang [red]{audio_lang}, [cyan]Video: [red]{video_duration:.2f}s, [cyan]Diff: [red]{diff_str}')

        if diff > limit_duration_diff:
            logger.warning(f"Duration difference for '{audio_lang}' exceeds limit ({diff:.2f}s > {limit_duration_diff}s). This track will be included with -shortest, but consider fixing the source files.")
            use_shortest = True

        if reference_audio is None:
            reference_audio = audio_path
            audio_track['_offset_sec'] = 0.0
            logger.info(f"Audio offset reference track: {audio_lang}")
        else:
            offset = detect_audio_offset(reference_audio, audio_path)
            if offset is None:
                logger.warning(f"Offset detection failed for '{audio_lang}'.")
                audio_track['_offset_sec'] = 0.0
            else:
                audio_track['_offset_sec'] = offset
                if abs(offset) >= 0.05:
                    direction = "Early" if offset > 0 else "Late"
                    console.print(f'[yellow]    OFFSET [cyan]{audio_lang}: [red]{offset:+.3f}s [cyan]({direction})')

    if MUX_ENGINE == "mkvmerge":
        result = _join_audios_mkvmerge(video_path, audio_tracks, out_path)
        return result[0], use_shortest, result[1]

    ffmpeg_cmd = [get_ffmpeg_path()]

    if USE_GPU:
        ffmpeg_cmd.extend(['-hwaccel', detect_gpu_device_type()])
    
    if is_mpegts_file(video_path):
        ffmpeg_cmd.extend(['-f', 'mpegts'])
    ffmpeg_cmd.extend(['-i', video_path])

    for audio_track in audio_tracks:
        audio_path  = audio_track.get('path')
        offset_sec  = audio_track.get('_offset_sec', 0.0)
        _OFFSET_THR = 0.05

        if is_mpegts_file(audio_track.get('path', '')):
            ffmpeg_cmd.extend(['-f', 'mpegts'])

        if offset_sec > _OFFSET_THR:
            ffmpeg_cmd.extend(['-itsoffset', f'{offset_sec:.3f}'])
            logger.info(f"Applying -itsoffset {offset_sec:.3f} to '{audio_track.get('name')}'")
        elif offset_sec < -_OFFSET_THR:
            ss_val = abs(offset_sec)
            ffmpeg_cmd.extend(['-ss', f'{ss_val:.3f}'])
            logger.info(f"Applying -ss {ss_val:.3f} to '{audio_track.get('name')}'")

        ffmpeg_cmd.extend(['-i', audio_path])

    ffmpeg_cmd.extend(['-map', '0:v'])
    for i in range(1, len(audio_tracks) + 1):
        ffmpeg_cmd.extend(['-map', f'{i}:a'])

    for i, audio_track in enumerate(audio_tracks):
        lang_source = audio_track.get('language') or audio_track.get('name', 'unknown')
        lang_code   = resolve_iso639_2(lang_source)
        track_title = audio_track.get('name') or lang_source

        # Set audio codec to copy (assuming compatibility, otherwise user should configure PARAM_AUDIO)
        ffmpeg_cmd.extend([f'-metadata:s:a:{i}', f'language={lang_code}'])
        ffmpeg_cmd.extend([f'-metadata:s:a:{i}', f'title={track_title}'])
        ffmpeg_cmd.extend([f'-metadata:s:a:{i}', f'handler_name={track_title}'])

        # First (highest-priority) audio track is default; others reset to 0
        ffmpeg_cmd.extend([f'-disposition:a:{i}', 'default' if i == 0 else '0'])

    add_encoding_params(ffmpeg_cmd)

    if use_shortest:
        ffmpeg_cmd.extend(['-shortest', '-strict', 'experimental'])
    
    ffmpeg_cmd.extend(_build_global_metadata_flags())
    ffmpeg_cmd.extend([out_path, '-y'])

    total_duration = get_video_duration(video_path)
    logger.info(f"Running Join Audio command: {' '.join(ffmpeg_cmd)}")
    result_json = capture_ffmpeg_real_time(ffmpeg_cmd, '[yellow]FFMPEG [cyan]Join audio', total_duration)
    if context_tracker.should_print:
        print()

    return out_path, use_shortest, result_json

def _join_audios_mkvmerge(video_path: str, audio_tracks: List[Dict[str, str]], out_path: str):
    base, _ = os.path.splitext(out_path)
    out_path = base + ".mkv"
    video_path = _strip_drm_boxes(video_path)

    title = (context_tracker.title or "").strip()
    cmd = [get_mkvmerge_path(), "--output", out_path]

    if title:
        cmd += ["--title", title]

    cmd += ["--no-audio", "--no-subtitles", video_path]

    for i, audio_track in enumerate(audio_tracks):
        lang_code  = _mkvmerge_lang_flag(audio_track)
        track_name = _mkvmerge_track_name(audio_track)
        clean_audio_path = _strip_drm_boxes(audio_track["path"])

        cmd += ["--language", f"0:{lang_code}"]
        if track_name:
            cmd += ["--track-name", f"0:{track_name}"]

        cmd += ["--default-track", f"0:{'yes' if i == 0 else 'no'}"]
        cmd += ["--audio-tracks", "0", "--no-video", "--no-subtitles", clean_audio_path]

    logger.info(f"Running Join Audio (mkvmerge) command: {' '.join(cmd)}")
    total_duration = get_video_duration(video_path)
    result_json = capture_ffmpeg_real_time(cmd, '[yellow]MKVMERGE [cyan]Join audio', total_duration)

    return out_path, result_json


def join_subtitles(video_path: str, subtitles_list: List[Dict[str, str]], out_path: str):
    """
    Joins subtitles with a video file using FFmpeg.
 
    Parameters:
        - video_path (str): The path to the video file.
        - subtitles_list (list[dict[str, str]]): A list of dicts with 'path',
          'language', and optionally 'is_wvtt_mp4' keys.
        - out_path (str): The path to save the output file.
 
    Returns:
        tuple: (out_path, result_json)
    """
    subtitle_order = _get_subtitle_order()
    if subtitle_order:
        subtitles_list = _sort_tracks_by_order(subtitles_list, subtitle_order, ["language", "lang", "name"])
        logger.info(f"Applying configured subtitle order: {subtitle_order}")

    # De-duplicate by resolved path (guards against any upstream double-add)
    seen_paths: set = set()
    deduped: List[Dict[str, str]] = []
    for sub in subtitles_list:
        canonical = os.path.normcase(os.path.abspath(sub.get("path", "")))
        if canonical in seen_paths:
            logger.warning(f"join_subtitles: duplicate subtitle path skipped → {os.path.basename(sub.get('path', ''))}")
            continue
        seen_paths.add(canonical)
        deduped.append(sub)
    subtitles_list = deduped

    # Pre-process: wvtt-mp4 → vtt extraction + normal conversion 
    processed: List[Dict[str, str]] = []
    for subtitle in subtitles_list:
        original_path = subtitle["path"]
        if subtitle.get("is_wvtt_mp4"):

            # Extract plain VTT from the fMP4 container (defined in helper/sub.py)
            vtt_path = str(Path(original_path).with_suffix(".vtt"))
            extracted = extract_vtt_from_wvtt_mp4(original_path, vtt_path)
            if extracted and os.path.exists(extracted) and os.path.getsize(extracted) > 0:
                logger.info(f"join_subtitles: wvtt extracted → {os.path.basename(extracted)}")
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
        logger.warning("join_subtitles: no valid subtitle tracks to mux after pre-processing")
        return join_video(video_path, out_path)

    subtitles_list = processed

    if MUX_ENGINE == "mkvmerge":
        return _join_subtitles_mkvmerge(video_path, subtitles_list, out_path)

    ffmpeg_cmd = [get_ffmpeg_path()]
    output_ext = os.path.splitext(out_path)[1].lower()
    if output_ext == ".mp4":
        subtitle_codec = "mov_text"
    elif output_ext == ".mkv":
        subtitle_codec = "srt"
    else:
        subtitle_codec = "copy"

    if is_mpegts_file(video_path):
        ffmpeg_cmd += ["-f", "mpegts"]
    
    ffmpeg_cmd += ["-i", video_path]
    for subtitle in subtitles_list:
        ffmpeg_cmd += ["-i", subtitle["path"]]

    ffmpeg_cmd += ["-map", "0:v"]
    if has_audio(video_path):
        ffmpeg_cmd += ["-map", "0:a"]

    for idx, subtitle in enumerate(subtitles_list):
        sub_path     = subtitle["path"]
        sub_ext      = os.path.splitext(sub_path)[1].lower().lstrip(".")
        lang_display = subtitle.get("lang", subtitle.get("language", "unknown"))

        # ISO 639-2: resolve_iso639_2 already strips _forced/_cc/_sdh via split("_")
        # e.g. "ita_forced" → "ita",  "en-US" → "eng",  "ita" → "ita"
        lang_iso = resolve_iso639_2(lang_display)

        console.print(f"[yellow]    - [cyan]Subtitle lang [red]{lang_display}.{sub_ext}")
        ffmpeg_cmd += ["-map", f"{idx + 1}:s"]

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

    ffmpeg_cmd.extend(["-c:v", "copy", "-c:a", "copy", "-c:s", subtitle_codec])

    # Disposizioni subtitle 
    # Passo 1: reset everything to 0

    for idx in range(len(subtitles_list)):
        ffmpeg_cmd.extend([f"-disposition:s:{idx}", "0"])

    # Passo 2: auto-flags (forced / hearing_impaired) da suffissi nel nome lingua
    # "_forced" → forced,  "_cc" o "_sdh" → hearing_impaired
    for idx, subtitle in enumerate(subtitles_list):
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
    if SUBTITLE_DISPOSITION_LANGUAGE and subtitles_list:
        config_lang = SUBTITLE_DISPOSITION_LANGUAGE.lower().strip()
        for idx, subtitle in enumerate(subtitles_list):
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
    ffmpeg_cmd += [out_path, "-y"]

    total_duration = get_video_duration(video_path)
    logger.info(f"Running Join Subtitle command: {' '.join(ffmpeg_cmd)}")
    result_json = capture_ffmpeg_real_time(ffmpeg_cmd, "[yellow]FFMPEG [cyan]Join subtitle", total_duration)
    if context_tracker.should_print:
        print()

    return out_path, result_json

def _join_subtitles_mkvmerge(video_path: str, subtitles_list: List[Dict[str, str]], out_path: str):
    base, _ = os.path.splitext(out_path)
    out_path = base + ".mkv"
    video_path = _strip_drm_boxes(video_path)

    title = (context_tracker.title or "").strip()
    cmd = [get_mkvmerge_path(), "--output", out_path]
    if title:
        cmd += ["--title", title]

    cmd += ["--no-subtitles", video_path]
    config_lang = (SUBTITLE_DISPOSITION_LANGUAGE or "").lower().strip()
    default_assigned = False

    for idx, subtitle in enumerate(subtitles_list):
        sub_path     = subtitle["path"]
        sub_ext      = os.path.splitext(sub_path)[1].lower().lstrip(".")
        lang_display = subtitle.get("lang", subtitle.get("language", "unknown"))
        lang_iso     = resolve_iso639_2(lang_display)
        lang_lower   = subtitle.get("language", "").lower()

        is_forced = "_forced" in lang_lower or bool(subtitle.get("forced"))
        is_hi     = "_sdh" in lang_lower or "_cc" in lang_lower or bool(subtitle.get("sdh")) or bool(subtitle.get("cc"))
        console.print(f"[yellow]    - [cyan]Subtitle lang [red]{lang_display}.{sub_ext}")

        cmd += ["--language",    f"0:{lang_iso}"]
        cmd += ["--track-name",  f"0:{lang_display}"]

        # forced flag
        cmd += ["--forced-track", f"0:{'yes' if is_forced else 'no'}"]

        # hearing-impaired flag
        cmd += ["--hearing-impaired-flag", f"0:{'yes' if is_hi else 'no'}"]

        # default track: segui SUBTITLE_DISPOSITION_LANGUAGE, altrimenti mai default
        if config_lang and not default_assigned and lang_lower == config_lang:
            cmd += ["--default-track", "0:yes"]
            console.print(f"[yellow]    Setting disposition: [red]{lang_display}")
            default_assigned = True
        else:
            cmd += ["--default-track", "0:no"]

        cmd += ["--subtitle-tracks", "0", "--no-video", "--no-audio", sub_path]

    logger.info(f"Running Join Subtitle (mkvmerge) command: {' '.join(cmd)}")
    total_duration = get_video_duration(video_path)
    result_json = capture_ffmpeg_real_time(cmd, "[yellow]MKVMERGE [cyan]Join subtitle", total_duration)
    return out_path, result_json
