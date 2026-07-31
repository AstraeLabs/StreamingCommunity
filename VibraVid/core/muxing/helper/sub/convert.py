# 16.04.24

import logging
import os
import shutil
import subprocess
from pathlib import Path

from rich.console import Console

from VibraVid.setup import get_ffmpeg_path

from .detect import detect_subtitle_format, fix_subtitle_extension
from .sanitize import sanitize_vtt_file
from .ttml import convert_ttml_to_format

console = Console()
logger = logging.getLogger(__name__)


def convert_subtitle(subtitle_path: str, target_format: str) -> str | None:
    """Converts a subtitle file to the target format using FFmpeg.

    Supported target formats:
      - 'vtt', 'srt', 'ass': convert to specified container format
      - 'auto': detect format and either rename or convert as needed
      - 'copy': leave the file untouched (no conversion or sanitization)
    """
    # no-op when user requests copy
    if target_format == "copy":
        return subtitle_path

    if target_format == "auto":
        detected_format = detect_subtitle_format(subtitle_path)

        # If it's TTML, we MUST convert it because most players/containers don't support raw TTML
        if detected_format == "ttml":
            output_path = f"{os.path.splitext(subtitle_path)[0]}.srt"
            if convert_ttml_to_format(subtitle_path, output_path, "srt"):
                return output_path
            return None

        # Otherwise, just ensure extension is correct
        return fix_subtitle_extension(subtitle_path)

    current_format = detect_subtitle_format(subtitle_path)
    if current_format == target_format:
        return subtitle_path

    output_path = f"{os.path.splitext(subtitle_path)[0]}.{target_format}"

    # Special high-fidelity converter for TTML -> (SRT, VTT)
    if current_format == "ttml":
        if target_format in ["srt", "vtt"]:
            if convert_ttml_to_format(subtitle_path, output_path, target_format):
                return output_path
        elif target_format == "ass":
            tmp_srt = f"{os.path.splitext(subtitle_path)[0]}_tmp.srt"
            if convert_ttml_to_format(subtitle_path, tmp_srt, "srt"):
                res = convert_subtitle(tmp_srt, "ass")
                try:
                    os.remove(tmp_srt)
                except Exception:
                    pass
                return res

        return None

    try:
        cmd = [get_ffmpeg_path(), "-v", "error", "-i", subtitle_path, output_path, "-y"]
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode == 0:
            console.print(f"[yellow]    Converted subtitle to [cyan]{target_format}: [green]{os.path.basename(output_path)}")
            return output_path
        else:
            console.print(f"[red]    Failed to convert subtitle to {target_format}: {result.stderr}")
            return None

    except Exception as e:
        console.print(f"[red]    Error converting subtitle: {str(e)}")
        return None


def extract_vtt_from_wvtt_mp4(wvtt_path: str, output_vtt_path: str | None = None) -> str | None:
    """Extract a plain WebVTT (.vtt) file from a fragmented MP4 container that carries a WVTT (WebVTT-in-MP4) subtitle track."""
    if not os.path.exists(wvtt_path):
        logger.error(f"extract_vtt_from_wvtt_mp4: input not found: {wvtt_path}")
        return None

    if output_vtt_path is None:
        output_vtt_path = str(Path(wvtt_path).with_suffix(".vtt"))

    try:
        mp4box = shutil.which("MP4Box") or shutil.which("mp4box")
        logger.info("Get mp4box path: " + (mp4box if mp4box else "not found"))

        if mp4box:
            cmd = [mp4box, "-raw", "1", wvtt_path, "-out", output_vtt_path]
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if result.returncode == 0 and os.path.exists(output_vtt_path) and os.path.getsize(output_vtt_path) > 0:
                logger.info(f"extract_vtt_from_wvtt_mp4 [MP4Box] OK -> {os.path.basename(output_vtt_path)}")
                sanitize_vtt_file(output_vtt_path)
                return output_vtt_path
            else:
                logger.warning(f"extract_vtt_from_wvtt_mp4 [MP4Box] failed (rc={result.returncode}): {result.stderr.strip()[:200]}")

    except Exception as exc:
        logger.warning(f"extract_vtt_from_wvtt_mp4 [MP4Box] exception: {exc}")

    return None
