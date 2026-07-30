# 29.07.26

"""Home screen: category sidebar + site list + ASCII art animated mouse video guide."""

import logging
from typing import Dict, List, Optional

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import ListItem, ListView, Static

from VibraVid.tui.bridge import SiteInfo, sites_by_category
from VibraVid.tui.screens.search import SearchScreen
from VibraVid.tui.widgets.custom_footer import CustomFooter
from VibraVid.tui.widgets.fuzzy_list import FuzzyItem, FuzzyList

logger = logging.getLogger(__name__)

CATEGORY_LABELS = {
    "anime": "Anime",
    "film_serie": "Film & Series",
    "serie": "Series",
    "tor": "Torrent",
    "song": "Music",
}
GLOBAL_ID = "cat-global"

DEMO_FRAMES = [
    """[bold cyan]┌─ Simulazione Mouse & Navigazione ─────────────┐[/bold cyan]
[bold cyan]│[/bold cyan] [bold white]Cerca:[/] 'batman' [bold yellow]🖯[/bold yellow]                        [bold cyan]│[/bold cyan]
[bold cyan]│[/bold cyan]                                               [bold cyan]│[/bold cyan]
[bold cyan]│[/bold cyan]  1. [yellow]🎬 Batman Ninja (2018)[/]  [cyan][AnimeWorld][/cyan]     [bold cyan]│[/bold cyan]
[bold cyan]│[/bold cyan]  2. [green]📺 Batman: Animated[/green]     [cyan][Streaming][/cyan]     [bold cyan]│[/bold cyan]
[bold cyan]└───────────────────────────────────────────────┘[/bold cyan]""",

    """[bold cyan]┌─ Simulazione Mouse & Navigazione ─────────────┐[/bold cyan]
[bold cyan]│[/bold cyan]  ▶ [yellow]🎬 Batman Ninja (2018)[/]    [bold yellow]🖯 (Hover)[/bold yellow]         [bold cyan]│[/bold cyan]
[bold cyan]│[/bold cyan]  Provider: [cyan]AnimeWorld[/cyan]   Format: [white]Movie 1080p[/white]  [bold cyan]│[/bold cyan]
[bold cyan]│[/bold cyan]  [italic]Trama: Il cavaliere oscuro viaggia nel tempo[/italic][bold cyan]│[/bold cyan]
[bold cyan]│[/bold cyan]  [bold cyan]->[/bold cyan] Metadati aggiornati all'istante a destra!   [bold cyan]│[/bold cyan]
[bold cyan]└───────────────────────────────────────────────┘[/bold cyan]""",

    """[bold cyan]┌─ Simulazione Mouse & Navigazione ─────────────┐[/bold cyan]
[bold cyan]│[/bold cyan]  [bold green][ ⬇️ Download Movie ][/bold green]  [bold yellow]🖯 (Click)[/bold yellow]             [bold cyan]│[/bold cyan]
[bold cyan]│[/bold cyan]                                               [bold cyan]│[/bold cyan]
[bold cyan]│[/bold cyan]  ● Batman Ninja  [bold green][████████░░ 80%][/bold green] 14 MB/s     [bold cyan]│[/bold cyan]
[bold cyan]│[/bold cyan]  ✔ [green]Download avviato in background con successo![/green][bold cyan]│[/bold cyan]
[bold cyan]└───────────────────────────────────────────────┘[/bold cyan]""",

    """[bold cyan]┌─ Simulazione Mouse & Navigazione ─────────────┐[/bold cyan]
[bold cyan]│[/bold cyan]  Gestione Coda [bold cyan][q][/bold cyan] & Storico [bold cyan][h][/bold cyan]            [bold cyan]│[/bold cyan]
[bold cyan]│[/bold cyan]  [bold cyan][q][/bold cyan] Coda Batch (1 job in esecuzione)            [bold cyan]│[/bold cyan]
[bold cyan]│[/bold cyan]  [bold cyan][d][/bold cyan] Download Attivi (1 in corso)                 [bold cyan]│[/bold cyan]
[bold cyan]│[/bold cyan]  [bold cyan][H][/bold cyan] [bold yellow]Premi H per tornare subito alla Home![/bold yellow]       [bold cyan]│[/bold cyan]
[bold cyan]└───────────────────────────────────────────────┘[/bold cyan]""",
]


