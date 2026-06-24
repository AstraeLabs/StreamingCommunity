# 18.07.25

import os
import logging
import shutil
import subprocess
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


def is_termux() -> bool:
    """Check if the application is running inside Termux on Android."""
    return 'TERMUX_VERSION' in os.environ or os.path.exists('/data/data/com.termux/files/usr/bin')


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
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("bento4", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    # Termux-specific check
    if is_termux():
        console.print("[red]Bento4 (mp4decrypt) is required on Termux.[/red]")
        console.print("[cyan]Please install it using: [yellow]pkg install bento4[/cyan]")
        return None

    # STEP 3: Download (only if installation level includes bento4)
    if not _should_download("bento4"):
        logger.info(f"Skipping download of {binary_exec}")
        return None

    binary_downloaded = binary_paths.download_binary("bento4", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
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
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("bento4", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    # Termux-specific check
    if is_termux():
        console.print("[red]Bento4 (mp4dump) is required on Termux.[/red]")
        console.print("[cyan]Please install it using: [yellow]pkg install bento4[/cyan]")
        return None

    # STEP 3: Download (only if installation level includes bento4)
    if not _should_download("bento4"):
        logger.info(f"Skipping download of {binary_exec}")
        return None

    binary_downloaded = binary_paths.download_binary("bento4", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
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
        logger.debug(f"Found ffmpeg ({ffmpeg_path}) and ffprobe ({ffprobe_path}) in system PATH")
        return ffmpeg_path, ffprobe_path

    # STEP 2: Check binary directory
    ffmpeg_local = binary_paths.get_binary_path("ffmpeg", ffmpeg_name)
    ffprobe_local = binary_paths.get_binary_path("ffmpeg", ffprobe_name)
    if ffmpeg_local and os.path.isfile(ffmpeg_local) and ffprobe_local and os.path.isfile(ffprobe_local):
        logger.debug(f"Found ffmpeg ({ffmpeg_local}) and ffprobe ({ffprobe_local}) in local binary directory")
        return ffmpeg_local, ffprobe_local

    # Termux-specific check
    if is_termux():
        console.print("[red]FFmpeg/FFprobe is required on Termux.[/red]")
        console.print("[cyan]Please install it using: [yellow]pkg install ffmpeg[/cyan]")
        return None, None

    # STEP 3: Download (only if installation level includes ffmpeg)
    if not _should_download("ffmpeg"):
        logger.info("Skipping download of ffmpeg/ffprobe")
        return None, None

    ffmpeg_downloaded = binary_paths.download_binary("ffmpeg", ffmpeg_name)
    ffprobe_downloaded = binary_paths.download_binary("ffmpeg", ffprobe_name)
    if ffmpeg_downloaded and ffprobe_downloaded:
        logger.debug(f"Downloaded ffmpeg ({ffmpeg_downloaded}) and ffprobe ({ffprobe_downloaded})")
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
        logger.debug(f"Found Shaka Packager in system PATH ({packager_path})")
        return packager_path

    # STEP 2: Check binary directory
    packager_local = binary_paths.get_binary_path("shaka_packager", packager_name)
    if packager_local and os.path.isfile(packager_local):
        logger.debug(f"Found Shaka Packager in local binary directory ({packager_local})")
        return packager_local

    # Termux-specific check
    if is_termux():
        console.print("[red]Shaka Packager is not supported natively on Termux downloaders.[/red]")
        console.print("[cyan]If required, please compile it and place it in system PATH.[/cyan]")
        return None

    # STEP 3: Download (only if installation level includes shaka_packager)
    if not _should_download("shaka_packager"):
        logger.info(f"Skipping download of {packager_name}")
        return None

    packager_downloaded = binary_paths.download_binary("shaka_packager", packager_name)
    if packager_downloaded:
        logger.debug(f"Downloaded Shaka Packager to {packager_downloaded}")
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
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("dovi_tool", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    # Termux-specific check
    if is_termux():
        console.print("[yellow]dovi_tool not found in Termux environment.[/yellow]")
        cargo_path = shutil.which("cargo")
        if cargo_path:
            console.print("[cyan]Cargo detected. Attempting to build dovi_tool from source...[/cyan]")
            binary_dir = binary_paths.ensure_binary_directory()
            try:
                cmd = ["cargo", "install", "--quiet", "--git", "https://github.com/quietvoid/dovi_tool", "--root", os.path.dirname(binary_dir)]
                subprocess.run(cmd, check=True)
                cargo_bin = os.path.join(os.path.dirname(binary_dir), "bin", "dovi_tool")
                dest_bin = os.path.join(binary_dir, "dovi_tool")
                if os.path.isfile(cargo_bin):
                    shutil.move(cargo_bin, dest_bin)
                    os.chmod(dest_bin, 0o755)
                    console.print("[green]dovi_tool compiled and installed successfully![/green]")
                    return dest_bin
            except Exception as e:
                console.print(f"[red]Failed to compile dovi_tool from source: {e}[/red]")
        console.print("[cyan]Please compile manually using: [yellow]cargo install --git https://github.com/quietvoid/dovi_tool[/cyan]")
        return None

    # STEP 3: Download (only if installation level includes dovi_tool)
    if not _should_download("dovi_tool"):
        logger.info(f"Skipping download of {binary_exec}")
        return None

    binary_downloaded = binary_paths.download_binary("dovi_tool", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
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
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("mkvtoolnix", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    # Termux-specific check
    if is_termux():
        console.print("[red]MKVToolNix (mkvmerge) is required on Termux.[/red]")
        console.print("[cyan]Please install it using: [yellow]pkg install mkvtoolnix[/cyan]")
        return None

    # STEP 3: Download (only if installation level includes mkvtoolnix)
    if not _should_download("mkvtoolnix"):
        logger.info(f"Skipping download of {binary_exec}")
        return None

    binary_downloaded = binary_paths.download_binary("mkvtoolnix", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
    console.print(f"Failed to download {binary_exec}", style="red")
    return None


def check_mkvpropedit() -> Optional[str]:
    """
    Check for mkvpropedit binary and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    system_platform = binary_paths.system
    binary_exec = "mkvpropedit.exe" if system_platform == "windows" else "mkvpropedit"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    # STEP 2: Check local binary directory (same mkvtoolnix package as mkvmerge)
    binary_local = binary_paths.get_binary_path("mkvtoolnix", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    if is_termux():
        return None

    if not _should_download("mkvtoolnix"):
        logger.info(f"Skipping download of {binary_exec}")
        return None

    binary_downloaded = binary_paths.download_binary("mkvtoolnix", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
    return None


def check_velora() -> Optional[str]:
    system_platform = binary_paths.system
    binary_exec = "velora.exe" if system_platform == "windows" else "velora"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("velora", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    # Termux-specific check
    if is_termux():
        console.print("[yellow]Velora binary not found in Termux environment.[/yellow]")
        cargo_path = shutil.which("cargo")
        if cargo_path:
            console.print("[cyan]Cargo detected. Attempting to build Velora from source...[/cyan]")
            binary_dir = binary_paths.ensure_binary_directory()
            try:
                cmd = ["cargo", "install", "--quiet", "--git", "https://github.com/AstraeLabs/Velora", "--root", os.path.dirname(binary_dir)]
                subprocess.run(cmd, check=True)
                cargo_bin = os.path.join(os.path.dirname(binary_dir), "bin", "Velora")
                dest_bin = os.path.join(binary_dir, "velora")
                if os.path.isfile(cargo_bin):
                    shutil.move(cargo_bin, dest_bin)
                    os.chmod(dest_bin, 0o755)
                    console.print("[green]Velora compiled and installed successfully![/green]")
                    return dest_bin
            except Exception as e:
                console.print(f"[red]Failed to compile Velora from source: {e}[/red]")
        console.print("[red]Please install rust/clang and compile manually:[/red]")
        console.print("[white]pkg install rust clang -y && cargo install --git https://github.com/AstraeLabs/Velora[/white]")
        return None

    # STEP 3: Download (only if installation level includes velora)
    if not _should_download("velora"):
        logger.info(f"Skipping download of {binary_exec}")
        return None

    binary_downloaded = binary_paths.download_binary("velora", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
    console.print(f"Failed to download {binary_exec}", style="red")
    return None
