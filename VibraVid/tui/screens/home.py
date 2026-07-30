# 29.07.26

"""Home screen: Search Engine style landing page with central search bar, scope selectors & filtered provider pills."""

import logging
from typing import Dict, List, Optional

from textual import on
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Input, Static

from VibraVid.tui.bridge import SiteInfo, sites_by_category
from VibraVid.tui.screens.search import SearchScreen
from VibraVid.tui.widgets.custom_footer import CustomFooter

logger = logging.getLogger(__name__)

ASCII_LOGO = """[bold cyan]
██╗   ██╗██╗██████╗ ██████╗  █████╗ ██╗   ██╗██╗██████╗ 
██║   ██║██║██╔══██╗██╔══██╗██╔══██╗██║   ██║██║██╔══██╗
██║   ██║██║██████╔╝██████╔╝███████║██║   ██║██║██║  ██║
╚██╗ ██╔╝██║██╔══██╗██╔══██╗██╔══██║╚██╗ ██╔╝██║██║  ██║
 ╚████╔╝ ██║██████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║██████╔╝
[/bold cyan]"""


class HomeScreen(Screen):
    """Search Engine style main screen with central search, category selectors & live-filtered provider pills."""

    def __init__(self) -> None:
        super().__init__()
        self._selected_scope: str = "global"
        self._selected_site: Optional[str] = None
        self._grouped: Dict[str, List[SiteInfo]] = {}
        self._all_sites: List[SiteInfo] = []
        self._searching_lock: bool = False

    def compose(self) -> ComposeResult:
        with Vertical(id="home-outer"):
            with Vertical(id="home-card"):
                yield Static(ASCII_LOGO, id="home-logo")
                yield Static(
                    "[bold #7aa2f7]Motore di ricerca universale per Streaming, Anime, Film, Serie e Musica[/bold #7aa2f7]",
                    id="home-tagline",
                )

                with Horizontal(id="home-search-bar"):
                    yield Input(placeholder="Cerca un titolo (es. Batman, Naruto, One Piece)...", id="main-search-input")
                    yield Button("Cerca 🔍", id="btn-submit-search", variant="primary")

                yield Static("Seleziona Categoria di Ricerca:", classes="home-section-title")
                with Horizontal(id="home-scope-pills"):
                    yield Button("🌐 Global (Tutti)", id="scope-global", variant="primary", classes="scope-pill")
                    yield Button("🌸 Anime", id="scope-anime", classes="scope-pill")
                    yield Button("🎬 Film", id="scope-film", classes="scope-pill")
                    yield Button("📺 Serie TV", id="scope-serie", classes="scope-pill")
                    yield Button("🎵 Musica", id="scope-music", classes="scope-pill")

                with Horizontal(id="home-provider-header"):
                    yield Static("Filtra Provider / Sito Specifico:", classes="home-section-title-left")
                    yield Input(placeholder="Filtra provider... (es. anime, streaming)", id="provider-filter-input")

                with Container(id="home-sites-box"):
                    with Horizontal(id="home-sites-wrap"):
                        yield Button("🌐 Tutti i Provider", id="site-all", variant="primary", classes="site-pill")

        yield CustomFooter()

    def on_mount(self) -> None:
        self._grouped = sites_by_category()
        self._all_sites = []
        added_names = set()
        for cat, site_list in self._grouped.items():
            for site in site_list:
                if site.name not in added_names:
                    added_names.add(site.name)
                    self._all_sites.append(site)

        self._render_provider_pills(self._all_sites)
        self.query_one("#main-search-input", Input).focus()

    def _render_provider_pills(self, sites: List[SiteInfo]) -> None:
        sites_wrap = self.query_one("#home-sites-wrap", Horizontal)
        for child in list(sites_wrap.children):
            child.remove()

        is_all_active = (self._selected_site is None)
        all_variant = "primary" if is_all_active else "default"
        sites_wrap.mount(Button("🌐 Tutti i Provider", id="site-all", variant=all_variant, classes="site-pill"))

        for site in sites:
            sites_wrap.mount(Static(" • ", classes="site-dot"))
            btn_id = f"site-btn-{site.name.replace('_', '-')}"
            label = site.name.capitalize()
            is_active = (self._selected_site == site.name)
            variant = "primary" if is_active else "default"
            sites_wrap.mount(Button(label, id=btn_id, variant=variant, classes="site-pill"))

    @on(Input.Changed, "#provider-filter-input")
    def _on_provider_filter_changed(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        if not query:
            filtered = self._all_sites
        else:
            filtered = [s for s in self._all_sites if query in s.name.lower() or query in s.category.lower()]
        self._render_provider_pills(filtered)

    # ── Scope and Site Pill Click Handlers ─────────────────────────────────

    @on(Button.Pressed, ".scope-pill")
    def _on_scope_pressed(self, event: Button.Pressed) -> None:
        for btn_id in ("#scope-global", "#scope-anime", "#scope-film", "#scope-serie", "#scope-music"):
            btn = self.query_one(btn_id, Button)
            btn.variant = "default"

        event.button.variant = "primary"
        if event.button.id == "scope-anime":
            self._selected_scope = "anime"
        elif event.button.id == "scope-film":
            self._selected_scope = "film"
        elif event.button.id == "scope-serie":
            self._selected_scope = "serie"
        elif event.button.id == "scope-music":
            self._selected_scope = "music"
        else:
            self._selected_scope = "global"

    @on(Button.Pressed, ".site-pill")
    def _on_site_pressed(self, event: Button.Pressed) -> None:
        pills = self.query_all(".site-pill")
        for pill in pills:
            pill.variant = "default"

        event.button.variant = "primary"
        if event.button.id == "site-all":
            self._selected_site = None
        else:
            site_name = event.button.id[len("site-btn-"):].replace("-", "_")
            self._selected_site = site_name

    # ── Search Submission (Crash Guarded) ──────────────────────────────────

    @on(Input.Submitted, "#main-search-input")
    @on(Button.Pressed, "#btn-submit-search")
    def _on_search(self) -> None:
        if self._searching_lock:
            return
        self._searching_lock = True
        try:
            query = self.query_one("#main-search-input", Input).value.strip()
            if not query:
                self.app.notify("Inserisci un titolo prima di cercare!", severity="warning")
                return

            screen = SearchScreen(site=self._selected_site, initial_query=query)
            if self._selected_scope != "global":
                screen._current_filter_category = self._selected_scope
            self.app.push_screen(screen)
        finally:
            self.set_timer(0.6, self._unlock_search)

    def _unlock_search(self) -> None:
        self._searching_lock = False

    # ── Keyboard Directional Navigation ────────────────────────────────────

    def action_nav_left(self) -> None:
        """Left Arrow: move to previous sibling button if focused on pill."""
        focused = self.focused
        if isinstance(focused, Button) and focused.parent:
            children = [c for c in focused.parent.children if isinstance(c, Button)]
            if focused in children:
                idx = children.index(focused)
                if idx > 0:
                    children[idx - 1].focus()

    def action_nav_right(self) -> None:
        """Right Arrow: move to next sibling button if focused on pill."""
        focused = self.focused
        if isinstance(focused, Button) and focused.parent:
            children = [c for c in focused.parent.children if isinstance(c, Button)]
            if focused in children:
                idx = children.index(focused)
                if idx < len(children) - 1:
                    children[idx + 1].focus()
