# 29.07.26

"""Search screen: query input + category filters + mouse-hover live metadata preview card."""

import logging
from typing import Dict, List, Optional, Tuple

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, LoadingIndicator, ListView, Static

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
    """Runs catalog search and displays results alongside mouse-hover live metadata preview card."""

    def __init__(self, site: Optional[str], initial_query: str = "") -> None:
        super().__init__()
        self._site = site
        self._initial_query = initial_query
        self._raw: List[Tuple[str, object]] = []
        self._current_filter_category: str = "all"
        self._highlighted_payload: Optional[Tuple[str, object]] = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="search-panel"):
            title = f"Search on {self._site}" if self._site else "Global search across all sites"
            yield Static(title, classes="panel-title")
            with Horizontal(id="search-inputs-bar"):
                yield Input(placeholder="Type title and press ENTER...", id="query", value=self._initial_query)
                yield Input(placeholder="Year (e.g. 2021 or 1990-2015)", id="year")

            with Horizontal(id="category-filter-pills"):
                yield Button("All (0)", id="filter-all", variant="primary", classes="filter-pill")
                yield Button("🎬 Film", id="filter-film", classes="filter-pill")
                yield Button("📺 Serie / Anime", id="filter-serie", classes="filter-pill")
                yield Button("🎵 Music", id="filter-music", classes="filter-pill")

            yield LoadingIndicator(id="search-loading")
            yield Static("", id="search-status")
            with Horizontal(id="search-split"):
                with Vertical(id="results-container"):
                    yield FuzzyList(placeholder="Filter results...", id="results")
                with Vertical(id="preview-container"):
                    with Vertical(id="preview-box"):
                        yield Static(
                            "Hover or click a search result to view details and metadata preview.",
                            id="search-preview-box",
                        )
                        with Horizontal(id="preview-actions-row"):
                            yield Button("Download Now", id="preview-open-btn", variant="primary")
                            yield Button("+ Add to Queue", id="preview-queue-btn")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#search-loading", LoadingIndicator).display = False
        self.query_one("#preview-actions-row", Horizontal).display = False
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
        self.query_one("#search-status", Static).update(f"[bold red]✖ Search failed:[/] {message}")

    def _apply_results(self, results: List[Tuple[str, object]], errors: Dict[str, str], year_spec: Optional[Tuple[int, int]]) -> None:
        self._set_loading(False)
        if year_spec:
            lo, hi = year_spec
            results = [r for r in results if (y := _item_year(r[1])) is not None and lo <= y <= hi]

        self._raw = results
        self._update_category_counts(results)
        self._populate_results_list()

        status = f"[bold green]✔ {len(results)}[/bold green] result(s) found"
        if errors:
            failed_names = ", ".join(sorted(errors))
            status += f"  ·  [bold yellow]⚠️ {len(errors)} site(s) timed out:[/] [dim]{failed_names}[/dim]"
        elif not results:
            status = "[bold yellow]No results found for query.[/bold yellow]"

        self.query_one("#search-status", Static).update(status)

    def _update_category_counts(self, results: List[Tuple[str, object]]) -> None:
        total = len(results)
        films = sum(1 for _, it in results if getattr(it, "is_movie", False))
        series = sum(1 for _, it in results if not getattr(it, "is_movie", False) and not getattr(it, "is_song", False))
        music = sum(1 for _, it in results if getattr(it, "is_song", False))

        self.query_one("#filter-all", Button).label = f"All ({total})"
        self.query_one("#filter-film", Button).label = f"🎬 Film ({films})"
        self.query_one("#filter-serie", Button).label = f"📺 Serie ({series})"
        self.query_one("#filter-music", Button).label = f"🎵 Music ({music})"

    def _populate_results_list(self) -> None:
        filtered = []
        for site, it in self._raw:
            is_movie = getattr(it, "is_movie", False)
            is_song = getattr(it, "is_song", False)

            if self._current_filter_category == "film" and not is_movie:
                continue
            if self._current_filter_category == "serie" and (is_movie or is_song):
                continue
            if self._current_filter_category == "music" and not is_song:
                continue

            filtered.append((site, it))

        global_mode = self._site is None
        items = []
        for site, it in filtered:
            name = getattr(it, "name", "?")
            year = getattr(it, "year", "") or ""
            is_movie = getattr(it, "is_movie", False)
            is_song = getattr(it, "is_song", False)

            if is_movie:
                type_tag = "[bold yellow]🎬 FILM[/bold yellow]"
            elif is_song:
                type_tag = "[bold magenta]🎵 MUSIC[/bold magenta]"
            else:
                type_tag = "[bold green]📺 SERIE[/bold green]"

            site_tag = f" [bold cyan][{site}][/bold cyan]" if global_mode else ""
            year_str = f" [dim]({year})[/dim]" if year else ""
            label = f"{site_tag} {type_tag} [bold white]{name}[/bold white]{year_str}"
            items.append(FuzzyItem(key=f"{site}:{getattr(it, 'id', name)}", label=label, payload=(site, it)))

        self.query_one("#results", FuzzyList).set_items(items)
        self.query_one("#results", FuzzyList).focus()

    # ── Category Filter Pills handlers ────────────────────────────────────

    @on(Button.Pressed, ".filter-pill")
    def _on_filter_pill_pressed(self, event: Button.Pressed) -> None:
        for btn_id in ("#filter-all", "#filter-film", "#filter-serie", "#filter-music"):
            btn = self.query_one(btn_id, Button)
            btn.variant = "default"

        event.button.variant = "primary"
        if event.button.id == "filter-film":
            self._current_filter_category = "film"
        elif event.button.id == "filter-serie":
            self._current_filter_category = "serie"
        elif event.button.id == "filter-music":
            self._current_filter_category = "music"
        else:
            self._current_filter_category = "all"

        self._populate_results_list()

    # ── Live Preview Card Rendering (Mouse Hover & Selection) ───────────────

    @on(FuzzyList.Highlighted, "#results")
    def _on_highlighted(self, event: FuzzyList.Highlighted) -> None:
        if not event.item or not event.item.payload:
            return
        site, item = event.item.payload
        self._highlighted_payload = (site, item)
        self._render_preview_card(site, item)

    def _render_preview_card(self, site: str, item: object) -> None:
        self.query_one("#preview-actions-row", Horizontal).display = True
        name = getattr(item, "name", "") or getattr(item, "title", "?")
        year = getattr(item, "year", None)
        typ = getattr(item, "type", "Movie/Serie")
        desc = getattr(item, "desc", "") or getattr(item, "description", "") or ""
        desc = desc.strip()
        slug = getattr(item, "slug", "")

        is_movie = getattr(item, "is_movie", False)
        is_song = getattr(item, "is_song", False)
        is_series = getattr(item, "is_series", not (is_movie or is_song))

        open_btn = self.query_one("#preview-open-btn", Button)

        if is_movie:
            type_header = "🎬  FILM / MOVIE FEATURE"
            badge = "[bold yellow]🎬 FILM[/bold yellow]"
            open_btn.label = "⬇️ Download Movie"
        elif is_song:
            type_header = "🎵  MUSIC TRACK / ALBUM"
            badge = "[bold magenta]🎵 MUSIC[/bold magenta]"
            open_btn.label = "⬇️ Download Track"
        else:
            type_header = "📺  TV SERIES / ANIME"
            badge = "[bold green]📺 SERIE / ANIME[/bold green]"
            open_btn.label = "📺 Select Seasons & Episodes"

        lines = [
            f"[bold cyan]┌──────────────────────────────────────────────┐[/bold cyan]",
            f"[bold cyan]│[/bold cyan] [bold white]{type_header[:44]:<44}[/bold white] [bold cyan]│[/bold cyan]",
            f"[bold cyan]└──────────────────────────────────────────────┘[/bold cyan]",
            "",
            f"{badge}  [bold white]{name}[/bold white]" + (f" [dim]({year})[/dim]" if year else ""),
            f"[dim]Provider:[/] [bold cyan]{site}[/bold cyan]   [dim]Format:[/] [bold white]{typ}[/bold white]",
        ]

        if slug:
            lines.append(f"[dim]Slug ID:[/] {slug}")

        lines.append("")
        lines.append("[bold cyan]Synopsis / Plot:[/bold cyan]")
        if desc:
            lines.append(f"[italic]{desc[:280]}...[/italic]" if len(desc) > 280 else f"[italic]{desc}[/italic]")
        else:
            lines.append("[dim]No description or plot details available for this title.[/dim]")

        preview = self.query_one("#search-preview-box", Static)
        preview.update("\n".join(lines))

    # ── Interactive Actions on Highlighted Item ────────────────────────────

    @on(Button.Pressed, "#preview-open-btn")
    def _on_preview_open(self) -> None:
        if not self._highlighted_payload:
            return
        site, item = self._highlighted_payload
        is_movie = getattr(item, "is_movie", False)
        is_song = getattr(item, "is_song", False)

        if is_movie or is_song:
            self._start_direct_download(site, item)
        else:
            from VibraVid.tui.screens.detail import TitleDetailScreen
            self.app.push_screen(TitleDetailScreen(site, item))

    @work(thread=True, exclusive=True, group="download")
    def _start_direct_download(self, site: str, item: object) -> None:
        from VibraVid.core.ui.tracker import context_tracker
        context_tracker.is_gui = True
        try:
            success = bridge.start_download(site, item, season=None, episodes=None)
            if success:
                self.app.call_from_thread(self.app.notify, f"Started download for '{getattr(item, 'name', 'item')}'", severity="information")
            else:
                self.app.call_from_thread(self.app.notify, "Download failed to start", severity="error")
        except Exception as e:
            logger.exception("download error")
            self.app.call_from_thread(self.app.notify, f"Download error: {e}", severity="error")
        finally:
            context_tracker.is_gui = False

    @on(Button.Pressed, "#preview-queue-btn")
    def _on_preview_queue(self) -> None:
        if not self._highlighted_payload:
            return
        site, item = self._highlighted_payload
        import uuid
        from VibraVid.cli.command.equivalent_command import EquivalentCommandBuilder
        from VibraVid.cli.command.queue import (
            _PROCESS_TAG,
            _QueueLock,
            _load_queue,
            _now_iso,
            _queue_path,
            _save_queue,
        )

        search_term = str(getattr(item, "name", "") or getattr(item, "title", "") or "")
        builder = EquivalentCommandBuilder(excluded_dests=[])
        argv = builder.build_argv_from_params(site=site, search=search_term, item="1")

        if not argv:
            self.app.notify("Could not build equivalent command.", severity="error")
            return

        tag = _PROCESS_TAG
        path = _queue_path(tag)
        job_item = {
            "id": uuid.uuid4().hex[:8],
            "argv": argv,
            "status": "pending",
            "tag": tag,
            "enqueued_at": _now_iso(),
            "started_at": None,
            "finished_at": None,
            "returncode": None,
            "attempts": 0,
        }

        try:
            with _QueueLock(path):
                data = _load_queue(path)
                data.setdefault("items", []).append(job_item)
                _save_queue(path, data)
            self.app.notify(f"Added '{search_term[:20]}' to queue ({job_item['id']})", severity="information")
        except Exception as e:
            self.app.notify(f"Queue error: {e}", severity="error")

    @on(FuzzyList.Chosen, "#results")
    def _on_chosen(self, event: FuzzyList.Chosen) -> None:
        site, item = event.item.payload
        self._highlighted_payload = (site, item)
        self._render_preview_card(site, item)

        is_movie = getattr(item, "is_movie", False)
        is_song = getattr(item, "is_song", False)

        if is_movie or is_song:
            # Directly trigger download for movie/song on click!
            self._start_direct_download(site, item)
        else:
            # Open episode selector for series
            from VibraVid.tui.screens.detail import TitleDetailScreen
            self.app.push_screen(TitleDetailScreen(site, item))
