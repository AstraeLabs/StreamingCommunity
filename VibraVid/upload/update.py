# 01.03.23

import os
import sys
import stat
import importlib.metadata

import httpx
from rich.console import Console

from .version import __version__ as source_code_version, __author__, __title__
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import get_userAgent
from VibraVid.setup import get_is_binary_installation
from VibraVid.setup.binary_paths import binary_paths


# Variable
if get_is_binary_installation():
    base_path = os.path.join(sys._MEIPASS, "VibraVid")
else:
    base_path = os.path.dirname(__file__)
console = Console()
auto_update_check = config_manager.config.get_bool("DEFAULT", "auto_update_check")


def fetch_github_releases():
    """Fetch releases data from GitHub API (sync)"""
    response = httpx.get(
        f"https://api.github.com/repos/{__author__}/{__title__}/releases",
        headers={'user-agent': get_userAgent()},
        timeout=config_manager.config.get_int("REQUESTS", "timeout"),
        follow_redirects=True
    )
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
        console.print("[red]Auto-update works only for binary installations")
        return False
    
    try:
        console.print("[cyan]Checking for updates...")
        releases = fetch_github_releases()
        latest = releases[0]
        latest_version = latest.get('name', '').replace('v', '').replace('.', '')
        
        # Current version
        try:
            current = importlib.metadata.version(__title__)
        except Exception:
            current = source_code_version
        current_version = str(current).replace('v', '').replace('.', '')
        
        # Version comparison
        if current_version == latest_version:
            console.print(f"[green]Already on latest version: {current}")
            return False
        console.print(f"[yellow]Current: {current} → Latest: {latest.get('name')}")
        
        # Find appropriate asset
        system = binary_paths._detect_system()
        patterns = {'windows': '.exe', 'linux': 'linux', 'darwin': 'macos'}
        pattern = patterns.get(system, '')
        
        asset = None
        for a in latest.get('assets', []):
            if pattern in a['name'].lower():
                asset = a
                break
        console.print(f"[cyan]Downloading {asset['name']}...")
        
        # Download
        response = httpx.get(asset['browser_download_url'], headers={'user-agent': get_userAgent()}, timeout=300, follow_redirects=True)
        
        if response.status_code != 200:
            console.print("[red]Download failed")
            return False
        
        # Save new executable
        current_exe = sys.executable
        new_exe = current_exe + ".new"
        with open(new_exe, 'wb') as f:
            f.write(response.content)
        console.print("[green]Download completed!")
        
        # Write update script
        if system == 'windows':
            script = current_exe + ".bat"
            with open(script, 'w') as f:
                f.write('@echo off\n')
                f.write('timeout /t 2 /nobreak >nul\n')
                f.write(f'move /y "{new_exe}" "{current_exe}"\n')
                f.write(f'start "" "{current_exe}"\n')
                f.write('del "%~f0"\n')
            
            os.startfile(script)
        
        else:
            os.chmod(new_exe, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP)
            
            script = current_exe + ".sh"
            with open(script, 'w') as f:
                f.write('#!/bin/bash\n')
                f.write('sleep 2\n')
                f.write(f'mv "{new_exe}" "{current_exe}"\n')
                f.write(f'chmod +x "{current_exe}"\n')
                f.write(f'"{current_exe}" &\n')
                f.write(f'rm "{script}"\n')
            
            os.chmod(script, stat.S_IRWXU)
            os.system(f'nohup "{script}" &')
        
        console.print("[cyan]Restarting...")
        sys.exit(0)
        
    except Exception as e:
        console.print(f"[red]Update failed: {e}")
        return False


def update():
    """Check for updates on GitHub and display relevant information."""
    if auto_update_check:
        try:
            response_releases = fetch_github_releases()
        except Exception as e:
            console.print(f"[red]Error accessing GitHub API: {e}")
            return

        # Calculate total download count from all releases
        total_download_count = sum(
            asset['download_count']
            for release in response_releases
            for asset in release.get('assets', [])
        )

        # Get latest version tag
        if response_releases:
            last_version = response_releases[0].get('tag_name', 'Unknown')
        else:
            last_version = 'Unknown'

    else:
        total_download_count = "-1"
        last_version = "Unknown"

    # Get the current version (installed version)
    try:
        current_version = importlib.metadata.version(__title__)
    except importlib.metadata.PackageNotFoundError:
        current_version = source_code_version

    console.print(
        f"\n[red]{__title__} has been downloaded: [yellow]{total_download_count}"
        f"\n[yellow]{get_execution_mode()} [white]- [red]{binary_paths._detect_system()} [white]- [green]Current installed version: [yellow]{current_version} "
        f"\n"
        f"  [cyan]Help the repository grow today by leaving a [yellow]star [cyan]and [yellow]sharing "
        f"[cyan]it with others online!\n"
        f"      [magenta]If you'd like to support development and keep the program updated, consider leaving a "
        f"[yellow]donation[magenta]. Thank you!"
    )

    if str(current_version).lower().replace("v.", "").replace("v", "") != str(last_version).lower().replace("v.", "").replace("v", ""):
        if last_version == "Unknown" or last_version == "Beta Build":
            return

        console.print(f"\n[red]New version available: [yellow]{last_version}")
        
        # Ensure tag formatting for url in case it lacks a leading 'v'
        tag_url = last_version if last_version.startswith("v") else f"v{last_version}"
        console.print(f"[green]Download it from: [yellow]https://github.com/AstraeLabs/VibraVid/releases/tag/{tag_url}")
        
        if get_execution_mode() == "installer":
            console.print("[cyan]Run with [yellow]-UP [cyan]to auto-update")