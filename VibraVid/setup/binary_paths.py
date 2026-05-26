# 19.09.25

import os
import platform
import logging
import threading
from typing import Dict, Optional

from rich.console import Console

from VibraVid.utils.http_client import get_headers, create_client


console = Console()
logger = logging.getLogger(__name__)


class BinaryPaths:
    def __init__(self):
        self.system = self._detect_system()
        self.arch = self._detect_arch()
        self.home_dir = os.path.expanduser('~')
        self.binary_dir_override = os.environ.get('VIBRAVID_BINARY_DIR') or os.environ.get('BINARY_DIR')
        self.github_repo = "https://raw.githubusercontent.com/AstraeLabs/Binary/main"
        self._paths_json_cache: Optional[dict] = None
        self._resolved: Dict[str, str] = {}
        self._cache_lock = threading.Lock()
        self._download_lock = threading.Lock()

    def _detect_system(self) -> str:
        """Detect and normalize the operating system name."""
        system = platform.system().lower()
        supported_systems = ['windows', 'darwin', 'linux']
        if system not in supported_systems:
            logger.warning(f"Unsupported OS detected ({system}), falling back to linux semantics")
            return 'linux'
        
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
        """Return the platform-specific directory where binaries are stored."""
        if self.binary_dir_override:
            return self.binary_dir_override

        if self.system == 'windows':
            return os.path.join(os.path.splitdrive(self.home_dir)[0] + os.path.sep, 'binary')
        elif self.system == 'darwin':
            return os.path.join(self.home_dir, 'Applications', 'binary')
        else:  # linux
            return os.path.join(self.home_dir, '.local', 'bin', 'binary')

    def ensure_binary_directory(self, mode: int = 0o755) -> str:
        """Create the binary directory if it does not already exist."""
        binary_dir = self.get_binary_directory()
        os.makedirs(binary_dir, mode=mode, exist_ok=True)
        return binary_dir

    def _load_paths_json(self) -> dict:
        """Fetch the binary_paths.json manifest from GitHub (thread-safe)."""
        if self._paths_json_cache is not None:
            return self._paths_json_cache

        with self._cache_lock:
            # Double-checked locking: another thread may have populated the cache while we were waiting to acquire the lock.
            if self._paths_json_cache is not None:
                return self._paths_json_cache

            try:
                url = f"{self.github_repo}/binary_paths.json"
                logger.info(f"Loading binary paths JSON from {url}")
                client = create_client(headers=get_headers())
                response = client.get(url)
                client.close()
                response.raise_for_status()
                self._paths_json_cache = response.json()
                logger.info(f"Loaded binary paths JSON ({len(self._paths_json_cache)} entries)")
                return self._paths_json_cache
            except Exception as e:
                logger.error(f"Failed to load binary paths JSON: {e}", exc_info=True)
                return {}

    def get_binary_path(self, tool: str, binary_name: str) -> Optional[str]:
        """Return the local path of *binary_name* if it has already been resolved (cache hit) or if the file exists on disk."""
        if binary_name in self._resolved:
            return self._resolved[binary_name]

        local_path = os.path.join(self.get_binary_directory(), binary_name)
        if os.path.isfile(local_path):
            logger.debug(f"Found local binary {binary_name} at {local_path}")
            self._resolved[binary_name] = local_path
            return local_path

        return None

    def download_binary(self, tool: str, binary_name: str) -> Optional[str]:
        """
        Download *binary_name* from GitHub and store it in the binary
        directory (thread-safe).

        If the binary has already been resolved (cache hit) or the file is
        already present on disk, the cached path is returned immediately
        without any network access.

        When multiple threads request the same binary concurrently, only the
        first acquires the lock and performs the download; the others wait
        and then find the file ready on disk.
        """
        if binary_name in self._resolved:
            return self._resolved[binary_name]

        local_path = os.path.join(self.get_binary_directory(), binary_name)

        with self._download_lock:
            # Double-checked locking: another thread may have completed the download while we were waiting to acquire the lock.
            if binary_name in self._resolved:
                return self._resolved[binary_name]

            # The file may also be present on disk even if not in the cache (e.g. left over from a previous application session).
            if os.path.isfile(local_path) and os.path.getsize(local_path) > 0:
                logger.info(f"Binary {binary_name} already on disk, skipping download")
                self._resolved[binary_name] = local_path
                return local_path

            paths_json = self._load_paths_json()
            key = f"{self.system}_{self.arch}_{tool}"
            logger.info(f"Looking up binary paths for key {key}")
            console.log(f"[cyan]Downloading [red]{binary_name} [cyan]for [yellow]{tool} [cyan]on [red]{self.system} {self.arch}")

            if key not in paths_json:
                logger.error(f"No binary paths found for key {key} in binary paths JSON")
                
                # Fallback: the file may have been installed manually or by a previous session
                # even though the remote manifest is unavailable (e.g. HTTP 404 / network error).
                if os.path.isfile(local_path) and os.path.getsize(local_path) > 0:
                    logger.info(f"Manifest unavailable but binary {binary_name} found on disk, using it")
                    self._resolved[binary_name] = local_path
                    return local_path
                return None

            for rel_path in paths_json[key]:
                if not rel_path.endswith(binary_name):
                    continue

                url = f"{self.github_repo}/binaries/{rel_path}"
                logger.info(f"Downloading {binary_name} from {url} to {local_path}")
                console.log(f"[cyan]Downloading from [red]{url} [cyan]to [yellow]{local_path}")
                os.makedirs(os.path.dirname(local_path), exist_ok=True)

                # Write to a temporary file first, then rename atomically.
                tmp_path = local_path + ".tmp"
                try:
                    client = create_client(headers=get_headers())
                    response = client.get(url)
                    client.close()
                    response.raise_for_status()

                    with open(tmp_path, 'wb') as f:
                        f.write(response.content)

                    if self.system != 'windows':
                        os.chmod(tmp_path, 0o755)

                    # Atomic rename: the binary becomes visible to other processes only after the write is fully complete.
                    os.replace(tmp_path, local_path)

                    logger.info(f"Downloaded {binary_name} to {local_path}")
                    self._resolved[binary_name] = local_path
                    return local_path

                except Exception as e:
                    logger.error(f"Failed to download {binary_name} from {url}: {e}", exc_info=True)
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except OSError:
                        pass
                    return None

        return None


binary_paths = BinaryPaths()
