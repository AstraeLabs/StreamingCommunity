# 24.01.24

import os
import shutil
import logging

from unidecode import unidecode
from rich.console import Console
from rich.prompt import Prompt
from pathvalidate import sanitize_filename, sanitize_filepath

from ..setup.binary_paths import binary_paths


msg = Prompt()
console = Console()
logger = logging.getLogger(__name__)


class OsManager:
    def __init__(self):
        self.system = binary_paths._detect_system()
        self.max_length = self._get_max_length()

    def _get_max_length(self) -> int:
        """Get max filename length based on OS."""
        return 255 if self.system == 'windows' else 4096

    def get_sanitize_file(self, filename: str, year: str = None) -> str:
        """Sanitize filename. Optionally append a year in format ' (YYYY)' if year is provided and valid."""
        if not filename:
            return filename

        # Extract and validate year if provided
        year_str = ""
        if year:
            y = str(year).split('-')[0].strip()
            if y.isdigit() and len(y) == 4:
                year_str = f" ({y})"

        # Decode and sanitize base filename
        decoded = unidecode(filename)
        sanitized = sanitize_filename(decoded)

        # Split name and extension
        name, ext = os.path.splitext(sanitized)

        # Append year if present
        name_with_year = name + year_str

        # Calculate available length for name considering the '...' and extension
        max_name_length = self.max_length - len('...') - len(ext)

        # Truncate name if it exceeds the max name length
        if len(name_with_year) > max_name_length:
            name_with_year = name_with_year[:max_name_length] + '...'

        # Ensure the final file name includes the extension
        return name_with_year + ext

    def get_sanitize_path(self, path: str) -> str:
        """Sanitize a complete path while preserving the native OS path separator."""
        if not path:
            return path

        # Decode unicode characters first (unidecode is safe on separators and drive letters — it only touches non-ASCII glyphs).
        decoded = unidecode(path)

        if self.system == 'windows':
            # ── Windows ───────────────────────────────────────────────────────
            # Normalise *input* separators to backslash so the checks below
            # work regardless of whether the caller used / or \.
            normalised = decoded.replace('/', '\\')

            # Handle network paths (UNC or IP-based)  \\server\share\...
            if normalised.startswith('\\\\'):
                parts = normalised.split('\\')
                sanitized_parts = parts[:4]
                if len(parts) > 4:
                    sanitized_parts.extend([
                        self.get_sanitize_file(part)
                        for part in parts[4:]
                        if part
                    ])
                return '\\'.join(sanitized_parts)

            # Handle drive letters  C:\...
            if len(normalised) >= 2 and normalised[1] == ':':
                drive = normalised[:2]          # e.g. "C:"
                rest  = normalised[2:].lstrip('\\')
                parts = [p for p in rest.split('\\') if p]
                sanitized_parts = [drive] + [self.get_sanitize_file(p) for p in parts]
                return '\\'.join(sanitized_parts)

            # Regular relative path
            parts = [p for p in normalised.split('\\') if p]
            return '\\'.join(self.get_sanitize_file(p) for p in parts)

        else:
            # ── Unix-like (Linux / macOS) ──────────────────────────────────
            # Use pathvalidate only on non-Windows where forward slashes are
            # the native separator and the function behaves correctly.
            sanitized = sanitize_filepath(decoded)
            is_absolute = sanitized.startswith('/')
            parts = sanitized.replace('\\', '/').split('/')
            sanitized_parts = [
                self.get_sanitize_file(part)
                for part in parts
                if part
            ]
            result = '/'.join(sanitized_parts)
            if is_absolute:
                result = '/' + result
            return result

    def get_glob_path(self, path: str) -> str:
        """Escape path for glob to prevent issues with special characters like brackets."""
        import glob
        return glob.escape(path)

    def create_path(self, path: str, mode: int = 0o755) -> bool:
        """
        Create directory path with specified permissions.

        Args:
            path (str): Path to create.
            mode (int, optional): Directory permissions. Defaults to 0o755.

        Returns:
            bool: True if path created successfully, False otherwise.
        """
        try:
            path = str(path)
            sanitized_path = self.get_sanitize_path(path)
            os.makedirs(sanitized_path, mode=mode, exist_ok=True)
            return True

        except Exception as e:
            logger.error(f"Path creation error: {e}")
            return False

    def remove_folder(self, folder_path: str) -> bool:
        """
        Safely remove a folder.

        Args:
            folder_path (str): Path of directory to remove.

        Returns:
            bool: Removal status.
        """
        try:
            shutil.rmtree(folder_path)
            return True

        except OSError as e:
            logger.error(f"Folder removal error: {e}")
            return False


class InternetManager():
    def format_file_size(self, size_bytes) -> str:
        """Formats a file size from bytes into a human-readable string representation."""
        if isinstance(size_bytes, str):
            try:
                size_str = str(size_bytes).upper().strip()
                if 'GB' in size_str:
                    return int(float(size_str.replace('GB', '')) * 1024 * 1024 * 1024)
                elif 'MB' in size_str:
                    return int(float(size_str.replace('MB', '')) * 1024 * 1024)
                elif 'KB' in size_str:
                    return int(float(size_str.replace('KB', '')) * 1024)
                elif 'B' in size_str:
                    return int(float(size_str.replace('B', '')))
                return None
            except Exception:
                return None
        
        elif isinstance(size_bytes, float) or isinstance(size_bytes, int):
            if size_bytes <= 0:
                return "0B"

            units = ['B', 'KB', 'MB', 'GB', 'TB']
            unit_index = 0
            while size_bytes >= 1024 and unit_index < len(units) - 1:
                size_bytes /= 1024
                unit_index += 1
            return f"{size_bytes:.2f} {units[unit_index]}"

    def format_transfer_speed(self, bytes: float) -> str:
        """Formats a transfer speed from bytes per second into a human-readable string representation."""
        if isinstance(bytes, float):
            if bytes < 1024:
                return f"{bytes:.2f} Bytes/s"
            elif bytes < 1024 * 1024:
                return f"{bytes / 1024:.2f} KB/s"
            else:
                return f"{bytes / (1024 * 1024):.2f} MB/s"
        
        elif isinstance(bytes, int):
            if bytes >= 1024 * 1024 * 1024:
                return f"{bytes/(1024*1024*1024):.2f} GB"
            elif bytes >= 1024 * 1024:
                return f"{bytes/(1024*1024):.2f} MB"
            elif bytes >= 1024:
                return f"{bytes/1024:.2f} KB"
            else:
                return f"{bytes} B"
            
    def format_time(self, seconds: float, add_hours: bool = False) -> str:
        """Format seconds to MM:SS or HH:MM:SS"""
        if seconds < 0 or seconds == float('inf'):
            return "00:00"
        
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        if add_hours:
            hours = int(minutes // 60)
            minutes = int(minutes % 60)
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"


# Initialize 
os_manager = OsManager()
internet_manager = InternetManager()