from __future__ import annotations

import os
from pathlib import Path

from rich.console import Console

from VibraVid.core.direct_download.adapter import try_download_with_ytdlp_direct
from VibraVid.tui.i18n import t
from VibraVid.utils import config_manager

console = Console(emoji=False)


def _detect_browser_for_cookies() -> str | None:
    """Return a browser name/browser path suitable for yt-dlp --cookies-from-browser."""
    import shutil

    candidates = [
        "msedge",
        "microsoft-edge",
        "edge",
        "chrome",
        "google-chrome",
        "chromium",
        "brave",
        "brave-browser",
        "firefox",
    ]
    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return candidate
    windows_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    ]
    for candidate in windows_paths:
        if os.path.exists(candidate):
            return candidate
    return None


def _open_browser_for_cookie_refresh() -> bool:
    """Open the default browser to the YouTube login page so the user can refresh a valid session."""
    try:
        import subprocess
        import webbrowser

        browser = _detect_browser_for_cookies()
        if browser:
            if os.name == "nt" and browser.lower().endswith(".exe"):
                subprocess.Popen([browser, "https://www.youtube.com"], shell=False)
            else:
                subprocess.Popen([browser, "https://www.youtube.com"], shell=False)
            return True
        webbrowser.open("https://www.youtube.com")
        return True
    except Exception:
        return False


def handle_direct_download_isolated(args) -> tuple[bool, bool]:
    """Compatibility wrapper for the legacy direct-download handler API.

    The CLI expects a function returning (handled, ok), where `handled` indicates that
    a direct URL download was processed and `ok` whether it succeeded. This adapter keeps
    the older public contract while dispatching to the isolated direct-download backend.
    """
    url = getattr(args, "down", None) or getattr(args, "url", None)
    if not url:
        return False, False

    out_root = getattr(args, "output", None) or None
    if not out_root:
        try:
            configured_root = config_manager.config.get("OUTPUT", "root_path", default=None)
        except Exception:
            configured_root = None

        if configured_root:
            out_root = str(Path(str(configured_root)).expanduser())
        else:
            out_root = str(Path(config_manager.base_path or os.getcwd()) / "video")

        if Path(str(out_root)).name.lower() != "i miei download":
            out_root = str(Path(out_root) / "I Miei Download")

    out_dir = str(Path(out_root).expanduser())
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    filename_base = (
        getattr(args, "meta_title", None)
        or getattr(args, "title", None)
        or Path(str(url).strip()).stem
        or "download"
    )
    title = getattr(args, "meta_title", None) or filename_base
    source = {
        "title": title,
        "display_label": title,
        "headers": getattr(args, "headers", None),
        "cookies": getattr(args, "cookies", None),
        "no_playlist": getattr(args, "no_playlist", False),
    }

    result = try_download_with_ytdlp_direct(
        url=str(url).strip(),
        out_dir=out_dir,
        filename_base=str(filename_base),
        source=source,
        download_id=getattr(args, "download_id", None),
        site_name=getattr(args, "meta_site", None),
        timeout=getattr(args, "timeout", 300),
        stream_output=True,
    )

    file_path = result.get("path")
    if file_path:
        console.print()
        console.print("[bold green]Download completato[/bold green]")
        console.print()
        return True, True

    error_text = result.get("error") or t('direct_download_unsupported', default='URL not supported or invalid')
    console.print()
    console.print(f"[bold red]{error_text}[/bold red]")
    console.print()
    return True, False
