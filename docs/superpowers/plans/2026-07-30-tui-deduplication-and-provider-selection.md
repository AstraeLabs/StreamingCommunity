# TUI Search Deduplication and Multi-Provider Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Riorganizzare la schermata risultati e la schermata di dettaglio nella TUI di VibraVid: deduplicare i titoli della ricerca aggregando i provider e introdurre il selettore dei provider a sinistra delle stagioni nella schermata dettaglio.

**Architecture:** Nella `SearchScreen` (`VibraVid/tui/screens/search.py`), i risultati restituiti dai vari provider vengono raggruppati per titolo univoco (normalizzato) mantenendo l'elenco dei provider disponibili per ciascun titolo. Nella `TitleDetailScreen` (`VibraVid/tui/screens/detail.py`), il layout `series-browser` ospiterà a sinistra un `ListView(id="providers")` che elenca i provider disponibili. La selezione del provider aggiornerà al volo il caricamento delle stagioni ed episodi relativi a quel provider senza dover uscire dalla schermata.

**Tech Stack:** Python 3.10+, Textual (TUI framework), Pytest.

---

### Task 1: Deduplicazione Risultati di Ricerca nella SearchScreen

**Files:**
- Modify: `VibraVid/tui/screens/search.py:163-220`
- Test: `Test/tui/test_search_dedup.py`

- [ ] **Step 1: Scrivere il test per la deduplicazione dei risultati di ricerca**

Crea `Test/tui/test_search_dedup.py`:

```python
import pytest
from unittest.mock import MagicMock
from VibraVid.tui.screens.search import deduplicate_search_results

class MockItem:
    def __init__(self, name: str, year: int = 2021, is_movie: bool = False):
        self.name = name
        self.year = year
        self.is_movie = is_movie

def test_deduplicate_search_results_combines_providers():
    item1 = MockItem("Breaking Bad", 2008)
    item2 = MockItem("Breaking Bad", 2008)
    
    raw_results = [
        ("animeworld", item1),
        ("streamingcommunity", item2)
    ]
    
    deduped = deduplicate_search_results(raw_results)
    assert len(deduped) == 1
    
    combined_payload = deduped[0]
    site_list, primary_item, all_providers = combined_payload
    assert len(all_providers) == 2
    assert "animeworld" in all_providers
    assert "streamingcommunity" in all_providers
```

- [ ] **Step 2: Eseguire il test per verificare che fallisca**

Esegui: `pytest Test/tui/test_search_dedup.py -v`
Atteso: FAIL con `ImportError: cannot import name 'deduplicate_search_results'`

- [ ] **Step 3: Implementare `deduplicate_search_results` in `search.py`**

In `VibraVid/tui/screens/search.py`, aggiungi la funzione helper di deduplicazione ed aggiorna `_apply_results` e `_populate_results_list`:

```python
def deduplicate_search_results(results: List[Tuple[str, object]]) -> List[Tuple[str, object, List[Tuple[str, object]]]]:
    """Group search results by normalized (name, year, is_movie/is_song) to collapse duplicate titles across providers."""
    groups: Dict[Tuple[str, Optional[int], bool, bool], List[Tuple[str, object]]] = {}
    for site, item in results:
        name = str(getattr(item, "name", "")).strip().lower()
        year = _item_year(item)
        is_movie = bool(getattr(item, "is_movie", False))
        is_song = bool(getattr(item, "is_song", False))
        key = (name, year, is_movie, is_song)
        if key not in groups:
            groups[key] = []
        groups[key].append((site, item))

    deduped = []
    for key, items in groups.items():
        primary_site, primary_item = items[0]
        providers = [(s, it) for s, it in items]
        deduped.append((primary_site, primary_item, providers))
    return deduped
```

- [ ] **Step 4: Eseguire il test per verificare che passi**

Esegui: `pytest Test/tui/test_search_dedup.py -v`
Atteso: PASS

- [ ] **Step 5: Integrazione della deduplicazione in `SearchScreen`**

Aggiorna `_apply_results` per formattare i titoli raggruppati (es. mostrare il numero di provider sul `FuzzyItem`) e passare la lista dei provider selezionati a `TitleDetailScreen`.

- [ ] **Step 6: Eseguire la suite di test TUI per la ricerca**

Esegui: `pytest Test/tui -v`
Atteso: PASS

- [ ] **Step 7: Commit delle modifiche alla ricerca**

```bash
git add VibraVid/tui/screens/search.py Test/tui/test_search_dedup.py
git commit -m "feat(tui): deduplicate search results by title across multiple providers"
```

---

### Task 2: Selettore dei Provider a Sinistra delle Stagioni nella TitleDetailScreen

**Files:**
- Modify: `VibraVid/tui/screens/detail.py:50-200`
- Modify: `VibraVid/tui/theme.tcss`
- Test: `Test/tui/test_detail_providers.py`

- [ ] **Step 1: Scrivere il test per la gestione multi-provider nella TitleDetailScreen**

Crea `Test/tui/test_detail_providers.py`:

```python
import pytest
from VibraVid.tui.screens.detail import TitleDetailScreen

class MockItem:
    def __init__(self, name: str):
        self.name = name
        self.is_movie = False

def test_detail_screen_multi_provider_init():
    item1 = MockItem("Game of Thrones")
    item2 = MockItem("Game of Thrones")
    providers = [("vidsrc", item1), ("streamingcommunity", item2)]
    
    screen = TitleDetailScreen(site="vidsrc", item=item1, providers=providers)
    assert len(screen._providers) == 2
    assert screen._current_site == "vidsrc"
```

- [ ] **Step 2: Eseguire il test per verificare che passi/fallisca secondo la firma di init**

Esegui: `pytest Test/tui/test_detail_providers.py -v`

- [ ] **Step 3: Aggiornare `TitleDetailScreen` in `detail.py`**

Modifica `compose()` per includere `ListView(id="providers")` a sinistra di `#seasons-box` all'interno di `#series-browser`:

```python
with Horizontal(id="series-browser"):
    with Vertical(id="providers-box"):
        yield Static("Provider", classes="box-title")
        yield ListView(id="providers")
    with Vertical(id="seasons-box"):
        yield Static("Stagioni", classes="box-title")
        yield ListView(id="seasons")
    with Vertical(id="episodes-box"):
        yield Static("Episodi", classes="box-title")
        yield SelectionList(id="episodes")
```

Aggiungi il gestore eventi per la selezione del provider:

```python
@on(ListView.Selected, "#providers")
def _on_provider_selected(self, event: ListView.Selected) -> None:
    item = getattr(event.item, "payload", None)
    if item:
        site, site_item = item
        self._current_site = site
        self._item = site_item
        self._load_seasons()
```

- [ ] **Step 4: Aggiornare gli stili in `theme.tcss`**

Aggiungi le regole CSS per `#providers-box` per dividere l'area orizzontale `#series-browser` in 3 colonne bilanciate:

```tcss
#series-browser {
    height: 1fr;
    width: 100%;
}

#providers-box {
    width: 25%;
    border-right: vblank $accent;
}

#seasons-box {
    width: 25%;
    border-right: vblank $accent;
}

#episodes-box {
    width: 50%;
}
```

- [ ] **Step 5: Eseguire la suite di test completa**

Esegui: `pytest Test/tui -v`
Atteso: PASS

- [ ] **Step 6: Commit finale**

```bash
git add VibraVid/tui/screens/detail.py VibraVid/tui/theme.tcss Test/tui/test_detail_providers.py
git commit -m "feat(tui): add multi-provider selection sidebar in title detail screen"
```
