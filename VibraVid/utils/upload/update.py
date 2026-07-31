# 01.03.23

import importlib.metadata
import json
import logging
import os
import re
import stat
import subprocess
import sys

from rich.console import Console

from VibraVid.setup import get_is_binary_installation
from VibraVid.setup.binary_paths import binary_paths
from VibraVid.utils import _startup_prefetch, config_manager
from VibraVid.utils.http_client import create_client, get_headers

from .version import __author__, __title__
from .version import __version__ as source_code_version

# Variable
if get_is_binary_installation():
    base_path = os.path.join(sys._MEIPASS, "VibraVid")
else:
    base_path = os.path.dirname(__file__)
console = Console()
logger = logging.getLogger(__name__)
auto_update_check = config_manager.config.get_bool("DEFAULT", "auto_update_check")
timeout = config_manager.config.get_int("REQUESTS", "timeout")


def fetch_github_releases():
    """Fetch releases data from GitHub API (sync)"""
    prefetched = _startup_prefetch.collect("releases", timeout=timeout)
    if prefetched is not None:
        return prefetched

    url = f"https://api.github.com/repos/{__author__}/{__title__}/releases"
    logger.info(f"Checking latest {__title__} release: {url}")
    with create_client(headers=get_headers()) as client:
        response = client.get(url)
    return response.json()


def get_execution_mode():
    """Get the execution mode of the application"""
    if get_is_binary_installation():
        return "installer"

    try:
        package_location = importlib.metadata.files(__title__)
        if any("site-packages" in str(path) for path in package_location):
            return "pip"
    except importlib.metadata.PackageNotFoundError:
        pass

    return "source_code"


def auto_update():
    """Automatically update the binary to latest version"""
    if not get_is_binary_installation():
        console.print("[#E63946]Auto-update works only for binary installations")
        return False

    try:
        console.print("[#00BCD4]Checking for updates...")
        releases = fetch_github_releases()
        latest = releases[0]
        latest_version = latest.get("name", "").replace("v", "").replace(".", "")

        # Current version
        try:
            current = importlib.metadata.version(__title__)
        except Exception:
            current = source_code_version
        current_version = str(current).replace("v", "").replace(".", "")

        # Version comparison
        if current_version == latest_version:
            console.print(f"[#06A77D]Already on latest version: {current}")
            return False
        console.print(f"[#FFD60A]Current: {current} -> Latest: {latest.get('name')}")

        # Find appropriate asset
        system = binary_paths._detect_system()
        patterns = {"windows": ".exe", "linux": "linux", "darwin": "macos"}
        pattern = patterns.get(system, "")

        asset = None
        for a in latest.get("assets", []):
            if pattern in a["name"].lower():
                asset = a
                break
        console.print(f"[#00BCD4]Downloading {asset['name']}...")

        # Download
        with create_client(headers=get_headers(), timeout=300, follow_redirects=True) as client:
            response = client.get(asset["browser_download_url"])

        if response.status_code != 200:
            console.print("[#E63946]Download failed")
            return False

        # Save new executable
        current_exe = sys.executable
        new_exe = current_exe + ".new"
        with open(new_exe, "wb") as f:
            f.write(response.content)
        console.print("[#06A77D]Download completed!")

        # Write update script
        if system == "windows":
            script = current_exe + ".bat"
            with open(script, "w") as f:
                f.write("@echo off\n")
                f.write("timeout /t 2 /nobreak >nul\n")
                f.write(f'move /y "{new_exe}" "{current_exe}"\n')
                f.write(f'start "" "{current_exe}"\n')
                f.write('del "%~f0"\n')

            os.startfile(script)

        else:
            os.chmod(new_exe, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)

            script = current_exe + ".sh"
            with open(script, "w") as f:
                f.write("#!/bin/bash\n")
                f.write("sleep 2\n")
                f.write(f'mv "{new_exe}" "{current_exe}"\n')
                f.write(f'chmod +x "{current_exe}"\n')
                f.write(f'"{current_exe}" &\n')
                f.write(f'rm "{script}"\n')

            os.chmod(script, stat.S_IRWXU)
            os.system(f'nohup "{script}" &')

        console.print("[#00BCD4]Restarting...")
        sys.exit(0)

    except Exception as e:
        console.print(f"[#E63946]Update failed: {e}")
        return False


def _fetch_latest_velora_version():
    """Return the latest Velora version from the Velora repo's Cargo.toml, or None."""
    prefetched = _startup_prefetch.collect("velora_version", timeout=timeout)
    if prefetched is not None:
        return prefetched

    try:
        url = f"https://raw.githubusercontent.com/{__author__}/Velora/main/Cargo.toml"
        logger.info(f"Checking latest Velora version: {url}")
        with create_client(headers=get_headers()) as client:
            response = client.get(url)
        response.raise_for_status()

        # The package version is the first `version = "x.y.z"` line under [package].
        for line in response.text.splitlines():
            stripped = line.strip()
            if stripped.startswith("version") and "=" in stripped:
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")

    except Exception as e:
        logger.debug(f"Failed to fetch latest Velora version: {e}")
    return None


def _get_local_velora_version(velora_path):
    """Return the version reported by `velora --version`, or None if unavailable."""
    try:
        out = subprocess.run([velora_path, "--version"], capture_output=True, text=True, timeout=5)
        raw = (out.stdout or out.stderr).strip()
        first = raw.splitlines()[0] if raw else ""

        # Velora prints a JSON line e.g. {"version": "2.0.2"}; fall back to a regex.
        try:
            version = str(json.loads(first).get("version", "")).strip()
            if version:
                return version
        except Exception:
            pass

        m = re.search(r"v?(\d+(?:\.\d+){1,3})", raw)
        return m.group(1) if m else None
    except Exception as e:
        logger.debug(f"Failed to read local Velora version: {e}")
        return None


