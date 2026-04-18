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

class InternetManager:
    def format_file_size(self, size_bytes) -> str:
        """Format *size_bytes* (int or float) as a human-readable size string."""
        try:
            nb = float(size_bytes)
        except (TypeError, ValueError):
            return "0 B"

        if nb <= 0:
            return "0 B"

        units = ['B', 'KB', 'MB', 'GB', 'TB']
        unit_index = 0
        while nb >= 1024 and unit_index < len(units) - 1:
            nb /= 1024
            unit_index += 1
        return f"{nb:.2f} {units[unit_index]}"

    def parse_file_size(self, size_str: str) -> int | None:
        """Parse a human-readable size string such as ``"1.5 GB"`` into bytes."""
        if not isinstance(size_str, str):
            return None
        try:
            s = size_str.upper().strip()
            if 'TB' in s:
                return int(float(s.replace('TB', '').strip()) * 1024 ** 4)
            if 'GB' in s:
                return int(float(s.replace('GB', '').strip()) * 1024 ** 3)
            if 'MB' in s:
                return int(float(s.replace('MB', '').strip()) * 1024 ** 2)
            if 'KB' in s:
                return int(float(s.replace('KB', '').strip()) * 1024)
            if 'B' in s:
                return int(float(s.replace('B', '').strip()))
            return None
        except Exception:
            return None

    def format_transfer_speed(self, bytes_per_second) -> str:
        """
        Format *bytes_per_second* (int or float) as a human-readable speed string.
        Always appends ``/s``.  Returns ``"0 B/s"`` for zero or negative values.
        """
        try:
            bps = float(bytes_per_second)
        except (TypeError, ValueError):
            return "0 B/s"

        if bps <= 0:
            return "0 B/s"
        if bps >= 1024 * 1024 * 1024:
            return f"{bps / (1024 ** 3):.2f} GB/s"
        if bps >= 1024 * 1024:
            return f"{bps / (1024 ** 2):.2f} MB/s"
        if bps >= 1024:
            return f"{bps / 1024:.2f} KB/s"
        return f"{bps:.2f} Bytes/s"

    def format_time(self, seconds: float, add_hours: bool = False) -> str:
        """Format seconds to MM:SS or HH:MM:SS."""
        if seconds < 0 or seconds == float('inf'):
            return "00:00"

        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        if add_hours:
            hours = int(minutes // 60)
            minutes = int(minutes % 60)
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"


# Initialize
os_manager = OsManager()
internet_manager = InternetManager()