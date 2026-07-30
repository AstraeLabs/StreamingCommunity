# 29.07.26

"""Modal help overlay listing the global keybindings."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

_HELP = [
    ("ESC", "Go back one screen"),
    ("d", "Downloads"),
    ("q", "Queue"),
    ("h", "History"),
    (",", "Settings"),
    ("?", "Toggle this help"),
    ("Ctrl+P", "Command palette"),
    ("Ctrl+Q", "Quit"),
]


class HelpScreen(ModalScreen):
    # ModalScreen blocks the App's own bindings, so ESC/? must be bound here.
    BINDINGS = [
        Binding("escape", "close_help", "Close"),
        Binding("question_mark", "close_help", "Close"),
    ]

    def action_close_help(self) -> None:
        self.dismiss()

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static("Keybindings", classes="placeholder-title")
            for key, desc in _HELP:
                yield Static(f"[b cyan]{key:>8}[/]  {desc}")
            yield Static("\nESC to close", classes="placeholder-hint")
