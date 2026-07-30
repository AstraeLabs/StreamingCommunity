"""Cross-platform helper to launch files and reveal them in OS file managers."""

import os
import platform
import subprocess
from typing import Tuple


def open_file(path: str) -> Tuple[bool, str]:
    """Launch a file with the system default application.

    Returns (success, message).
    """
    if not path or not os.path.exists(path):
        return False, f"File non trovato: {path}"

    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", path])
        elif system == "Windows":
            os.startfile(path)
        else:
            subprocess.Popen(["xdg-open", path])
        return True, f"File avviato: {os.path.basename(path)}"
    except Exception as e:
        return False, f"Impossibile aprire il file: {e}"


def open_folder(path: str) -> Tuple[bool, str]:
    """Open the directory in the OS file manager, highlighting the file if supported.

    Returns (success, message).
    """
    if not path:
        return False, "Percorso non specificato."

    abs_path = os.path.abspath(path)
    dir_path = os.path.dirname(abs_path) if os.path.isfile(abs_path) else abs_path

    if not os.path.exists(dir_path) and not os.path.exists(abs_path):
        return False, f"Cartella non trovata: {dir_path}"

    system = platform.system()
    try:
        if system == "Darwin":
            if os.path.isfile(abs_path):
                subprocess.Popen(["open", "-R", abs_path])
            else:
                subprocess.Popen(["open", dir_path])
        elif system == "Windows":
            if os.path.isfile(abs_path):
                subprocess.Popen(["explorer.exe", f"/select,{abs_path}"])
            else:
                subprocess.Popen(["explorer.exe", dir_path])
        else:
            subprocess.Popen(["xdg-open", dir_path])
        return True, f"Cartella aperta: {dir_path}"
    except Exception as e:
        return False, f"Impossibile aprire la cartella: {e}"
