# 29.07.26

"""VibraVid Textual application shell.

Launched by tui.py. The classic CLI in VibraVid/cli/run.py is untouched
and remains the default interface. ESC always navigates back one screen,
never kills the app; quit is Ctrl+Q from anywhere (M2 will add the
confirm-when-downloads-active dialog).
"""

import logging

from textual.app import App
from textual.binding import Binding
from textual.events import Resize

from VibraVid.tui.screens.downloads import DownloadsScreen
from VibraVid.tui.screens.help import HelpScreen
from VibraVid.tui.screens.home import HomeScreen
from VibraVid.tui.screens.placeholder import PlaceholderScreen
from VibraVid.tui.screens.settings import SettingsScreen
from VibraVid.tui.screens.system import SystemScreen
from VibraVid.utils.upload.version import __version__

logger = logging.getLogger(__name__)

MIN_WIDTH = 80
MIN_HEIGHT = 24

# Areas reachable from the global keymap. Real screens land in later
# milestones: downloads -> M2, queue/history -> M3, settings/system -> M4.
AREAS = {
    "downloads": ("Downloads", "Live progress with per-track bars, cancel and retry.", "M2"),
    "queue": ("Queue", "Batch queue, shared with the --queue-* CLI commands.", "M3"),
    "history": ("History", "Past downloads with status, paths and errors.", "M3"),
    "settings": ("Settings", "config.json and login.json editors.", "M4"),
    "system": ("System", "Dependencies, DRM, logs and update check.", "M4"),
}


class VibraVidApp(App):
    """Root application: screen stack, global keymap, theme."""

    CSS_PATH = "theme.tcss"
    TITLE = "VibraVid"
    SUB_TITLE = f"v{__version__}"

    BINDINGS = [
        Binding("escape", "back", "Back", priority=True),
        Binding("d", "open_area('downloads')", "Downloads"),
        Binding("q", "open_area('queue')", "Queue"),
        Binding("h", "open_area('history')", "History"),
        Binding("comma", "open_area('settings')", "Settings"),
        Binding("s", "open_area('system')", "System"),
        Binding("question_mark", "help", "Help"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._small_terminal_warned = False

    def on_mount(self) -> None:
        self.push_screen(HomeScreen())

    # ── Global actions ────────────────────────────────────────────────────

    def action_back(self) -> None:
        """ESC goes one level back, never kills the process."""
        if len(self.screen_stack) > 1:
            self.pop_screen()

    def action_open_area(self, area: str) -> None:
        if area == "downloads":
            if not isinstance(self.screen, DownloadsScreen):
                self.push_screen(DownloadsScreen())
            return
        if area == "settings":
            if not isinstance(self.screen, SettingsScreen):
                self.push_screen(SettingsScreen())
            return
        if area == "system":
            if not isinstance(self.screen, SystemScreen):
                self.push_screen(SystemScreen())
            return
        if isinstance(self.screen, PlaceholderScreen) and self.screen.area == area:
            return
        title, body, milestone = AREAS[area]
        self.push_screen(PlaceholderScreen(area=area, title=title, body=body, milestone=milestone))

    def action_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
        else:
            self.push_screen(HelpScreen())

    # ── Small terminal guard ──────────────────────────────────────────────

    def on_resize(self, event: Resize) -> None:
        small = event.size.width < MIN_WIDTH or event.size.height < MIN_HEIGHT
        if small and not self._small_terminal_warned:
            self._small_terminal_warned = True
            self.notify(
                f"Terminal is smaller than {MIN_WIDTH}x{MIN_HEIGHT}: layout may degrade.",
                title="Small terminal",
                severity="warning",
            )
        elif not small:
            self._small_terminal_warned = False


def main() -> None:
    # Preload the site registry and the GUI adapter registry before Textual
    # takes over the terminal (both may print warnings through rich/print).
    # The stdio proxy makes any later stray print harmless for the display.
    from VibraVid.tui import bridge

    bridge.install_stdio_proxy()
    try:
        bridge.list_sites()
        bridge.preload_registry()
    except Exception as e:  # registries are retried lazily from the screens
        print(f"[tui] preload failed: {e}")
    VibraVidApp().run()
