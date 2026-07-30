# 29.07.26

"""Home screen: category sidebar + site list with directional (left/right) navigation."""

import logging
from typing import Dict, List, Optional

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from VibraVid.tui.bridge import SiteInfo, sites_by_category
from VibraVid.tui.screens.search import SearchScreen
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


class HomeScreen(Screen):
    """Landing screen with the provider catalog."""

    def __init__(self) -> None:
        super().__init__()
        self._grouped: Dict[str, List[SiteInfo]] = {}
        self._categories: List[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Static("Categories", classes="panel-title")
                yield ListView(id="categories")
            with Vertical(id="site-panel"):
                yield Static("Sites", classes="panel-title")
                yield FuzzyList(placeholder="Filter sites... [/]", id="sites")
        yield Footer()

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
