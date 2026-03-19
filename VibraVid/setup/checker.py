# 18.07.25

import os
import logging
import shutil
from typing import Optional, Tuple

from rich.console import Console

from .binary_paths import binary_paths

console = Console()
logger = logging.getLogger(__name__)


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
        logger.info("Found %s in system PATH (%s)", binary_exec, binary_path)
        return binary_path
    
    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("bento4", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.info("Found %s in local binary directory (%s)", binary_exec, binary_local)
        return binary_local
    
    # STEP 3: Download
    binary_downloaded = binary_paths.download_binary("bento4", binary_exec)
    if binary_downloaded:
        logger.info("Downloaded %s to %s", binary_exec, binary_downloaded)
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
        logger.info("Found %s in system PATH (%s)", binary_exec, binary_path)
        return binary_path
    
    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("bento4", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.info("Found %s in local binary directory (%s)", binary_exec, binary_local)
        return binary_local
    
    # STEP 3: Download
    binary_downloaded = binary_paths.download_binary("bento4", binary_exec)
    if binary_downloaded:
        logger.info("Downloaded %s to %s", binary_exec, binary_downloaded)
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
        logger.info("Found ffmpeg (%s) and ffprobe (%s) in system PATH", ffmpeg_path, ffprobe_path)
        return ffmpeg_path, ffprobe_path
    
    # STEP 2: Check binary directory
    ffmpeg_local = binary_paths.get_binary_path("ffmpeg", ffmpeg_name)
    ffprobe_local = binary_paths.get_binary_path("ffmpeg", ffprobe_name)
    
    if ffmpeg_local and os.path.isfile(ffmpeg_local) and ffprobe_local and os.path.isfile(ffprobe_local):
        logger.info("Found ffmpeg (%s) and ffprobe (%s) in local binary directory", ffmpeg_local, ffprobe_local)
        return ffmpeg_local, ffprobe_local
    
    # STEP 3: Download from GitHub repository
    ffmpeg_downloaded = binary_paths.download_binary("ffmpeg", ffmpeg_name)
    ffprobe_downloaded = binary_paths.download_binary("ffmpeg", ffprobe_name)
    
    if ffmpeg_downloaded and ffprobe_downloaded:
        logger.info("Downloaded ffmpeg (%s) and ffprobe (%s)", ffmpeg_downloaded, ffprobe_downloaded)
        return ffmpeg_downloaded, ffprobe_downloaded
    
    logger.error("Failed to download FFmpeg")
    console.print("Failed to download FFmpeg", style="red")
    return None, None

def check_n_m3u8dl_re() -> Optional[str]:
    """
    Check for N_m3u8DL-RE binary and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    system_platform = binary_paths.system
    binary_exec = "N_m3u8DL-RE.exe" if system_platform == "windows" else "N_m3u8DL-RE"
    
    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    
    if binary_path:
        logger.info("Found %s in system PATH (%s)", binary_exec, binary_path)
        return binary_path
    
    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("n_m3u8dl", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.info("Found %s in local binary directory (%s)", binary_exec, binary_local)
        return binary_local
    
    # STEP 3: Download
    binary_downloaded = binary_paths.download_binary("n_m3u8dl", binary_exec)
    if binary_downloaded:
        logger.info("Downloaded %s to %s", binary_exec, binary_downloaded)
        return binary_downloaded
    
    logger.error("Failed to download %s", binary_exec)
    console.print(f"Failed to download {binary_exec}", style="red")
    return None

def check_shaka_packager() -> Tuple[Optional[str], Optional[str]]:
    """
    Check for Shaka Packager executables and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    system_platform = binary_paths.system
    packager_name = "packager.exe" if system_platform == "windows" else "packager"
    
    # STEP 1: Check system PATH
    packager_path = shutil.which(packager_name)
    
    if packager_path:
        logger.info("Found Shaka Packager in system PATH (%s)", packager_path)
        return packager_path
    
    # STEP 2: Check binary directory
    packager_local = binary_paths.get_binary_path("shaka_packager", packager_name)
    
    if packager_local and os.path.isfile(packager_local):
        logger.info("Found Shaka Packager in local binary directory (%s)", packager_local)
        return packager_local
    
    # STEP 3: Download from GitHub repository
    packager_downloaded = binary_paths.download_binary("shaka_packager", packager_name)
    
    if packager_downloaded:
        logger.info("Downloaded Shaka Packager to %s", packager_downloaded)
        return packager_downloaded
    
    logger.error("Failed to download Shaka Packager")
    console.print("Failed to download Shaka Packager", style="red")
    return None, None