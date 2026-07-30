# 30.07.26

"""Custom bracketed footer widget displaying keybindings in crisp [key] Label format."""

import logging

from textual.widgets import Static

logger = logging.getLogger(__name__)


class CustomFooter(Static):
    """Custom footer displaying bracketed shortcut labels across all TUI screens."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.styles.dock = "bottom"
        self.styles.height = 1
        self.styles.background = "#24283b"
        self.styles.color = "#565f89"

    def on_mount(self) -> None:
        text = (
            "[bold cyan][H][/bold cyan] Home  ·  "
            "[bold cyan][d][/bold cyan] Downloads  ·  "
            "[bold cyan][q][/bold cyan] Coda  ·  "
            "[bold cyan][h][/bold cyan] Storia  ·  "
            "[bold cyan][,][/bold cyan] Settings  ·  "
            "[bold cyan][s][/bold cyan] Sistema  ·  "
            "[bold cyan][?][/bold cyan] Aiuto  ·  "
            "[bold cyan][ESC][/bold cyan] Indietro  ·  "
            "[bold cyan][Ctrl+Q][/bold cyan] Esci"
        )
        self.update(text)
