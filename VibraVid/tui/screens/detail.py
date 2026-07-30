# 29.07.26

"""Title detail screen: metadata, season/episode multi-select, DSL preview.

Read-only in M1: the Download/Queue actions are wired in M2/M3. The DSL
preview shows the exact `selections` dict the download path will receive —
the same strings the CLI DSL produces ("1-3", "*", "1,2,5").
"""

import logging
from typing import Dict, List, Optional, Set

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, ListItem, ListView, LoadingIndicator, SelectionList, Static
from textual.widgets._selection_list import Selection

from VibraVid.tui import bridge

logger = logging.getLogger(__name__)


def compact_ranges(numbers: Set[int], total: int) -> str:
    """Compact a set of ints into the CLI DSL: [1,2,3,5] -> '1-3,5'; all -> '*'."""
    if not numbers:
        return ""
    ordered = sorted(set(numbers))
    if len(ordered) >= total > 0:
        return "*"

    parts = []
    start = prev = ordered[0]
    for n in ordered[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    parts.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(parts)


class TitleDetailScreen(Screen):
    """Shows one catalog item; for series lets the user pick episodes."""

    def __init__(self, site: str, item) -> None:
        super().__init__()
        self._site = site
        self._item = item
        self._seasons: List = []
        self._current_season: Optional[int] = None
        self._episode_selections: Dict[int, Set[int]] = {}

    # ── Layout ────────────────────────────────────────────────────────────

    @property
    def _is_single(self) -> bool:
        return bool(getattr(self._item, "is_movie", False) or getattr(self._item, "is_song", False))

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="detail-panel"):
            yield Static(str(getattr(self._item, "name", "?")), classes="detail-title")
            yield Static(self._meta_line(), classes="detail-meta")
            yield Static(self._desc_line(), classes="detail-meta")
            with Vertical(id="series-area"):
                yield Static("Seasons & episodes", classes="panel-title")
                yield LoadingIndicator(id="seasons-loading")
                with Horizontal(id="series-browser"):
                    with Vertical(id="seasons-box"):
                        yield ListView(id="seasons")
                    with Vertical(id="episodes-box"):
                        yield SelectionList(id="episodes")
                yield Static("", id="dsl-preview", classes="dsl-preview")
            with Horizontal(id="actions-row"):
                yield Button("Download", id="dl")
                yield Button("Queue", id="queue")
        yield Footer()

    def _meta_line(self) -> str:
        parts = [
            f"type: {getattr(self._item, 'type', '?')}",
            f"site: {self._site}",
        ]
        year = getattr(self._item, "year", None)
        if year:
            parts.append(f"year: {year}")
        slug = getattr(self._item, "slug", None)
        if slug:
            parts.append(f"slug: {slug}")
        return "  ·  ".join(parts)

    def _desc_line(self) -> str:
        desc = str(getattr(self._item, "desc", "") or "").strip()
        return desc[:300] + ("..." if len(desc) > 300 else "") if desc else ""

    def on_mount(self) -> None:
        if self._is_single:
            self.query_one("#series-area", Vertical).display = False
            self.query_one("#dsl-preview", Static).update("selections = None (single item)")
            return
        self._load_seasons()

    # ── Seasons loading (worker) ──────────────────────────────────────────

    @work(thread=True, exclusive=True, group="seasons")
    def _load_seasons(self) -> None:
        try:
            seasons = bridge.get_seasons(self._site, self._item)
        except Exception as e:
            logger.exception("season loading failed")
            self.app.call_from_thread(self._seasons_failed, str(e))
            return
        self.app.call_from_thread(self._apply_seasons, seasons or [])

    def _seasons_failed(self, message: str) -> None:
        self.query_one("#seasons-loading", LoadingIndicator).display = False
        self.query_one("#dsl-preview", Static).update(f"[red]Could not load seasons: {message}")

    def _apply_seasons(self, seasons: List) -> None:
        self.query_one("#seasons-loading", LoadingIndicator).display = False
        self._seasons = list(seasons)
        if not self._seasons:
            self.query_one("#dsl-preview", Static).update("No season data available for this title.")
            return

        season_list = self.query_one("#seasons", ListView)
        for season in self._seasons:
            count = len(getattr(season, "episodes", []) or [])
            label = f"S{season.number}  ·  {count} ep"
            item = ListItem(Static(label), id=f"season-{season.number}")
            item.season_payload = season
            season_list.append(item)
        season_list.index = 0
        self._show_season(self._seasons[0])

    def _show_season(self, season) -> None:
        self._current_season = season.number
        selected = self._episode_selections.get(season.number, set())
        episodes: SelectionList = self.query_one("#episodes", SelectionList)
        episodes.clear_options()
        for ep in getattr(season, "episodes", []) or []:
            label = f"E{ep.number}  ·  {getattr(ep, 'name', '') or 'Episode'}"
            episodes.add_option(Selection(label, ep.number, initial_state=ep.number in selected))
        self._update_dsl()

    @on(ListView.Highlighted, "#seasons")
    def _on_season_highlighted(self, event: ListView.Highlighted) -> None:
        season = getattr(event.item, "season_payload", None) if event.item else None
        if season is not None and season.number != self._current_season:
            self._show_season(season)

    @on(SelectionList.SelectedChanged, "#episodes")
    def _on_episodes_changed(self) -> None:
        if self._current_season is None:
            return
        selected = set(self.query_one("#episodes", SelectionList).selected)
        self._episode_selections[self._current_season] = selected
        self._update_dsl()

    # ── DSL preview ───────────────────────────────────────────────────────

    def _update_dsl(self) -> None:
        picked = {s: eps for s, eps in self._episode_selections.items() if eps}
        if not picked:
            self.query_one("#dsl-preview", Static).update(
                "SPACE to select episodes — selections preview will appear here"
            )
            return

        if len(picked) == 1:
            season, eps = next(iter(picked.items()))
            total = self._season_episode_count(season)
            text = f"selections = {{'season': '{season}', 'episode': '{compact_ranges(eps, total)}'}}"
        else:
            seasons = compact_ranges(set(picked), len(self._seasons))
            all_full = all(len(eps) >= self._season_episode_count(s) > 0 for s, eps in picked.items())
            episode = "*" if all_full else "<per-season>"
            text = f"selections = {{'season': '{seasons}', 'episode': '{episode}'}}"
        self.query_one("#dsl-preview", Static).update(text)

    def _season_episode_count(self, season_number: int) -> int:
        for season in self._seasons:
            if season.number == season_number:
                return len(getattr(season, "episodes", []) or [])
        return 0

    # ── Download action ───────────────────────────────────────────────────

    @on(Button.Pressed, "#dl")
    def _on_download(self) -> None:
        if self._is_single:
            # Movie/song: no selections needed
            self._start_download_worker(None, None)
        else:
            # Series: build selections from episode picks
            picked = {s: eps for s, eps in self._episode_selections.items() if eps}
            if not picked:
                self.app.notify("Select at least one episode first.", severity="warning")
                return
            if len(picked) == 1:
                season, eps = next(iter(picked.items()))
                total = self._season_episode_count(season)
                season_str = str(season)
                episode_str = compact_ranges(eps, total)
            else:
                seasons = compact_ranges(set(picked), len(self._seasons))
                all_full = all(len(eps) >= self._season_episode_count(s) > 0 for s, eps in picked.items())
                season_str = seasons
                episode_str = "*" if all_full else "1-*"  # fallback: all episodes per season
            self._start_download_worker(season_str, episode_str)

    @work(thread=True, exclusive=True, group="download")
    def _start_download_worker(self, season: Optional[str], episodes: Optional[str]) -> None:
        # Set is_gui=True to suppress Rich bars/prints
        from VibraVid.core.ui.tracker import context_tracker
        context_tracker.is_gui = True
        try:
            success = bridge.start_download(self._site, self._item, season=season, episodes=episodes)
            if success:
                self.app.call_from_thread(self.app.notify, "Download started", severity="information")
            else:
                self.app.call_from_thread(self.app.notify, "Download failed to start", severity="error")
        except Exception as e:
            logger.exception("download failed")
            self.app.call_from_thread(self.app.notify, f"Download error: {e}", severity="error")
        finally:
            context_tracker.is_gui = False

    # ── Queue action ──────────────────────────────────────────────────────

    @on(Button.Pressed, "#queue")
    def _on_queue(self) -> None:
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

        search_term = str(getattr(self._item, "name", "") or getattr(self._item, "title", "") or "")
        season_str = None
        episode_str = None

        if not self._is_single:
            picked = {s: eps for s, eps in self._episode_selections.items() if eps}
            if not picked:
                self.app.notify("Select at least one episode first to queue.", severity="warning")
                return
            if len(picked) == 1:
                season, eps = next(iter(picked.items()))
                total = self._season_episode_count(season)
                season_str = str(season)
                episode_str = compact_ranges(eps, total)
            else:
                seasons = compact_ranges(set(picked), len(self._seasons))
                all_full = all(len(eps) >= self._season_episode_count(s) > 0 for s, eps in picked.items())
                season_str = seasons
                episode_str = "*" if all_full else "1-*"

        builder = EquivalentCommandBuilder(excluded_dests=[])
        argv = builder.build_argv_from_params(
            site=self._site,
            search=search_term,
            item="1",
            season=season_str,
            episode=episode_str,
        )

        if not argv:
            self.app.notify("Could not build equivalent command for queue.", severity="error")
            return

        tag = _PROCESS_TAG
        path = _queue_path(tag)
        item = {
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
                data.setdefault("items", []).append(item)
                _save_queue(path, data)
            self.app.notify(f"Added item {item['id']} to queue ({search_term[:25]})", severity="information")
        except Exception as e:
            logger.exception("Failed to enqueue item")
            self.app.notify(f"Queue error: {e}", severity="error")

