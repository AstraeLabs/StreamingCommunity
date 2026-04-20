# 18.07.25

import os
import logging
import shutil
from typing import Optional, Tuple

from rich.console import Console

from .binary_paths import binary_paths
from VibraVid.utils import config_manager

console = Console()
logger = logging.getLogger(__name__)
INSTALLATION_LEVELS = {
    "none": [],
    "essential": ["bento4", "ffmpeg", "velora"],
    "full": ["bento4", "ffmpeg", "velora", "dovi_tool", "mkvtoolnix"],
}


def _should_download(tool_group: str) -> bool:
    """Return True if the given tool group should be downloaded based on the installation level."""
    level = config_manager.config.get("DEFAULT", "installation")
    return tool_group in INSTALLATION_LEVELS.get(level, [])


def check_bento4() -> Optional[str]:
    """
    Check for a Bento4 binary and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    system_platform = binary_paths.system
    binary_exec = "mp4decrypt.exe" if system_platform == "windows" else "mp4decrypt"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug("Found %s in system PATH (%s)", binary_exec, binary_path)
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("bento4", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug("Found %s in local binary directory (%s)", binary_exec, binary_local)
        return binary_local

    # STEP 3: Download (only if installation level includes bento4)
    if not _should_download("bento4"):
        logger.info(f"Skipping download of {binary_exec}")
        return None

    binary_downloaded = binary_paths.download_binary("bento4", binary_exec)
    if binary_downloaded:
        logger.debug("Downloaded %s to %s", binary_exec, binary_downloaded)
        return binary_downloaded

    logger.error("Failed to download %s", binary_exec)
    console.print(f"Failed to download {binary_exec}", style="red")
    return None


def check_mp4dump() -> Optional[str]:
    """
    Check for Bento4 mp4dump binary and download if not found.
    """
    system_platform = binary_paths.system
    binary_exec = "mp4dump.exe" if system_platform == "windows" else "mp4dump"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug("Found %s in system PATH (%s)", binary_exec, binary_path)
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("bento4", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug("Found %s in local binary directory (%s)", binary_exec, binary_local)
        return binary_local

    # STEP 3: Download (only if installation level includes bento4)
    if not _should_download("bento4"):
        logger.info(f"Skipping download of {binary_exec}")
        return None

    binary_downloaded = binary_paths.download_binary("bento4", binary_exec)
    if binary_downloaded:
        logger.debug("Downloaded %s to %s", binary_exec, binary_downloaded)
        return binary_downloaded

    logger.error("Failed to download %s", binary_exec)
    console.print(f"Failed to download {binary_exec}", style="red")
    return None


def check_ffmpeg() -> Tuple[Optional[str], Optional[str]]:
    """
    Check for FFmpeg executables and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    system_platform = binary_paths.system
    ffmpeg_name = "ffmpeg.exe" if system_platform == "windows" else "ffmpeg"
    ffprobe_name = "ffprobe.exe" if system_platform == "windows" else "ffprobe"

    # STEP 1: Check system PATH
    ffmpeg_path = shutil.which(ffmpeg_name)
    ffprobe_path = shutil.which(ffprobe_name)
    if ffmpeg_path and ffprobe_path:
        logger.debug("Found ffmpeg (%s) and ffprobe (%s) in system PATH", ffmpeg_path, ffprobe_path)
        return ffmpeg_path, ffprobe_path

    # STEP 2: Check binary directory
    ffmpeg_local = binary_paths.get_binary_path("ffmpeg", ffmpeg_name)
    ffprobe_local = binary_paths.get_binary_path("ffmpeg", ffprobe_name)
    if ffmpeg_local and os.path.isfile(ffmpeg_local) and ffprobe_local and os.path.isfile(ffprobe_local):
        logger.debug("Found ffmpeg (%s) and ffprobe (%s) in local binary directory", ffmpeg_local, ffprobe_local)
        return ffmpeg_local, ffprobe_local

    # STEP 3: Download (only if installation level includes ffmpeg)
    if not _should_download("ffmpeg"):
        logger.info("Skipping download of ffmpeg/ffprobe")
        return None, None

    ffmpeg_downloaded = binary_paths.download_binary("ffmpeg", ffmpeg_name)
    ffprobe_downloaded = binary_paths.download_binary("ffmpeg", ffprobe_name)
    if ffmpeg_downloaded and ffprobe_downloaded:
        logger.debug("Downloaded ffmpeg (%s) and ffprobe (%s)", ffmpeg_downloaded, ffprobe_downloaded)
        return ffmpeg_downloaded, ffprobe_downloaded

    logger.error("Failed to download FFmpeg")
    console.print("Failed to download FFmpeg", style="red")
    return None, None