def _version_tuple(version):
    """Parse a dotted version string into a comparable tuple of ints, or None."""
    if not version:
        return None
    parts = re.findall(r"\d+", version)
    return tuple(int(p) for p in parts) if parts else None


def check_velora_update() -> dict:
    """Re-download the Velora binary when it is outdated."""
    latest_version = _fetch_latest_velora_version()
    if not latest_version:
        return {"success": False, "message": "Could not fetch the latest Velora version."}

    from VibraVid.setup import get_velora_path
    from VibraVid.setup import system as setup_system

    velora_path = get_velora_path()
    if not velora_path:
        return {"success": False, "message": "Velora binary not found."}

    # Only manage the binary we downloaded ourselves; never touch a system-PATH install.
    managed_dir = os.path.abspath(binary_paths.get_binary_directory())
    if os.path.dirname(os.path.abspath(velora_path)) != managed_dir:
        logger.info("Velora resolved outside the managed binary directory; skipping auto-update")
        return {"success": False, "message": "Velora is a system-installed binary; skipping auto-update."}

    local_version = _get_local_velora_version(velora_path)
    local_tuple = _version_tuple(local_version)
    latest_tuple = _version_tuple(latest_version)

    needs_update = local_tuple is None or (latest_tuple is not None and local_tuple < latest_tuple)
    if not needs_update:
        logger.debug(f"Velora is up to date (local: {local_version}, latest: {latest_version})")
        return {
            "success": True,
            "updated": False,
            "local": local_version,
            "latest": latest_version,
            "message": f"Velora is up to date ({local_version}).",
        }

    console.print(f"[#FFD60A]Velora outdated (local: {local_version or 'unknown'} -> latest: {latest_version}), updating...")

    try:
        os.remove(velora_path)
    except OSError as e:
        logger.warning(f"Failed to remove stale Velora binary: {e}")
        return {"success": False, "message": f"Failed to remove stale Velora binary: {e}"}

    # Drop cached resolutions so the next lookup re-downloads the binary.
    binary_paths.invalidate_binary(os.path.basename(velora_path))
    setup_system.reset_velora_path()

    new_path = get_velora_path()
    if not new_path:
        console.print("[#E63946]Velora re-download failed")
        return {"success": False, "message": "Velora re-download failed."}

    return {
        "success": True,
        "updated": True,
        "local": local_version,
        "latest": latest_version,
        "message": f"Velora updated: {local_version or 'unknown'} -> {latest_version}.",
    }


def update():
    """Check for updates on GitHub and display relevant information."""
    if auto_update_check:
        try:
            check_velora_update()
        except Exception as e:
            logger.debug(f"Velora update check failed: {e}")

        try:
            response_releases = fetch_github_releases()
        except Exception as e:
            console.print(f"[#E63946]Error accessing GitHub API: {e}")
            return

        # Get latest version tag
        if response_releases:
            last_version = response_releases[0].get("tag_name", "Unknown")
        else:
            last_version = "Unknown"

    else:
        last_version = "Unknown"

    # Get the current version (installed version)
    try:
        current_version = importlib.metadata.version(__title__)
    except importlib.metadata.PackageNotFoundError:
        current_version = source_code_version

    # Get country code
    country_code = None
    try:
        CACHE_FILE = os.path.join(config_manager.base_path, ".cache", "ip.json")
        if os.path.exists(CACHE_FILE):
            data_json = json.load(open(CACHE_FILE))
            country_code = data_json.get("country_code")
    except Exception:
        pass

    logger.info(f"Execution mode: {get_execution_mode()}, System: {binary_paths._detect_system()}, Version: {current_version}, Latest: {last_version}, Country: {country_code}")
    console.print(f"      [green]{get_execution_mode()} [dim]·[/] [red]{current_version} [dim]·[/] [cyan]{binary_paths.system} {binary_paths.arch} [dim]·[/] [purple]{country_code if country_code else 'None'} [dim]·[/] [link=https://discord.com/invite/8vV68UGRc7][#5865F2]Discord[/link] [dim]·[/] [link=https://www.paypal.com/donate/?hosted_button_id=UXTWMT8P6HE2C][#ea4aaa]Donate[/link]")

    if str(current_version).lower().replace("v.", "").replace("v", "") != str(last_version).lower().replace(
        "v.", ""
    ).replace("v", ""):
        if last_version == "Unknown" or last_version == "Beta Build":
            return

        tag_url = last_version if last_version.startswith("v") else f"v{last_version}"
        mode = get_execution_mode()
        if mode == "installer":
            console.print(f"\n[red]New [#00BCD4]version available: [#FFD60A][link=https://github.com/AstraeLabs/VibraVid/releases/tag/{tag_url}]{last_version}[/link] [dim]·[/] [#00BCD4]Run with [#FFD60A]-UP [#00BCD4]to auto-update")
        elif mode == "source_code":
            console.print(f"\n[red]New [#00BCD4]version available: [#FFD60A][link=https://github.com/AstraeLabs/VibraVid/releases/tag/{tag_url}]{last_version}[/link] [dim]·[/] [#00BCD4]Run [#FFD60A]git pull [#00BCD4]to update")
