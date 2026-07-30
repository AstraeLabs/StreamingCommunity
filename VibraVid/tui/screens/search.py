# 29.07.26

"""Search screen: query input + category filters + mouse-hover live metadata preview card."""

import logging

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Header, Input, LoadingIndicator, Static

from VibraVid.tui import bridge
from VibraVid.tui.i18n import t
from VibraVid.tui.widgets.custom_footer import CustomFooter
from VibraVid.tui.widgets.fuzzy_list import FuzzyItem, FuzzyList

logger = logging.getLogger(__name__)


def _parse_year_filter(spec: str) -> tuple[int, int] | None:
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


def _item_year(item) -> int | None:
    try:
        return int(str(getattr(item, "year", "")).split("-")[0].strip())
    except (ValueError, TypeError):
        return None


def _normalize_title(title: str) -> str:
    import re
    cleaned = re.sub(r"[^\w\s]", "", (title or "").lower())
    return " ".join(cleaned.split())


def deduplicate_search_results(
    results: list[tuple[str, object]]
) -> list[tuple[str, object, list[tuple[str, object]]]]:
    """Group search results by normalized title, year, and category, merging providers."""
    groups: dict[tuple[str, int | None, str], tuple[str, object, list[tuple[str, object]]]] = {}
    ordered_keys = []

    for site, item in results:
        name = getattr(item, "name", "") or getattr(item, "title", "") or ""
        norm_title = _normalize_title(str(name))
        year = _item_year(item)

        is_movie = getattr(item, "is_movie", False)
        is_song = getattr(item, "is_song", False)
        if is_movie:
            cat = "movie"
        elif is_song:
            cat = "music"
        else:
            cat = "serie"

        key = (norm_title, year, cat)
        if key not in groups:
            groups[key] = (site, item, [(site, item)])
            ordered_keys.append(key)
        else:
            primary_site, primary_item, providers = groups[key]
            providers.append((site, item))

    return [groups[k] for k in ordered_keys]