def check_shaka_packager() -> Optional[str]:
    """
    Check for Shaka Packager executable and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    system_platform = binary_paths.system
    packager_name = "packager.exe" if system_platform == "windows" else "packager"

    # STEP 1: Check system PATH
    packager_path = shutil.which(packager_name)
    if packager_path:
        logger.debug("Found Shaka Packager in system PATH (%s)", packager_path)
        return packager_path

    # STEP 2: Check binary directory
    packager_local = binary_paths.get_binary_path("shaka_packager", packager_name)
    if packager_local and os.path.isfile(packager_local):
        logger.debug("Found Shaka Packager in local binary directory (%s)", packager_local)
        return packager_local

    # STEP 3: Download (only if installation level includes shaka_packager)
    if not _should_download("shaka_packager"):
        logger.info(f"Skipping download of {packager_name}")
        return None

    packager_downloaded = binary_paths.download_binary("shaka_packager", packager_name)
    if packager_downloaded:
        logger.debug("Downloaded Shaka Packager to %s", packager_downloaded)
        return packager_downloaded

    logger.error("Failed to download Shaka Packager")
    console.print("Failed to download Shaka Packager", style="red")
    return None


def check_dovi_tool() -> Optional[str]:
    """
    Check for dovi_tool binary and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    system_platform = binary_paths.system
    binary_exec = "dovi_tool.exe" if system_platform == "windows" else "dovi_tool"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug("Found %s in system PATH (%s)", binary_exec, binary_path)
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("dovi_tool", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug("Found %s in local binary directory (%s)", binary_exec, binary_local)
        return binary_local

    # STEP 3: Download (only if installation level includes dovi_tool)
    if not _should_download("dovi_tool"):
        logger.info(f"Skipping download of {binary_exec}")
        return None

    binary_downloaded = binary_paths.download_binary("dovi_tool", binary_exec)
    if binary_downloaded:
        logger.debug("Downloaded %s to %s", binary_exec, binary_downloaded)
        return binary_downloaded

    logger.error("Failed to download %s", binary_exec)
    console.print(f"Failed to download {binary_exec}", style="red")
    return None


def check_mkvmerge() -> Optional[str]:
    """
    Check for mkvmerge binary and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    system_platform = binary_paths.system
    binary_exec = "mkvmerge.exe" if system_platform == "windows" else "mkvmerge"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug("Found %s in system PATH (%s)", binary_exec, binary_path)
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("mkvtoolnix", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug("Found %s in local binary directory (%s)", binary_exec, binary_local)
        return binary_local

    # STEP 3: Download (only if installation level includes mkvtoolnix)
    if not _should_download("mkvtoolnix"):
        logger.info(f"Skipping download of {binary_exec}")
        return None

    binary_downloaded = binary_paths.download_binary("mkvtoolnix", binary_exec)
    if binary_downloaded:
        logger.debug("Downloaded %s to %s", binary_exec, binary_downloaded)
        return binary_downloaded

    logger.error("Failed to download %s", binary_exec)
    console.print(f"Failed to download {binary_exec}", style="red")
    return None

def check_velora() -> Optional[str]:
    system_platform = binary_paths.system
    binary_exec = "velora.exe" if system_platform == "windows" else "velora"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug("Found %s in system PATH (%s)", binary_exec, binary_path)
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("velora", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug("Found %s in local binary directory (%s)", binary_exec, binary_local)
        return binary_local

    # STEP 3: Download (only if installation level includes velora)
    if not _should_download("velora"):
        logger.info(f"Skipping download of {binary_exec}")
        return None

    binary_downloaded = binary_paths.download_binary("velora", binary_exec)
    if binary_downloaded:
        logger.debug("Downloaded %s to %s", binary_exec, binary_downloaded)
        return binary_downloaded

    logger.error("Failed to download %s", binary_exec)
    console.print(f"Failed to download {binary_exec}", style="red")
    return None