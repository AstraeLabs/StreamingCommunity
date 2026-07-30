# Completed Downloads & File Manager Integration TUI Design Spec

**Date:** 2026-07-30  
**Status:** Approved  
**Target Module:** `VibraVid/tui` & `VibraVid/utils`

---

## 1. Overview & Objectives

Provide a seamless TUI experience for completed downloads in VibraVid, allowing users to:
1. View active downloads and completed downloads in the **Downloads Screen** (`DownloadsScreen`).
2. Launch downloaded media files directly with the OS default media player from both **Downloads** and **History** screens.
3. Open the containing output folder in the OS file manager (Finder / Explorer / File Manager) with the downloaded file selected.

---

## 2. System Architecture & Components

### 2.1 OS Integration Helper (`VibraVid/utils/system_open.py`)
A cross-platform helper module to launch files and reveal them in file managers cleanly and safely:
- `open_file(path: str) -> Tuple[bool, str]`:
  - **macOS**: `open "<path>"` via `subprocess.Popen`
  - **Windows**: `os.startfile(path)` or `explorer.exe "<path>"`
  - **Linux**: `xdg-open "<path>"`
  - Returns `(success, error_message)`.
- `open_folder(path: str) -> Tuple[bool, str]`:
  - If `path` is a file, finds its parent directory or passes reveal flags.
  - **macOS**: `open -R "<filepath>"` (reveals file in Finder) or `open "<dirpath>"`
  - **Windows**: `explorer.exe /select,"<filepath>"`
  - **Linux**: `xdg-open "<dirpath>"`
  - Returns `(success, error_message)`.

---

## 3. UI Specifications

### 3.1 Downloads Screen (`VibraVid/tui/screens/downloads.py`)
- **Layout**:
  - Panel title: `Downloads & Active Tasks`
  - Top Table (`#downloads-table`): Active & Downloading items.
  - Sub-title: `Completed Downloads`
  - Bottom Table (`#completed-table`): Completed downloads fetched from `download_tracker.get_history()` (filtered by `status == "completed"`).
  - Action Bar (`#downloads-actions`):
    - `[▶ Avvia File]` (`id="btn-play-file"`): Plays selected completed item.
    - `[📁 Apri Cartella]` (`id="btn-open-folder"`): Opens parent folder in OS file manager.
    - `[🛑 Annulla Download]` (`id="btn-cancel"`): Stops active download.
    - `[🧹 Pulisci]` (`id="btn-clear"`): Clears completed/history items.
- **Interactions**:
  - Double clicking or pressing `ENTER` on a row in `#completed-table` automatically launches the file.

### 3.2 History Screen (`VibraVid/tui/screens/history.py`)
- **Layout**:
  - History Table (`#history-table`): Displays past download records.
  - Action Bar (`#history-actions`):
    - `[▶ Avvia File]` (`id="play-history-btn"`): Launches the file of selected item if status is `completed`.
    - `[📁 Apri Cartella]` (`id="open-folder-history-btn"`): Opens parent directory in Finder/Explorer.
    - `[🔄 Re-enqueue]` (`id="retry-history-btn"`).
    - `[🧹 Clear history]` (`id="clear-history-btn"`).
    - `[↻ Refresh]` (`id="refresh-btn"`).
- **Interactions**:
  - `ENTER` key on a `completed` history row launches the file.

---

## 4. Theme & Styling Updates (`VibraVid/tui/theme.tcss`)
- Style `#completed-table` with smooth border layering matching `#downloads-table`.
- Ensure buttons in `#downloads-actions` and `#history-actions` highlight smoothly when enabled/focused.

---

## 5. Verification Plan
1. Run `python3 -m py_compile` on modified Python modules.
2. Launch `uv run python tui.py` and test navigating to `Downloads` and `History`.
3. Verify that `▶ Avvia File` and `📁 Apri Cartella` work on completed downloads.
