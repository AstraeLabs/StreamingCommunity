# 18.07.25

import logging
import os
import shutil
import subprocess
import urllib.request
import zipfile

from rich.console import Console

from VibraVid.tui.i18n import t
from VibraVid.utils import config_manager

from .binary_paths import binary_paths

console = Console()
logger = logging.getLogger(__name__)

INSTALLATION_LEVELS = {
    "": ["ffmpeg", "velora", "yt_dlp", "deno"],
    "essential": ["ffmpeg", "velora", "yt_dlp", "deno"],
    "essential+drm": ["ffmpeg", "velora", "flux", "bento4", "shaka_packager", "yt_dlp", "deno"],
    "drm": ["ffmpeg", "velora", "flux", "bento4", "shaka_packager", "yt_dlp", "deno"],
    "full": ["ffmpeg", "velora", "flux", "dovi_tool", "mkvtoolnix", "yt_dlp", "deno"],
}


def is_termux() -> bool:
    """Check if the application is running inside Termux on Android."""
    return binary_paths.is_termux


def _should_download(tool_group: str) -> bool:
    """Return True if the given tool group should be downloaded based on the installation level."""
    level = str(config_manager.config.get("DEFAULT", "installation", default="essential")).strip().lower()
    if not level:
        level = "essential"
    level_aliases = {
        "drm": "essential+drm",
        "essential+drm": "essential+drm",
        "": "essential",
    }
    normalized_level = level_aliases.get(level, level)
    return tool_group in INSTALLATION_LEVELS.get(normalized_level, INSTALLATION_LEVELS["essential"])

