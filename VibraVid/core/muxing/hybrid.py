# 16.04.24

import asyncio
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from VibraVid.core.muxing.util.info import Mediainfo
from VibraVid.core.muxing.helper.video import get_media_metadata
from VibraVid.setup import get_dovi_tool_path, get_ffmpeg_path, get_ffprobe_path, get_mkvmerge_path
from VibraVid.core.utils.decrypt_engine import _run_with_progress


logger = logging.getLogger(__name__)


def _run_command(cmd: List[str], description: str) -> bool:
    logger.info("%s: %s", description, " ".join(str(part) for part in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        if stderr:
            logger.error("%s failed: %s", description, stderr)
        elif stdout:
            logger.error("%s failed: %s", description, stdout)
        else:
            logger.error("%s failed with exit code %s", description, result.returncode)
        return False
    return True


def _run_progress_command(cmd: List[str], label: str, input_path: Path, output_path: Path) -> bool:
    result = _run_with_progress(cmd, label, str(input_path), str(output_path))
    if isinstance(result, tuple):
        return bool(result[0])
    return bool(result)


def _normalize_language(value: str) -> str:
    normalized = (value or "und").strip().replace("_", "-")
    return normalized or "und"


def _track_language(track: Dict[str, Any]) -> str:
    return _normalize_language(track.get("language") or track.get("lang") or "und")


def _track_name(track: Dict[str, Any], fallback: str) -> str:
    name = (track.get("name") or track.get("title") or fallback or "").strip()
    return name or fallback or "und"


def probe_media_file(file_path: str) -> Dict[str, Any]:
    """Probe a media file and return rich metadata for hybrid decisions."""
    probe: Dict[str, Any] = {}
    if not file_path:
        return probe

    file_obj = Path(file_path)
    if not file_obj.exists():
        return probe

    try:
        simple_probe = get_media_metadata(str(file_obj))
        if isinstance(simple_probe, dict):
            probe.update(simple_probe)
    except Exception as exc:
        logger.debug("Hybrid metadata probe failed for %s: %s", file_obj, exc)

    try:
        ffprobe_path = get_ffprobe_path()
        stream_info = asyncio.run(Mediainfo.from_file_async(ffprobe_path, str(file_obj)))
    except Exception as exc:
        logger.debug("Hybrid stream probe failed for %s: %s", file_obj, exc)
        return probe

    video_stream = next((item for item in stream_info if item.type.lower() == "video"), None)
    if video_stream:
        probe.update(
            {
                "resolution": video_stream.resolution,
                "bitrate": video_stream.bitrate,
                "fps": video_stream.fps,
                "base_info": video_stream.base_info,
                "hdr": video_stream.hdr,
                "dolby_vision": video_stream.dolby_vision,
            }
        )

    return probe


def _to_annexb(input_path: Path, output_path: Path) -> bool:
    cmd = [
        get_ffmpeg_path(),
        "-y",
        "-i",
        str(input_path),
        "-c:v",
        "copy",
        "-bsf:v",
        "hevc_mp4toannexb",
        str(output_path),
    ]
    return _run_command(cmd, f"ffmpeg annexb {input_path.name}")


def _select_hybrid_video(video_track: Dict[str, Any], other_videos: Iterable[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    base_path = str(video_track.get("path") or "").strip()
    if not base_path:
        return None

    base_probe = video_track.get("probe") or probe_media_file(base_path)
    if base_probe.get("dolby_vision") and not list(other_videos):
        return video_track

    if not base_probe.get("hdr"):
        return None

    dv_candidate = None
    for candidate in other_videos:
        if not candidate:
            continue
        probe = candidate.get("probe") or probe_media_file(str(candidate.get("path", "")))
        candidate["probe"] = probe
        if probe.get("dolby_vision") or (candidate.get("tag") or "").lower() == "dv":
            dv_candidate = candidate
            break

    return dv_candidate


def build_hybrid_output(
    video_track: Dict[str, Any],
    other_videos: Iterable[Dict[str, Any]],
    audio_tracks: List[Dict[str, Any]],
    subtitle_tracks: List[Dict[str, Any]],
    output_path: str,
    filename_base: str,
) -> Optional[str]:
    """Build a hybrid DV + HDR10 output when the media probes match the script workflow."""
    if not video_track:
        return None

    base_path_str = str(video_track.get("path") or "").strip()
    if not base_path_str:
        logger.warning("Hybrid mux skipped: base video path missing")
        return None

    base_path = Path(base_path_str)
    if not base_path.exists():
        logger.warning("Hybrid mux skipped: base video not found at %s", base_path)
        return None

    selected_other = _select_hybrid_video(video_track, other_videos)
    if not selected_other:
        return None

    dv_path_str = str(selected_other.get("path") or "").strip()
    if not dv_path_str:
        logger.warning("Hybrid mux skipped: DV video path missing")
        return None

    dv_path = Path(dv_path_str)
    if not dv_path.exists():
        logger.warning("Hybrid mux skipped: DV video not found at %s", dv_path)
        return None

    dovi_tool = get_dovi_tool_path()
    mkvmerge = get_mkvmerge_path()
    if not dovi_tool or not mkvmerge:
        logger.warning("Hybrid mux skipped: dovi_tool or mkvmerge not available")
        return None

    output_file = Path(output_path)
    if output_file.suffix.lower() != ".mkv":
        output_file = output_file.with_suffix(".mkv")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    work_dir = output_file.parent / f"{filename_base}_hybrid_tmp"
    work_dir.mkdir(parents=True, exist_ok=True)

    base_hevc = work_dir / f"{filename_base}_hdr.hevc"
    dv_hevc = work_dir / f"{filename_base}_dv.hevc"
    rpu_file = work_dir / f"{filename_base}_rpu.bin"
    hybrid_hevc = work_dir / f"{filename_base}_hybrid.hevc"

    if selected_other is video_track:
        if not _to_annexb(base_path, dv_hevc):
            return None
        base_hevc = dv_hevc
    else:
        if not _to_annexb(base_path, base_hevc):
            return None
        if not _to_annexb(dv_path, dv_hevc):
            return None

    if not _run_command([dovi_tool, "extract-rpu", str(dv_hevc), "-o", str(rpu_file)], "dovi_tool extract-rpu"):
        return None

    dovi_label = f"[cyan]Proc[/cyan] [green]{base_hevc.name}[/green] - [yellow]DoviTool[/yellow]"
    if not _run_progress_command(
        [
            dovi_tool, "inject-rpu", "-i", str(base_hevc),
            "--rpu-in", str(rpu_file),
            "-o", str(hybrid_hevc),
        ],
        dovi_label,
        base_hevc,
        hybrid_hevc,
    ):
        return None
    
    mux_cmd: List[str] = [
        mkvmerge, "-o", str(output_file),
        "--language", "0:und",
        "--track-name", "0:Hybrid DV+HDR10",
        "--compression", "0:none",
        str(hybrid_hevc),
    ]

    for track in audio_tracks:
        track_path_str = str(track.get("path") or "").strip()
        if not track_path_str:
            continue
        track_path = Path(track_path_str)
        if not track_path.exists():
            continue
        mux_cmd.extend(
            [
                "--language", f"0:{_track_language(track)}",
                "--track-name", f"0:{_track_name(track, _track_language(track))}",
                str(track_path),
            ]
        )

    for track in subtitle_tracks:
        track_path_str = str(track.get("path") or "").strip()
        if not track_path_str:
            continue

        track_path = Path(track_path_str)
        if not track_path.exists():
            continue

        mux_cmd.extend(
            [
                "--language", f"0:{_track_language(track)}",
                "--track-name", f"0:{_track_name(track, _track_language(track))}",
                str(track_path),
            ]
        )

    mkv_label = f"[cyan]Mux[/cyan] [green]{output_file.name}[/green] - [yellow]MKVMerge[/yellow]"
    if not _run_progress_command(mux_cmd, mkv_label, hybrid_hevc, output_file):
        return None

    if not output_file.exists() or output_file.stat().st_size <= 0:
        logger.error("Hybrid mux produced no output: %s", output_file)
        return None

    logger.info("Hybrid output created: %s", output_file)
    return str(output_file)