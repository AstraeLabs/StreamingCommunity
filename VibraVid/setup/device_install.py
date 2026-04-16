# 18.07.25

import logging
import os
from typing import Optional

from rich.console import Console

from .binary_paths import binary_paths


console = Console()
logger = logging.getLogger(__name__)


class DeviceSearcher:
    def __init__(self):
        self.base_dir = binary_paths.ensure_binary_directory()

    def _check_existing(self, ext: str) -> Optional[str]:
        """Check for existing files with given extension in binary directory."""
        try:
            for file in os.listdir(self.base_dir):
                if file.lower().endswith(ext):
                    path = os.path.join(self.base_dir, file)
                    logger.debug("Found %s file in binary directory: %s", ext, path)
                    return path
                
            return None
        
        except Exception as e:
            logger.exception("Error checking existing %s files", ext)
            console.print(f"[red]Error checking existing {ext} files: {e}")
            return None

    def _find_recursively(self, ext: str = None, start_dir: str = ".", filename: str = None) -> Optional[str]:
        """
        Find file recursively by extension or exact filename starting from start_dir.
        If filename is provided, search for that filename. Otherwise, search by extension.
        """
        try:
            for root, dirs, files in os.walk(start_dir):
                for file in files:
                    if filename:
                        if file == filename:
                            path = os.path.join(root, file)
                            logger.info("Found %s at %s", filename, path)
                            return path
                        
                    elif ext:
                        if file.lower().endswith(ext):
                            path = os.path.join(root, file)
                            logger.info("Found %s at %s", ext, path)
                            return path
                        
            return None
        except Exception as e:
            logger.exception("Error during recursive search for filename %s", filename)
            console.print(f"[red]Error during recursive search for filename {filename}: {e}")
            return None

    def search(self, ext: str = None, filename: str = None) -> Optional[str]:
        """
        Search for file with given extension or exact filename in binary directory or recursively.
        If filename is provided, search for that filename. Otherwise, search by extension.
        """
        if filename:
            try:
                target_path = os.path.join(self.base_dir, filename)
                if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
                    logger.info("Found %s in binary directory: %s", filename, target_path)
                    return target_path
            except Exception as e:
                logger.exception("Error checking for existing file %s", filename)
                console.print(f"[red]Error checking for existing file {filename}: {e}")
                return None

            return self._find_recursively(filename=filename)
        
        else:
            path = self._check_existing(ext)
            if path:
                return path
            return self._find_recursively(ext=ext)


def check_device_wvd_path() -> Optional[str]:
    """Check for device.wvd file in binary directory and extract from PNG if not found."""
    try:
        searcher = DeviceSearcher()
        return searcher.search('.wvd')
    except Exception:
        return None

def check_device_prd_path() -> Optional[str]:
    """Check for device.prd file in binary directory and search recursively if not found."""
    try:
        searcher = DeviceSearcher()
        return searcher.search('.prd')
    except Exception:
        return None