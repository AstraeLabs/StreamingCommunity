# 29.07.26

"""Search screen: query input + fuzzy-filterable results with directional navigation."""

import logging
from typing import Dict, List, Optional, Tuple

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, LoadingIndicator, ListView, Static

from VibraVid.tui import bridge
from VibraVid.tui.widgets.fuzzy_list import FuzzyItem, FuzzyList

logger = logging.getLogger(__name__)


def _parse_year_filter(spec: str) -> Optional[Tuple[int, int]]:
    """Parse '2020' or '1990-2015' into (min, max); None if empty/invalid."""
    spec = (spec or "").strip()
    if not spec:
        return None
    parts = spec.split("-")
    try:
        if len(parts) == 1:
            year = int(parts[0])
            return (year, year)
        return (int(parts[0]), int(parts[1]))
    except ValueError:
        return None


def _item_year(item) -> Optional[int]:
    try:
        return int(str(getattr(item, "year", "")).split("-")[0].strip())
    except (ValueError, TypeError):
        return None


class SearchScreen(Screen):
    """Runs a catalog search in a worker and shows filterable results."""

    def __init__(self, site: Optional[str], initial_query: str = "") -> None:
        super().__init__()
        self._site = site
        self._initial_query = initial_query
        self._raw: List[Tuple[str, object]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="search-panel"):
            title = f"Search on {self._site}" if self._site else "Global search"
            yield Static(title, classes="panel-title")
            yield Input(placeholder="Type title and press ENTER (or Right -> Results)", id="query", value=self._initial_query)
            yield Input(placeholder="Year filter, e.g. 2021 or 1990-2015 (optional)", id="year")
            yield LoadingIndicator(id="search-loading")
            yield Static("", id="search-status")
            yield FuzzyList(placeholder="Filter results...", id="results")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#search-loading", LoadingIndicator).display = False
        self.query_one("#query", Input).focus()
        if self._initial_query:
            self._start_search()

    # ── Directional Navigation (Left / Right) ──────────────────────────────

    def action_nav_left(self) -> None:
        """Left Arrow: move focus up to inputs or go back."""
        query_input = self.query_one("#query", Input)
        year_input = self.query_one("#year", Input)
        results = self.query_one("#results", FuzzyList)

        if self.focused == results or (self.focused and results.contains_widget(self.focused)):
            query_input.focus()
        elif self.focused == year_input:
            query_input.focus()
        else:
            self.app.pop_screen()

    def action_nav_right(self) -> None:
        """Right Arrow: move focus down to results or select result."""
        query_input = self.query_one("#query", Input)
        results = self.query_one("#results", FuzzyList)

        if self.focused == query_input:
            results.focus()
        else:
            fuzzy_list = results.query_one("#fuzzy-list", ListView)
            if fuzzy_list.highlighted_child:
                fuzzy_list.action_select_cursor()

    # ── Search orchestration ──────────────────────────────────────────────

    @on(Input.Submitted, "#query")
    @on(Input.Submitted, "#year")
    def _on_submit(self) -> None:
        self._start_search()

    def _start_search(self) -> None:
        query = self.query_one("#query", Input).value.strip()
        if not query:
            self.app.notify("Type something to search first.", severity="warning")
            return
        year_spec = _parse_year_filter(self.query_one("#year", Input).value)
        self._set_loading(True)
        self._search_worker(query, year_spec)

    @work(thread=True, exclusive=True, group="search")
    def _search_worker(self, query: str, year_spec: Optional[Tuple[int, int]]) -> None:
        try:
            if self._site:
                items = bridge.search_titles(self._site, query)
                results = [(self._site, it) for it in items]
                errors: Dict[str, str] = {}
            else:
                found, errors = bridge.search_global(query)
                results = [(site, it) for site, items in found.items() for it in items]
        except Exception as e:
            logger.exception("search failed")
            self.app.call_from_thread(self._search_failed, str(e))
            return
        self.app.call_from_thread(self._apply_results, results, errors, year_spec)

    def _set_loading(self, active: bool) -> None:
        self.query_one("#search-loading", LoadingIndicator).display = active

    def _search_failed(self, message: str) -> None:
        self._set_loading(False)
        self.query_one("#search-status", Static).update(f"[red]Search failed: {message}")

    def _apply_results(self, results: List[Tuple[str, object]], errors: Dict[str, str], year_spec: Optional[Tuple[int, int]]) -> None:
        self._set_loading(False)
        if year_spec:
            lo, hi = year_spec
            results = [r for r in results if (y := _item_year(r[1])) is not None and lo <= y <= hi]

        self._raw = results
        global_mode = self._site is None
        items = []
        for site, it in results:
            name = getattr(it, "name", "?")
            year = getattr(it, "year", "") or ""
            typ = getattr(it, "type", "") or ""
            site_tag = f"  [{site}]" if global_mode else ""
            label = f"{name} ({year})  [{typ}]{site_tag}" if year else f"{name}  [{typ}]{site_tag}"
            items.append(FuzzyItem(key=f"{site}:{getattr(it, 'id', name)}", label=label, payload=(site, it)))
        self.query_one("#results", FuzzyList).set_items(items)

        status = f"{len(items)} result(s)"
        if errors:
            status += f" — {len(errors)} site(s) failed: {', '.join(sorted(errors))}"
        elif not items:
            status = "No results found"
        self.query_one("#search-status", Static).update(status)
        self.query_one("#results", FuzzyList).focus()

    @on(FuzzyList.Chosen, "#results")
    def _on_chosen(self, event: FuzzyList.Chosen) -> None:
        site, item = event.item.payload
        from VibraVid.tui.screens.detail import TitleDetailScreen
        self.app.push_screen(TitleDetailScreen(site, item))
