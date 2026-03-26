# 01.03.23

import os
import sys
import stat
import json
import importlib.metadata

from rich.console import Console

from .version import __version__ as source_code_version, __author__, __title__
from VibraVid.utils import config_manager
from VibraVid.utils.http_client import get_headers, create_client
from VibraVid.setup import get_is_binary_installation
from VibraVid.setup.binary_paths import binary_paths


# Variable
if get_is_binary_installation():
    base_path = os.path.join(sys._MEIPASS, "VibraVid")
else:
    base_path = os.path.dirname(__file__)
console = Console()
auto_update_check = config_manager.config.get_bool("DEFAULT", "auto_update_check")
timeout = config_manager.config.get_int("REQUESTS", "timeout")

def fetch_github_releases():
    """Fetch releases data from GitHub API (sync)"""
    response = create_client(headers=get_headers()).get(f"https://api.github.com/repos/{__author__}/{__title__}/releases")
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
        latest_version = latest.get('name', '').replace('v', '').replace('.', '')
        
        # Current version
        try:
            current = importlib.metadata.version(__title__)
        except Exception:
            current = source_code_version
        current_version = str(current).replace('v', '').replace('.', '')
        
        # Version comparison
        if current_version == latest_version:
            console.print(f"[#06A77D]Already on latest version: {current}")
            return False
        console.print(f"[#FFD60A]Current: {current} → Latest: {latest.get('name')}")
        
        # Find appropriate asset
        system = binary_paths._detect_system()
        patterns = {'windows': '.exe', 'linux': 'linux', 'darwin': 'macos'}
        pattern = patterns.get(system, '')
        
        asset = None
        for a in latest.get('assets', []):
            if pattern in a['name'].lower():
                asset = a
                break
        console.print(f"[#00BCD4]Downloading {asset['name']}...")
        
        # Download
        response = create_client(headers=get_headers(), timeout=300, follow_redirects=True).get(asset['browser_download_url'])
        
        if response.status_code != 200:
            console.print("[#E63946]Download failed")
            return False
        
        # Save new executable
        current_exe = sys.executable
        new_exe = current_exe + ".new"
        with open(new_exe, 'wb') as f:
            f.write(response.content)
        console.print("[#06A77D]Download completed!")
        
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
        
        console.print("[#00BCD4]Restarting...")
        sys.exit(0)
        
    except Exception as e:
        console.print(f"[#E63946]Update failed: {e}")
        return False


def update():
    """Check for updates on GitHub and display relevant information."""
    if auto_update_check:
        try:
            response_releases = fetch_github_releases()
        except Exception as e:
            console.print(f"[#E63946]Error accessing GitHub API: {e}")
            return

        # Get latest version tag
        if response_releases:
            last_version = response_releases[0].get('tag_name', 'Unknown')
        else:
            last_version = 'Unknown'

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
        CACHE_FILE = os.path.join(os.getcwd(), ".cache", "ip.json")
        if os.path.exists(CACHE_FILE):
            data_json = json.load(open(CACHE_FILE, "r"))
            country_code = data_json.get("country_code")
    except:
        pass

    console.print(
        f"\n[#FFD60A]{get_execution_mode()} [white]- [#E63946]{binary_paths._detect_system()} [white]- [#06A77D]Version: [#FFD60A]{current_version} [white]- [#E63946]Country: [#FFD60A]{country_code if country_code else 'None'}"
        f"\n"
        f"  [#00BCD4]Help the repository grow today by leaving a [#FFD60A]star [#00BCD4]and [#FFD60A]sharing "
        f"[#00BCD4]it with others online!\n"
        f"      [#9D4EDD]If you'd like to support development and keep the program updated, consider leaving a "
        f"[#FFD60A]donation[#9D4EDD]. Thank you!"
    )

    if str(current_version).lower().replace("v.", "").replace("v", "") != str(last_version).lower().replace("v.", "").replace("v", ""):
        if last_version == "Unknown" or last_version == "Beta Build":
            return

        console.print(f"\n[#E63946]New version available: [#FFD60A]{last_version}")
        
        # Ensure tag formatting for url in case it lacks a leading 'v'
        tag_url = last_version if last_version.startswith("v") else f"v{last_version}"
        console.print(f"[#06A77D]Download it from: [#FFD60A]https://github.com/AstraeLabs/VibraVid/releases/tag/{tag_url}")
        
        if get_execution_mode() == "installer":
            console.print("[#00BCD4]Run with [#FFD60A]-UP [#00BCD4]to auto-update")