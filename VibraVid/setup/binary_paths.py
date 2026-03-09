# 19.09.25

import os
import platform
from typing import Optional

import httpx
from rich.console import Console

from VibraVid.utils.http_client import get_headers


console = Console()


class BinaryPaths:
    def __init__(self):
        self.system = self._detect_system()
        self.arch = self._detect_arch()
        self.home_dir = os.path.expanduser('~')
        self.github_repo = "https://raw.githubusercontent.com/AstraeLabs/Binary/main"
        self.paths_cache = None
    
    def _detect_system(self) -> str:
        """Detect and normalize the operating system name."""
        system = platform.system().lower()
        supported_systems = ['windows', 'darwin', 'linux']
        
        if system not in supported_systems:
            raise ValueError(f"Unsupported OS: {system}")
        
        return system
    
    def _detect_arch(self) -> str:
        """Detect and normalize the system architecture."""
        machine = platform.machine().lower()
        arch_map = {
            'amd64': 'x64', 
            'x86_64': 'x64',
            'arm64': 'arm64',
            'aarch64': 'arm64',
        }
        return arch_map.get(machine, 'x64')
    
    def get_binary_directory(self) -> str:
        """Get the binary directory path based on the operating system."""
        if self.system == 'windows':
            return os.path.join(os.path.splitdrive(self.home_dir)[0] + os.path.sep, 'binary')
        elif self.system == 'darwin':
            return os.path.join(self.home_dir, 'Applications', 'binary')
        else:  # linux
            return os.path.join(self.home_dir, '.local', 'bin', 'binary')
    
    def ensure_binary_directory(self, mode: int = 0o755) -> str:
        """Create the binary directory if it doesn't exist."""
        binary_dir = self.get_binary_directory()
        os.makedirs(binary_dir, mode=mode, exist_ok=True)
        return binary_dir
    
    def _load_paths_json(self) -> dict:
        """Load binary paths from GitHub repository."""
        if self.paths_cache is not None:
            return self.paths_cache
        
        try:
            url = f"{self.github_repo}/binary_paths.json"
            response = httpx.get(url, timeout=10, headers=get_headers())
            response.raise_for_status()
            self.paths_cache = response.json()
            return self.paths_cache
        except Exception:
            return {}
    
    def get_binary_path(self, tool: str, binary_name: str) -> Optional[str]:
        """
        Get the full path to a binary from the repository.
        
        Args:
            tool: Tool name (ffmpeg, bento4)
            binary_name: Binary name (ffmpeg.exe, mp4decrypt, etc.)
        
        Returns:
            Full local path to the binary or None if not found
        """
        binary_dir = self.get_binary_directory()
        local_path = os.path.join(binary_dir, binary_name)
        
        if os.path.isfile(local_path):
            return local_path
        
        return None
    
    def download_binary(self, tool: str, binary_name: str) -> Optional[str]:
        """
        Download a specific binary from GitHub repository directly to binary directory.
        
        Args:
            tool: Tool name (ffmpeg, bento4)
            binary_name: Binary name to download
        
        Returns:
            Full local path to the downloaded binary or None if failed
        """
        paths_json = self._load_paths_json()
        key = f"{self.system}_{self.arch}_{tool}"
        console.log(f"[cyan]Downloading [red]{binary_name} [cyan]for [yellow]{tool} [cyan]on [red]{self.system} {self.arch}")
        
        if key not in paths_json:
            return None
        
        for rel_path in paths_json[key]:
            if rel_path.endswith(binary_name):
                url = f"{self.github_repo}/binaries/{rel_path}"
                local_path = os.path.join(self.get_binary_directory(), binary_name)
                console.log(f"[cyan]Downloading from [red]{url} [cyan]to [yellow]{local_path}")
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                
                try:
                    response = httpx.get(url, timeout=60, headers=get_headers())
                    response.raise_for_status()
                    
                    with open(local_path, 'wb') as f:
                        f.write(response.content)
                    
                    # Set executable permission on Unix systems
                    if self.system != 'windows':
                        os.chmod(local_path, 0o755)
                    
                    return local_path
                except Exception:
                    return None
        
        return None


# Instance
binary_paths = BinaryPaths()