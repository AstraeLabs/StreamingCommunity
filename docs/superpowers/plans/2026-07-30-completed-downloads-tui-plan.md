# Completed Downloads & File Manager Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable launching completed media files and opening their containing folders in Finder/Explorer from both Downloads and History screens in the VibraVid TUI.

**Architecture:** Create a cross-platform `system_open` helper in `VibraVid/utils/system_open.py` using native OS processes (`open`/`open -R`, `os.startfile`/`explorer`, `xdg-open`). Update `DownloadsScreen` to include a dedicated completed downloads table and action buttons (`▶ Avvia File`, `📁 Apri Cartella`). Update `HistoryScreen` with matching action buttons and `ENTER` key support.

**Tech Stack:** Python 3.10+, Textual TUI, subprocess, OS desktop services.

---

### Task 1: Cross-Platform System Open Helper

**Files:**
- Create: `VibraVid/utils/system_open.py`
- Modify: `VibraVid/utils/__init__.py`

- [ ] **Step 1: Write `VibraVid/utils/system_open.py`**

```python
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
```

- [ ] **Step 2: Check Python compilation**

Run: `python3 -m py_compile VibraVid/utils/system_open.py`  
Expected: Exit code 0

- [ ] **Step 3: Commit Task 1**

```bash
git add VibraVid/utils/system_open.py
git commit -m "feat: add cross-platform system_open helper for files and folders"
```

---

### Task 2: Update DownloadsScreen with Completed Downloads Table & Action Buttons

**Files:**
- Modify: `VibraVid/tui/screens/downloads.py`
- Modify: `VibraVid/tui/theme.tcss`

- [ ] **Step 1: Update `VibraVid/tui/screens/downloads.py`**

Modify `DownloadsScreen` to render two tables (`#downloads-table` and `#completed-table`) and add action buttons `btn-play-file` and `btn-open-folder`.

```python
# In VibraVid/tui/screens/downloads.py
from VibraVid.utils.system_open import open_file, open_folder
```

Add `@on(Button.Pressed, "#btn-play-file")`, `@on(Button.Pressed, "#btn-open-folder")`, and handle `DataTable.RowSelected` on `#completed-table`.

- [ ] **Step 2: Check Python compilation**

Run: `python3 -m py_compile VibraVid/tui/screens/downloads.py`  
Expected: Exit code 0

- [ ] **Step 3: Update `theme.tcss` styles for completed table**

Ensure `#completed-table` is styled nicely with `height: 1fr` or max-height and proper border/background matching `#downloads-table`.

- [ ] **Step 4: Commit Task 2**

```bash
git add VibraVid/tui/screens/downloads.py VibraVid/tui/theme.tcss
git commit -m "feat(tui): add completed downloads table and file launch actions to Downloads screen"
```

---

### Task 3: Update HistoryScreen with File Launch & Open Folder Actions

**Files:**
- Modify: `VibraVid/tui/screens/history.py`

- [ ] **Step 1: Update `VibraVid/tui/screens/history.py`**

Add imports:
```python
from VibraVid.utils.system_open import open_file, open_folder
```

Add buttons `[▶ Avvia File]` (`id="play-history-btn"`) and `[📁 Apri Cartella]` (`id="open-folder-history-btn"`) to `#history-actions`. Add click handlers for `#play-history-btn` and `#open-folder-history-btn`, and add `on_data_table_row_selected` / `DataTable.RowSelected` behavior.

- [ ] **Step 2: Check Python compilation**

Run: `python3 -m py_compile VibraVid/tui/screens/history.py`  
Expected: Exit code 0

- [ ] **Step 3: Commit Task 3**

```bash
git add VibraVid/tui/screens/history.py
git commit -m "feat(tui): add play file and open folder actions to History screen"
```

---

### Task 4: Integration Verification

- [ ] **Step 1: Check compilation across all modified files**

Run: `python3 -m py_compile VibraVid/utils/system_open.py VibraVid/tui/screens/downloads.py VibraVid/tui/screens/history.py`  
Expected: Exit code 0

- [ ] **Step 2: Commit final implementation**

```bash
git commit --allow-empty -m "chore: completed downloads and system open feature verification complete"
```