class SearchScreen(Screen):
    """Runs catalog search and displays results alongside mouse-hover live metadata preview card."""

    def __init__(self, site: str | None, initial_query: str = "") -> None:
        super().__init__()
        self._site = site
        self._initial_query = initial_query
        self._raw: list[tuple[str, object, list[tuple[str, object]]]] = []
        self._current_filter_category: str = "all"
        self._highlighted_payload: tuple | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="search-panel"):
            title = t("search_on_site", site=self._site) if self._site else t("global_search_title")
            yield Static(title, classes="panel-title")
            with Horizontal(id="search-inputs-bar"):
                yield Input(placeholder=t("search_input_placeholder"), id="query", value=self._initial_query)
                yield Input(placeholder=t("year_placeholder"), id="year")

            with Horizontal(id="category-filter-pills"):
                yield Button(f"{t('all')} (0)", id="filter-all", variant="primary", classes="filter-pill")
                yield Button(f"🎬 {t('film')}", id="filter-film", classes="filter-pill")
                yield Button(f"📺 {t('serie_anime')}", id="filter-serie", classes="filter-pill")
                yield Button(f"🎵 {t('music')}", id="filter-music", classes="filter-pill")

            yield LoadingIndicator(id="search-loading")
            yield Static("", id="search-status")
            with Horizontal(id="search-split"):
                with Vertical(id="results-container"):
                    yield FuzzyList(placeholder=t("filter_results_placeholder"), id="results")
                with Vertical(id="preview-container"):
                    with Vertical(id="preview-box"):
                        yield Static(
                            t("preview_initial_text"),
                            id="search-preview-box",
                        )
                        with Horizontal(id="preview-actions-row"):
                            yield Button(t("download_now"), id="preview-open-btn", variant="primary")
                            yield Button(t("add_to_queue"), id="preview-queue-btn")
        yield CustomFooter()

    def on_mount(self) -> None:
        self.query_one("#search-loading", LoadingIndicator).display = False
        self.query_one("#preview-actions-row", Horizontal).display = False
        self.query_one("#query", Input).focus()
        if self._initial_query:
            self._start_search()

    # ── Directional Navigation (Left / Right) ──────────────────────────────

    def action_nav_left(self) -> None:
        """Left Arrow: move focus left from preview to results, or up to inputs, or go back."""
        query_input = self.query_one("#query", Input)
        year_input = self.query_one("#year", Input)
        results = self.query_one("#results", FuzzyList)
        open_btn = self.query_one("#preview-open-btn", Button)
        queue_btn = self.query_one("#preview-queue-btn", Button)

        preview_container = self.query_one("#preview-container")
        if self.focused in (open_btn, queue_btn) or (self.focused and self.focused in preview_container.walk_children()):
            results.focus()
        elif self.focused == results or (self.focused and self.focused in results.walk_children()) or self.focused == year_input:
            query_input.focus()
        else:
            self.app.pop_screen()

    def action_nav_right(self) -> None:
        """Right Arrow: move focus right from inputs to results, or from results to preview buttons."""
        query_input = self.query_one("#query", Input)
        year_input = self.query_one("#year", Input)
        results = self.query_one("#results", FuzzyList)
        open_btn = self.query_one("#preview-open-btn", Button)

        if self.focused in (query_input, year_input):
            results.focus()
        elif self.focused == results or (self.focused and self.focused in results.walk_children()):
            if self._highlighted_payload and open_btn.display:
                open_btn.focus()

    # ── Search orchestration ──────────────────────────────────────────────

    @on(Input.Submitted, "#query")
    @on(Input.Submitted, "#year")
    def _on_submit(self) -> None:
        self._start_search()

    def _start_search(self) -> None:
        query = self.query_one("#query", Input).value.strip()
        if not query:
            self.app.notify(t("type_something_warning"), severity="warning")
            return
        year_spec = _parse_year_filter(self.query_one("#year", Input).value)
        self._set_loading(True)
        self._search_worker(query, year_spec)

    @work(thread=True, exclusive=True, group="search")
    def _search_worker(self, query: str, year_spec: tuple[int, int] | None) -> None:
        try:
            if self._site:
                items = bridge.search_titles(self._site, query)
                results = [(self._site, it) for it in items]
                errors: dict[str, str] = {}
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
        self.query_one("#search-status", Static).update(f"[bold red]✖ {t('search_failed')}:[/] {message}")

    def _apply_results(self, results: list[tuple[str, object]], errors: dict[str, str], year_spec: tuple[int, int] | None) -> None:
        self._set_loading(False)
        if year_spec:
            lo, hi = year_spec
            results = [r for r in results if (y := _item_year(r[1])) is not None and lo <= y <= hi]

        deduped = deduplicate_search_results(results)

        self._raw = deduped
        self._update_category_counts(deduped)
        self._populate_results_list()

        status = t("results_found_status", count=len(deduped))
        if errors:
            failed_names = ", ".join(sorted(errors))
            status += t("sites_timed_out_status", count=len(errors), sites=failed_names)
        elif not deduped:
            status = t("no_results_status")

        self.query_one("#search-status", Static).update(status)

    def _update_category_counts(self, results: list[tuple[str, object, list[tuple[str, object]]]]) -> None:
        total = len(results)
        films = sum(1 for _, it, _ in results if getattr(it, "is_movie", False))
        series = sum(1 for _, it, _ in results if not getattr(it, "is_movie", False) and not getattr(it, "is_song", False))
        music = sum(1 for _, it, _ in results if getattr(it, "is_song", False))

        self.query_one("#filter-all", Button).label = f"{t('all')} ({total})"
        self.query_one("#filter-film", Button).label = f"🎬 {t('film')} ({films})"
        self.query_one("#filter-serie", Button).label = f"📺 {t('serie')} ({series})"
        self.query_one("#filter-music", Button).label = f"🎵 {t('music')} ({music})"

    def _populate_results_list(self) -> None:
        filtered = []
        for site, it, providers in self._raw:
            is_movie = getattr(it, "is_movie", False)
            is_song = getattr(it, "is_song", False)

            if self._current_filter_category == "film" and not is_movie:
                continue
            if self._current_filter_category == "serie" and (is_movie or is_song):
                continue
            if self._current_filter_category == "music" and not is_song:
                continue

            filtered.append((site, it, providers))

        global_mode = self._site is None
        items = []
        for site, it, providers in filtered:
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

            provider_names = [p[0] for p in providers]
            if len(provider_names) > 1 or global_mode:
                site_tag = f" [bold cyan][{', '.join(provider_names)}][/bold cyan]"
            else:
                site_tag = ""

            year_str = f" [dim]({year})[/dim]" if year else ""
            label = f"{site_tag} {type_tag} [bold white]{name}[/bold white]{year_str}"
            items.append(FuzzyItem(key=f"{site}:{getattr(it, 'id', name)}", label=label, payload=(site, it, providers)))

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
        payload = event.item.payload
        self._highlighted_payload = payload
        self._render_preview_card(payload)

    def _render_preview_card(self, payload: tuple) -> None:
        self.query_one("#preview-actions-row", Horizontal).display = True
        site, item = payload[0], payload[1]
        providers = payload[2] if len(payload) > 2 else [(site, item)]

        name = getattr(item, "name", "") or getattr(item, "title", "?")
        year = getattr(item, "year", None)
        typ = getattr(item, "type", "Movie/Serie")
        desc = getattr(item, "desc", "") or getattr(item, "description", "") or ""
        desc = desc.strip()
        slug = getattr(item, "slug", "")

        is_movie = getattr(item, "is_movie", False)
        is_song = getattr(item, "is_song", False)

        open_btn = self.query_one("#preview-open-btn", Button)

        if is_movie:
            type_header = t("header_film")
            badge = f"[bold yellow]🎬 {t('film').upper()}[/bold yellow]"
            open_btn.label = f"⬇️ {t('download_movie')}"
        elif is_song:
            type_header = t("header_music")
            badge = f"[bold magenta]🎵 {t('music').upper()}[/bold magenta]"
            open_btn.label = f"⬇️ {t('download_track')}"
        else:
            type_header = t("header_serie")
            badge = f"[bold green]📺 {t('serie_anime').upper()}[/bold green]"
            open_btn.label = f"📺 {t('select_seasons_episodes')}"

        prov_str = ", ".join(p[0] for p in providers)
        lines = [
            "[bold cyan]┌──────────────────────────────────────────────┐[/bold cyan]",
            f"[bold cyan]│[/bold cyan] [bold white]{type_header[:44]:<44}[/bold white] [bold cyan]│[/bold cyan]",
            "[bold cyan]└──────────────────────────────────────────────┘[/bold cyan]",
            "",
            f"{badge}  [bold white]{name}[/bold white]" + (f" [dim]({year})[/dim]" if year else ""),
            f"[dim]{t('label_providers')}[/] [bold cyan]{prov_str}[/bold cyan]   [dim]{t('label_format')}[/] [bold white]{typ}[/bold white]",
        ]

        if slug:
            lines.append(f"[dim]{t('label_slug')}[/] {slug}")

        lines.append("")
        lines.append(f"[bold cyan]{t('synopsis_plot')}:[/bold cyan]")
        if desc:
            lines.append(f"[italic]{desc[:280]}...[/italic]" if len(desc) > 280 else f"[italic]{desc}[/italic]")
        else:
            lines.append(f"[dim]{t('no_description')}[/dim]")

        preview = self.query_one("#search-preview-box", Static)
        preview.update("\n".join(lines))

    # ── Interactive Actions on Highlighted Item ────────────────────────────

    @on(Button.Pressed, "#preview-open-btn")
    def _on_preview_open(self) -> None:
        if not self._highlighted_payload:
            return
        site, item = self._highlighted_payload[0], self._highlighted_payload[1]
        providers = self._highlighted_payload[2] if len(self._highlighted_payload) > 2 else [(site, item)]
        is_movie = getattr(item, "is_movie", False)
        is_song = getattr(item, "is_song", False)

        if is_movie or is_song:
            self._start_direct_download(site, item)
        else:
            from VibraVid.tui.screens.detail import TitleDetailScreen
            self.app.push_screen(TitleDetailScreen(site, item, providers=providers))

    @work(thread=True, exclusive=True, group="download")
    def _start_direct_download(self, site: str, item: object) -> None:
        from VibraVid.core.ui.tracker import context_tracker
        context_tracker.is_gui = True
        try:
            success = bridge.start_download(site, item, season=None, episodes=None)
            if success:
                self.app.call_from_thread(self.app.notify, t("started_download_for", item=getattr(item, "name", "item")), severity="information")
            else:
                self.app.call_from_thread(self.app.notify, t("download_failed_to_start"), severity="error")
        except Exception as e:
            logger.exception("download error")
            self.app.call_from_thread(self.app.notify, t("download_error", error=str(e)), severity="error")
        finally:
            context_tracker.is_gui = False

    @on(Button.Pressed, "#preview-queue-btn")
    def _on_preview_queue(self) -> None:
        if not self._highlighted_payload:
            return
        site, item = self._highlighted_payload[0], self._highlighted_payload[1]
        import uuid

        from VibraVid.cli.command.equivalent_command import EquivalentCommandBuilder
        from VibraVid.cli.command.queue import (
            _PROCESS_TAG,
            _load_queue,
            _now_iso,
            _queue_path,
            _QueueLock,
            _save_queue,
        )

        search_term = str(getattr(item, "name", "") or getattr(item, "title", "") or "")
        builder = EquivalentCommandBuilder(excluded_dests=[])
        argv = builder.build_argv_from_params(site=site, search=search_term, item="1")

        if not argv:
            self.app.notify(t("could_not_build_cmd"), severity="error")
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
            self.app.notify(t("added_to_queue_msg", title=search_term[:20], job_id=job_item['id']), severity="information")
        except Exception as e:
            self.app.notify(t("queue_error", error=str(e)), severity="error")

    @on(FuzzyList.Chosen, "#results")
    def _on_chosen(self, event: FuzzyList.Chosen) -> None:
        """When an item is clicked or selected: update preview & maintain focus on results list."""
        payload = event.item.payload
        self._highlighted_payload = payload
        self._render_preview_card(payload)

        # Re-focus results list so item highlights in blue and arrow keys work immediately
        self.query_one("#results", FuzzyList).action_focus_list()

        # Re-focus results list so item highlights in blue and arrow keys work immediately
        self.query_one("#results", FuzzyList).action_focus_list()

