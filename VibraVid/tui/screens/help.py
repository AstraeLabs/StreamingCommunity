# 30.07.26

"""Modal help overlay listing all global & contextual keybindings."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static
from VibraVid.tui.i18n import t

_GLOBAL_KEYS = [
    ("H / Home", "Torna alla schermata iniziale (Home)"),
    ("d", "Apri la schermata Scaricamenti (Downloads)"),
    ("q", "Apri la Coda di download (Queue)"),
    ("h", "Apri la Cronologia (History)"),
    (",", "Apri le Impostazioni (Settings)"),
    ("s", "Apri Diagnostica di Sistema e Log"),
    ("?", "Apri / Chiudi questo menu di Aiuto"),
    ("ESC", "Torna indietro di un livello"),
    ("Ctrl+Q", "Esci dall'applicazione"),
]

_NAV_KEYS = [
    ("← / →", "Spostamento tra colonne, sezioni e filtri"),
    ("↑ / ↓", "Scorrimento elenchi e risultati di ricerca"),
    ("ENTER", "Conferma selezione o avvia ricerca"),
]

_CONTEXT_KEYS = [
    ("a", "Seleziona tutti gli episodi (Dettaglio serie)"),
    ("u", "Deseleziona tutti gli episodi (Dettaglio serie)"),
    ("Ctrl+S", "Salva la sezione corrente (Impostazioni)"),
    ("r", "Ricarica diagnostica e log (Sistema)"),
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
                for key, desc in _GLOBAL_KEYS:
                    yield Static(f"[bold #7dcfff]{key:>10}[/bold #7dcfff]  [#c0caf5]{desc}[/#c0caf5]")

                yield Static(f"\n[bold #7aa2f7]{t('navigation_keyboard')}[/bold #7aa2f7]", classes="help-section-header")
                for key, desc in _NAV_KEYS:
                    yield Static(f"[bold #7dcfff]{key:>10}[/bold #7dcfff]  [#c0caf5]{desc}[/#c0caf5]")

                yield Static(f"\n[bold #7aa2f7]{t('contextual_shortcuts')}[/bold #7aa2f7]", classes="help-section-header")
                for key, desc in _CONTEXT_KEYS:
                    yield Static(f"[bold #7dcfff]{key:>10}[/bold #7dcfff]  [#c0caf5]{desc}[/#c0caf5]")

            yield Static(f"\n{t('press_esc_to_close')}", classes="placeholder-hint")

