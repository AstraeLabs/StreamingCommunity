# 30.07.26

"""Modal help overlay listing all global & contextual keybindings."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from VibraVid.tui.i18n import t

_GLOBAL_KEYS = [
    ("F1 / H", "hk_home"),
    ("F2", "hk_search"),
    ("F3 / d", "hk_downloads"),
    ("F4 / q", "hk_queue"),
    ("F5 / h", "hk_history"),
    ("F6 / ,", "hk_settings"),
    ("F7 / s", "hk_system"),
    ("F8 / F9 / ?", "hk_help"),
    ("ESC", "hk_back"),
    ("Ctrl+Q", "hk_quit"),
]

_NAV_KEYS = [
    ("← / →", "hk_arrow_lr"),
    ("↑ / ↓", "hk_arrow_ud"),
    ("ENTER", "hk_enter"),
]

_CONTEXT_KEYS = [
    ("a", "hk_select_all"),
    ("u", "hk_deselect_all"),
    ("Ctrl+S", "hk_save_section"),
    ("r", "hk_reload_system"),
]


class HelpScreen(ModalScreen):
    """Modal screen displaying organized keybindings and navigation guide."""

    BINDINGS = [
        Binding("escape", "close_help", "Close"),
        Binding("question_mark", "close_help", "Close"),
    ]

    def action_close_help(self) -> None:
        self.dismiss()

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(t("keyboard_help_guide"), classes="placeholder-title")
            with VerticalScroll(id="help-scroll"):
                yield Static(f"[bold #7aa2f7]{t('global_navigation')}[/bold #7aa2f7]", classes="help-section-header")
                for key, desc_key in _GLOBAL_KEYS:
                    yield Static(f"[bold #7dcfff]{key:>10}[/bold #7dcfff]  [#c0caf5]{t(desc_key)}[/#c0caf5]")

                yield Static(f"\n[bold #7aa2f7]{t('navigation_keyboard')}[/bold #7aa2f7]", classes="help-section-header")
                for key, desc_key in _NAV_KEYS:
                    yield Static(f"[bold #7dcfff]{key:>10}[/bold #7dcfff]  [#c0caf5]{t(desc_key)}[/#c0caf5]")

                yield Static(f"\n[bold #7aa2f7]{t('contextual_shortcuts')}[/bold #7aa2f7]", classes="help-section-header")
                for key, desc_key in _CONTEXT_KEYS:
                    yield Static(f"[bold #7dcfff]{key:>10}[/bold #7dcfff]  [#c0caf5]{t(desc_key)}[/#c0caf5]")

            yield Static(f"\n{t('press_esc_to_close')}", classes="placeholder-hint")

