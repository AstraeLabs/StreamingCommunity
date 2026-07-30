# 30.07.26

"""Custom bracketed footer widget displaying keybindings in crisp [key] Label format."""

import logging

from textual.widgets import Static

logger = logging.getLogger(__name__)


class CustomFooter(Static):
    """Custom footer displaying bracketed shortcut labels across all TUI screens."""

    DEFAULT_CSS = """
    CustomFooter {
        dock: bottom;
        height: 1;
        background: #1a1b26;
        color: #c0caf5;
        padding: 0 1;
        border: none;
    }
    """

    def on_mount(self) -> None:
        text = (
            "[bold #7aa2f7][H][/bold #7aa2f7] Home    "
            "[bold #7aa2f7][d][/bold #7aa2f7] Downloads    "
            "[bold #7aa2f7][q][/bold #7aa2f7] Coda    "
            "[bold #7aa2f7][h][/bold #7aa2f7] Storia    "
            "[bold #7aa2f7][,][/bold #7aa2f7] Settings    "
            "[bold #7aa2f7][s][/bold #7aa2f7] Sistema    "
            "[bold #7aa2f7][?][/bold #7aa2f7] Aiuto    "
            "[bold #7aa2f7][ESC][/bold #7aa2f7] Indietro    "
            "[bold #7aa2f7][Ctrl+Q][/bold #7aa2f7] Esci"
        )
        self.update(text)