class HomeScreen(Screen):
    """Landing screen with provider catalog, interactive guide & animated ASCII video simulation."""

    def __init__(self) -> None:
        super().__init__()
        self._grouped: Dict[str, List[SiteInfo]] = {}
        self._categories: List[str] = []
        self._demo_frame_idx = 0

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static("Categories", classes="panel-title")
                yield ListView(id="categories")
            with Vertical(id="site-panel"):
                yield Static("Sites", classes="panel-title")
                yield FuzzyList(placeholder="Filter sites... [/]", id="sites")
            with Vertical(id="demo-panel"):
                yield Static("Guida Rapida & Demo", classes="panel-title")
                yield Static(DEMO_FRAMES[0], id="demo-anim-box")
                yield Static(
                    "\n[bold cyan]🕹️ Navigazione Intuitiva:[/bold cyan]\n"
                    " · [bold white]Frecce ← / →[/bold white]: Muoviti tra le colonne o torna indietro\n"
                    " · [bold white]Mouse Hover[/bold white]: Passa sui film per l'anteprima live\n"
                    " · [bold white]Clic[/bold white]: Scarica film o seleziona episodi serie\n\n"
                    "[bold cyan]⚡ Tasti Rapidi Principali:[/bold cyan]\n"
                    " · [bold yellow][H][/bold yellow] Home  ·  [bold yellow][d][/bold yellow] Downloads  ·  [bold yellow][q][/bold yellow] Coda\n"
                    " · [bold yellow][h][/bold yellow] Storia  ·  [bold yellow][,][/bold yellow] Config  ·  [bold yellow][s][/bold yellow] Sistema",
                    id="demo-guide-text",
                )
        yield CustomFooter()

    def on_mount(self) -> None:
        self._grouped = sites_by_category()
        known = [c for c in CATEGORY_LABELS if c in self._grouped]
        extra = sorted(c for c in self._grouped if c not in CATEGORY_LABELS)
        self._categories = known + extra

        cat_list = self.query_one("#categories", ListView)
        for cat in self._categories:
            label = CATEGORY_LABELS.get(cat, cat.capitalize())
            cat_list.append(
                ListItem(Static(label, classes=f"category-label cat-{cat}"), id=f"cat-{cat}")
            )
        cat_list.append(
            ListItem(Static("(global) Global search", classes="category-label cat-global"), id=GLOBAL_ID)
        )
        if self._categories:
            cat_list.index = 0
            self._show_category(self._categories[0])
            cat_list.focus()

        # Start ASCII art mouse animation timer
        self.set_interval(0.9, self._advance_demo_animation)

    def _advance_demo_animation(self) -> None:
        self._demo_frame_idx = (self._demo_frame_idx + 1) % len(DEMO_FRAMES)
        anim_box = self.query_one("#demo-anim-box", Static)
        anim_box.update(DEMO_FRAMES[self._demo_frame_idx])

    def _show_category(self, category: str) -> None:
        items = []
        for site in self._grouped.get(category, []):
            suffix = "" if site.source == "default" else f"  ({site.source})"
            items.append(FuzzyItem(key=site.name, label=f"{site.name.capitalize()}{suffix}", payload=site))
        self.query_one("#sites", FuzzyList).set_items(items)

    # ── Directional navigation ────────────────────────────────────────────

    def action_nav_left(self) -> None:
        """Left arrow: move focus from Sites panel to Categories sidebar."""
        cat_list = self.query_one("#categories", ListView)
        cat_list.focus()

    def action_nav_right(self) -> None:
        """Right arrow: move focus from Categories to Sites, or select site."""
        focused = self.focused
        cat_list = self.query_one("#categories", ListView)

        if focused == cat_list or (focused and self.query_one("#sidebar").contains_widget(focused)):
            sites = self.query_one("#sites", FuzzyList)
            sites.focus()
        else:
            # If on sites, trigger selection of highlighted site
            sites = self.query_one("#sites", FuzzyList)
            fuzzy_list = sites.query_one("#fuzzy-list", ListView)
            if fuzzy_list.highlighted_child:
                fuzzy_list.action_select_cursor()

    @on(ListView.Highlighted, "#categories")
    def _on_category_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is None or event.item.id in (None, GLOBAL_ID):
            return
        self._show_category(event.item.id[len("cat-"):])

    @on(ListView.Selected, "#categories")
    def _on_category_selected(self, event: ListView.Selected) -> None:
        if event.item is not None and event.item.id == GLOBAL_ID:
            self.app.push_screen(SearchScreen(site=None))
        else:
            # Shift focus to sites list upon category selection
            self.query_one("#sites", FuzzyList).focus()

    @on(FuzzyList.Chosen, "#sites")
    def _on_site_chosen(self, event: FuzzyList.Chosen) -> None:
        site: Optional[SiteInfo] = event.item.payload
        if site is not None:
            self.app.push_screen(SearchScreen(site=site.name))