def check_flux(download: bool = True) -> str | None:
    """
    Check for a flux binary and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
    system_platform = binary_paths.system
    binary_exec = "flux.exe" if system_platform == "windows" else "flux"

    # STEP 1: Check system PATH
    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug(f"Found {binary_exec} in system PATH ({binary_path})")
        return binary_path

    # STEP 2: Check local binary directory
    binary_local = binary_paths.get_binary_path("flux", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found {binary_exec} in local binary directory ({binary_local})")
        return binary_local

    if not download:
        return None

    # Termux-specific check: try the prebuilt android-target binary first
    if is_termux():
        if _should_download("flux"):
            binary_downloaded = binary_paths.download_binary("flux", binary_exec)
            if binary_downloaded:
                logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
                return binary_downloaded
        
        console.print("[red]No prebuilt flux binary available for this Termux device.[/red]")
        console.print("[cyan]If required, please compile it and place it in system PATH.[/cyan]")
        return None

    # STEP 3: Download (only if installation level includes flux)
    if not _should_download("flux"):
        return None

    binary_downloaded = binary_paths.download_binary("flux", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
    console.print(f"Failed to download {binary_exec}", style="red")
    return None


def check_yt_dlp() -> str | None:
    """Check for yt-dlp and install it in the shared binary folder if needed."""
    system_platform = binary_paths.system
    binary_exec = "yt-dlp.exe" if system_platform == "windows" else "yt-dlp"

    binary_path = shutil.which(binary_exec) or shutil.which("yt_dlp")
    if binary_path:
        logger.debug(f"Found yt-dlp in system PATH ({binary_path})")
        return binary_path

    binary_local = binary_paths.get_binary_path("yt_dlp", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found yt-dlp in local binary directory ({binary_local})")
        return binary_local

    binary_dir = binary_paths.ensure_binary_directory()
    target_path = os.path.join(binary_dir, binary_exec)

    # Backward compatibility: move any legacy install from the tools folder into the canonical root binary directory.
    legacy_path = os.path.join(binary_dir, "tools", binary_exec)
    if os.path.isfile(legacy_path) and not os.path.exists(target_path):
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.move(legacy_path, target_path)
        logger.debug(f"Migrated legacy yt-dlp from {legacy_path} to {target_path}")

    if os.path.isfile(target_path):
        logger.debug(f"Found yt-dlp in the canonical binary directory ({target_path})")
        return target_path

    if not _should_download("yt_dlp"):
        logger.info(f"Skipping download of {binary_exec}")
        return None

    try:
        if os.path.exists(target_path):
            return target_path

        console.print(f"[cyan]Downloading yt-dlp.exe to {target_path}...[/cyan]")
        url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe" if system_platform == "windows" else "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
        urllib.request.urlretrieve(url, target_path)
        if os.path.isfile(target_path):
            logger.debug(f"Downloaded yt-dlp to {target_path}")
            console.print(f"[green]yt-dlp.exe installed at {target_path}[/green]")
            if system_platform != "windows":
                os.chmod(target_path, 0o755)
            return target_path
    except Exception as exc:
        logger.error(f"Failed to download yt-dlp: {exc}")
        console.print(
            f"{t('setup_binary_download_failed_error', default='Download fallito di yt-dlp: {error}').format(error=exc)}",
            style="red",
        )
    return None


def check_deno() -> str | None:
    """Check for Deno and install it in the shared binary folder if needed."""
    system_platform = binary_paths.system
    binary_exec = "deno.exe" if system_platform == "windows" else "deno"

    binary_path = shutil.which(binary_exec)
    if binary_path:
        logger.debug(f"Found Deno in system PATH ({binary_path})")
        return binary_path

    binary_local = binary_paths.get_binary_path("deno", binary_exec)
    if binary_local and os.path.isfile(binary_local):
        logger.debug(f"Found Deno in local binary directory ({binary_local})")
        return binary_local

    binary_dir = binary_paths.ensure_binary_directory()
    deno_exe_path = os.path.join(binary_dir, binary_exec)

    legacy_path = os.path.join(binary_dir, "tools", binary_exec)
    if os.path.isfile(legacy_path) and not os.path.exists(deno_exe_path):
        os.makedirs(os.path.dirname(deno_exe_path), exist_ok=True)
        shutil.move(legacy_path, deno_exe_path)
        logger.debug(f"Migrated legacy Deno from {legacy_path} to {deno_exe_path}")

    if os.path.isfile(deno_exe_path):
        logger.debug(f"Found Deno in the canonical binary directory ({deno_exe_path})")
        return deno_exe_path

    if not _should_download("deno"):
        logger.info(f"Skipping download of {binary_exec}")
        return None

    try:
        if os.path.exists(deno_exe_path):
            return deno_exe_path

        if system_platform == "windows":
            archive_name = "deno-x86_64-pc-windows-msvc.zip"
            archive_url = f"https://github.com/denoland/deno/releases/latest/download/{archive_name}"
        elif system_platform == "darwin":
            arch_suffix = "x86_64" if binary_paths.arch == "x64" else "aarch64"
            archive_name = f"deno-{arch_suffix}-apple-darwin.zip"
            archive_url = f"https://github.com/denoland/deno/releases/latest/download/{archive_name}"
        else:
            arch_suffix = "x86_64" if binary_paths.arch == "x64" else "aarch64"
            archive_name = f"deno-{arch_suffix}-unknown-linux-gnu.zip"
            archive_url = f"https://github.com/denoland/deno/releases/latest/download/{archive_name}"

        console.print(f"[cyan]Downloading deno.exe to {deno_exe_path}...[/cyan]")
        zip_path = os.path.join(binary_dir, "deno_temp.zip")
        temp_extract_dir = os.path.join(binary_dir, "temp_deno")
        logger.info(f"Downloading Deno from {archive_url}")
        urllib.request.urlretrieve(archive_url, zip_path)

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(temp_extract_dir)

        extracted_root = None
        for item_name in os.listdir(temp_extract_dir):
            item_path = os.path.join(temp_extract_dir, item_name)
            if os.path.isfile(item_path) and os.path.basename(item_path).lower() in {"deno.exe", "deno"}:
                shutil.move(item_path, deno_exe_path)
                extracted_root = item_path
                break
            if os.path.isdir(item_path):
                extracted_root = item_path
                break

        if extracted_root is not None and os.path.isdir(extracted_root):
            for item in os.listdir(extracted_root):
                src = os.path.join(extracted_root, item)
                dst = os.path.join(binary_dir, item)
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    shutil.move(src, dst)

        shutil.rmtree(temp_extract_dir, ignore_errors=True)
        if os.path.exists(zip_path):
            os.remove(zip_path)

        if os.path.isfile(deno_exe_path):
            logger.debug(f"Downloaded Deno to {deno_exe_path}")
            console.print(f"[green]deno.exe installed at {deno_exe_path}[/green]")
            if system_platform != "windows":
                os.chmod(deno_exe_path, 0o755)
            try:
                ver_res = subprocess.run([deno_exe_path, "--version"], capture_output=True, text=True, timeout=10)
                if ver_res.returncode == 0:
                    logger.debug(f"Deno version: {ver_res.stdout.strip()}")
                else:
                    logger.warning(f"Deno version check failed: {ver_res.stderr.strip() or ver_res.stdout.strip()}")
            except Exception as e:
                logger.warning(f"Deno verification run failed: {e}")
            return deno_exe_path
    except Exception as exc:
        logger.error(f"Failed to download Deno: {exc}")
        console.print(
            f"{t('setup_binary_download_failed_error', default='Download fallito di Deno: {error}').format(error=exc)}",
            style="red",
        )
        for stale_path in (os.path.join(binary_dir, "deno_temp.zip"), os.path.join(binary_dir, "temp_deno")):
            if os.path.exists(stale_path):
                if os.path.isdir(stale_path):
                    shutil.rmtree(stale_path, ignore_errors=True)
                else:
                    os.remove(stale_path)
    return None


def check_ffmpeg(download: bool = True) -> tuple[str | None, str | None]:
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

    if not download:
        return None, None

    # Termux-specific check
    if is_termux():
        console.print("[red]FFmpeg/FFprobe is required on Termux.[/red]")
        console.print("[cyan]Please install it using: [yellow]pkg install ffmpeg[/cyan]")
        return None, None

    # STEP 3: Download (only if installation level includes ffmpeg)
    if not _should_download("ffmpeg"):
        return None, None

    ffmpeg_downloaded = binary_paths.download_binary("ffmpeg", ffmpeg_name)
    ffprobe_downloaded = binary_paths.download_binary("ffmpeg", ffprobe_name)
    if ffmpeg_downloaded and ffprobe_downloaded:
        logger.debug(f"Downloaded ffmpeg ({ffmpeg_downloaded}) and ffprobe ({ffprobe_downloaded})")
        return ffmpeg_downloaded, ffprobe_downloaded

    logger.error("Failed to download FFmpeg")
    console.print("Failed to download FFmpeg", style="red")
    return None, None


def check_dovi_tool(download: bool = True) -> str | None:
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

    if not download:
        return None

    # Termux-specific check
    if is_termux():
        console.print("[yellow]dovi_tool not found in Termux environment.[/yellow]")
        cargo_path = shutil.which("cargo")
        if cargo_path:
            console.print("[cyan]Cargo detected. Attempting to build dovi_tool from source...[/cyan]")
            binary_dir = binary_paths.ensure_binary_directory()
            try:
                cmd = [
                    "cargo",
                    "install",
                    "--quiet",
                    "--git",
                    "https://github.com/quietvoid/dovi_tool",
                    "--root",
                    os.path.dirname(binary_dir),
                ]
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
        return None

    binary_downloaded = binary_paths.download_binary("dovi_tool", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
    console.print(f"Failed to download {binary_exec}", style="red")
    return None


def check_mkvmerge(download: bool = True) -> str | None:
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

    if not download:
        return None

    # Termux-specific check
    if is_termux():
        console.print("[red]MKVToolNix (mkvmerge) is required on Termux.[/red]")
        console.print("[cyan]Please install it using: [yellow]pkg install mkvtoolnix[/cyan]")
        return None

    # STEP 3: Download (only if installation level includes mkvtoolnix)
    if not _should_download("mkvtoolnix"):
        return None

    binary_downloaded = binary_paths.download_binary("mkvtoolnix", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
    console.print(f"Failed to download {binary_exec}", style="red")
    return None


def check_velora(download: bool = True) -> str | None:
    """
    Check for velora binary and download if not found.
    Order: system PATH -> binary directory -> download from GitHub
    """
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

    if not download:
        return None

    # Termux-specific check: try the prebuilt android-target binary first
    if is_termux():
        if _should_download("velora"):
            binary_downloaded = binary_paths.download_binary("velora", binary_exec)
            if binary_downloaded:
                logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
                return binary_downloaded

    # STEP 3: Download (only if installation level includes velora)
    if not _should_download("velora"):
        return None

    binary_downloaded = binary_paths.download_binary("velora", binary_exec)
    if binary_downloaded:
        logger.debug(f"Downloaded {binary_exec} to {binary_downloaded}")
        return binary_downloaded

    logger.error(f"Failed to download {binary_exec}")
    console.print(f"Failed to download {binary_exec}", style="red")
    return None
