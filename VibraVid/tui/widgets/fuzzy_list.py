# 29.07.26

"""Filter-as-you-type list: an Input over a ListView of labelled items with mouse hover & directional events."""

import logging
from difflib import SequenceMatcher
from typing import Any, List, NamedTuple

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, ListItem, ListView, Static

logger = logging.getLogger(__name__)


class FuzzyItem(NamedTuple):
    key: str
    label: str
    payload: Any = None


class HoverListItem(ListItem):
    """ListItem container for FuzzyList items."""
    pass


class FuzzyList(Widget):
    """Input filter + ListView; reorders and filters items while typing."""

    class Chosen(Message):
        """Posted when the user confirms a list entry with ENTER or CLICK."""

        def __init__(self, item: FuzzyItem, control) -> None:
            super().__init__()
            self.item = item
            self._control_widget = control

        @property
        def control(self):
            return self._control_widget

    class Highlighted(Message):
        """Posted when a list entry is highlighted via mouse hover or arrow keys."""

        def __init__(self, item: FuzzyItem, control) -> None:
            super().__init__()
            self.item = item
            self._control_widget = control

        @property
        def control(self):
            return self._control_widget

    BINDINGS = [Binding("down", "focus_list", show=False)]

    def __init__(self, placeholder: str = "Filter...", **kwargs) -> None:
        super().__init__(**kwargs)
        self._items: List[FuzzyItem] = []
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        yield Input(placeholder=self._placeholder, id="fuzzy-input")
        yield ListView(id="fuzzy-list")

    # ── Public API ────────────────────────────────────────────────────────

    def set_items(self, items: List[FuzzyItem]) -> None:
        self._items = list(items)
        self.query_one("#fuzzy-input", Input).value = ""
        self.call_after_refresh(self._refresh_list, self._items)

    def focus_input(self) -> None:
        self.query_one("#fuzzy-input", Input).focus()

    def action_focus_list(self) -> None:
        self.query_one("#fuzzy-list", ListView).focus()

    # ── Internals ─────────────────────────────────────────────────────────

    async def _refresh_list(self, items: List[FuzzyItem]) -> None:
        lv = self.query_one("#fuzzy-list", ListView)
        await lv.clear()
        for it in items:
            li = HoverListItem(Static(it.label))
            li.fuzzy_payload = it
            lv.append(li)
        if len(lv) > 0:
            lv.index = 0
            first = getattr(lv.children[0], "fuzzy_payload", None)
            if first:
                self.post_message(self.Highlighted(first, self))

    @on(Input.Changed, "#fuzzy-input")
    async def _filter(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        if not query:
            await self._refresh_list(self._items)
            return

        scored = []
        for it in self._items:
            label = it.label.lower()
            if query in label:
                score = 1.0 + len(query) / max(len(label), 1)
            else:
                score = SequenceMatcher(None, query, label).ratio()
            if score > 0.35:
                scored.append((score, it))
        scored.sort(key=lambda x: x[0], reverse=True)
        await self._refresh_list([it for _, it in scored])

    @on(ListView.Selected, "#fuzzy-list")
    def _chosen(self, event: ListView.Selected) -> None:
        item = getattr(event.item, "fuzzy_payload", None)
        if item is not None:
            self.post_message(self.Chosen(item, self))

    @on(ListView.Highlighted, "#fuzzy-list")
    def _highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is not None:
            item = getattr(event.item, "fuzzy_payload", None)
            if item is not None:
                self.post_message(self.Highlighted(item, self))

    @on(events.Click, "ListItem")
    def _on_item_click(self, event: events.Click) -> None:
        self.action_focus_list()

