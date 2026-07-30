# 30.07.26

"""Custom bracketed footer widget displaying keybindings in crisp [key] Label format."""

import logging

from rich.markup import escape
from textual.widgets import Static

logger = logging.getLogger(__name__)


from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Button

logger = logging.getLogger(__name__)


class CustomFooter(Widget):
    """Custom footer displaying interactive shortcut buttons across all TUI screens."""

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
        align: center middle;
    }
    Button.footer-btn {
        min-width: 0;
        height: 1;
        padding: 0 1;
        margin: 0;
        border: none;
        background: transparent;
        color: #7aa2f7;
    }
    Button.footer-btn:hover {
        color: #ffffff;
        background: #2f3549;
        border: none;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(id="custom-footer-bar"):
            yield Button("[H] Home", id="footer-home", classes="footer-btn")
            yield Button("[d] Downloads", id="footer-downloads", classes="footer-btn")
            yield Button("[q] Coda", id="footer-queue", classes="footer-btn")
            yield Button("[h] Storia", id="footer-history", classes="footer-btn")
            yield Button("[,] Settings", id="footer-settings", classes="footer-btn")
            yield Button("[s] Sistema", id="footer-system", classes="footer-btn")
            yield Button("[?] Aiuto", id="footer-help", classes="footer-btn")
            yield Button("[ESC] Indietro", id="footer-back", classes="footer-btn")
            yield Button("[Ctrl+Q] Esci", id="footer-quit", classes="footer-btn")

    def on_mount(self) -> None:
        for btn in self.query(".footer-btn"):
            btn.can_focus = False

    @on(Button.Pressed, ".footer-btn")
    def _on_footer_button(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "footer-home":
            self.app.action_go_home()
        elif btn_id == "footer-downloads":
            self.app.action_open_area("downloads")
        elif btn_id == "footer-queue":
            self.app.action_open_area("queue")
        elif btn_id == "footer-history":
            self.app.action_open_area("history")
        elif btn_id == "footer-settings":
            self.app.action_open_area("settings")
        elif btn_id == "footer-system":
            self.app.action_open_area("system")
        elif btn_id == "footer-help":
            self.app.action_help()
        elif btn_id == "footer-back":
            self.app.action_back()
        elif btn_id == "footer-quit":
            self.app.action_quit()
