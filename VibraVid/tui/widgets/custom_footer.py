# 30.07.26

"""Custom bracketed footer widget displaying interactive shortcut labels."""

import logging

from textual import events, on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Static

from VibraVid.tui.i18n import t

logger = logging.getLogger(__name__)


class CustomFooter(Widget):
    """Custom footer displaying bracketed shortcut labels that highlight on hover and respond to mouse clicks."""

    DEFAULT_CSS = """
    CustomFooter {
        dock: bottom;
        height: 1;
        background: #1f2335;
        color: #c0caf5;
        padding: 0 1;
        border: none;
    }
    #custom-footer-bar {
        height: 1;
        width: 100%;
        align: center middle;
    }
    .foot-item {
        width: auto;
        height: 1;
        padding: 0 1;
        margin: 0;
        color: #7aa2f7;
        background: transparent;
    }
    .foot-item:hover {
        color: #ffffff;
        background: #2f3549;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="custom-footer-bar"):
            yield Static(fr"[bold #7dcfff]\[H][/bold #7dcfff] {t('nav_home')}", id="foot-home", classes="foot-item")
            yield Static(fr"[bold #7dcfff]\[d][/bold #7dcfff] {t('nav_downloads')}", id="foot-downloads", classes="foot-item")
            yield Static(fr"[bold #7dcfff]\[q][/bold #7dcfff] {t('nav_queue')}", id="foot-queue", classes="foot-item")
            yield Static(fr"[bold #7dcfff]\[h][/bold #7dcfff] {t('nav_history')}", id="foot-history", classes="foot-item")
            yield Static(fr"[bold #7dcfff]\[,][/bold #7dcfff] {t('nav_settings')}", id="foot-settings", classes="foot-item")
            yield Static(fr"[bold #7dcfff]\[s][/bold #7dcfff] {t('nav_system')}", id="foot-system", classes="foot-item")
            yield Static(fr"[bold #7dcfff]\[?][/bold #7dcfff] {t('nav_help')}", id="foot-help", classes="foot-item")
            yield Static(fr"[bold #7dcfff]\[ESC][/bold #7dcfff] {t('nav_back')}", id="foot-back", classes="foot-item")
            yield Static(fr"[bold #7dcfff]\[Ctrl+Q][/bold #7dcfff] {t('nav_quit')}", id="foot-quit", classes="foot-item")

    @on(events.Click, ".foot-item")
    def _on_foot_item_click(self, event: events.Click) -> None:
        target_id = event.widget.id
        if target_id == "foot-home":
            self.app.action_go_home()
        elif target_id == "foot-downloads":
            self.app.action_open_area("downloads")
        elif target_id == "foot-queue":
            self.app.action_open_area("queue")
        elif target_id == "foot-history":
            self.app.action_open_area("history")
        elif target_id == "foot-settings":
            self.app.action_open_area("settings")
        elif target_id == "foot-system":
            self.app.action_open_area("system")
        elif target_id == "foot-help":
            self.app.action_help()
        elif target_id == "foot-back":
            self.app.action_back()
        elif target_id == "foot-quit":
            self.app.action_quit()

