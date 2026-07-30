# TUI Internationalization (i18n) and Localization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introdurre un sistema di internazionalizzazione (i18n) centralizzato con supporto per Italiano (`it`) e Inglese (`en`), autorilevamento della lingua del sistema operativo e uniformare tutti i testi della TUI.

**Architecture:** Creare il modulo `VibraVid/tui/i18n.py` con una funzione `t(key, lang=None)` e i dizionari delle traduzioni (`IT` ed `EN`). Rilevare automaticamente la lingua di sistema tramite `locale` / variabili d'ambiente (`LANG`), e aggiornare le schermate della TUI per consumare le stringhe tramite `t(...)`.

**Tech Stack:** Python 3.10+, Textual, Pytest.

---

### Task 1: Modulo i18n Centralizzato e Test Unitari

**Files:**
- Create: `VibraVid/tui/i18n.py`
- Test: `Test/tui/test_i18n.py`

- [ ] **Step 1: Scrivere il test per il modulo i18n**

Crea `Test/tui/test_i18n.py`:

```python
import os
import pytest
from VibraVid.tui.i18n import t, set_language, get_language

def test_i18n_default_and_fallback():
    set_language("en")
    assert get_language() == "en"
    assert t("seasons") == "Seasons"
    
    set_language("it")
    assert get_language() == "it"
    assert t("seasons") == "Stagioni"

def test_i18n_format_args():
    set_language("it")
    assert t("results_found", count=5) == "✔ 5 risultati trovati"
    set_language("en")
    assert t("results_found", count=5) == "✔ 5 results found"
```

- [ ] **Step 2: Eseguire il test per verificare che fallisca**

Esegui: `PYTHONPATH=. .venv/bin/pytest Test/tui/test_i18n.py -v`
Atteso: FAIL con `ModuleNotFoundError: No module named 'VibraVid.tui.i18n'`

- [ ] **Step 3: Implementare `VibraVid/tui/i18n.py`**

Crea `VibraVid/tui/i18n.py` con il dizionario completo delle chiavi sia in italiano che in inglese e autorilevamento di lingua.

- [ ] **Step 4: Eseguire il test per verificare che passi**

Esegui: `PYTHONPATH=. .venv/bin/pytest Test/tui/test_i18n.py -v`
Atteso: PASS

- [ ] **Step 5: Commit del modulo i18n**

```bash
git add VibraVid/tui/i18n.py Test/tui/test_i18n.py
git commit -m "feat(tui): add centralized i18n module with IT/EN translation catalogs"
```

---

### Task 2: Integrazione i18n in tutte le Schermate della TUI

**Files:**
- Modify: `VibraVid/tui/screens/home.py`
- Modify: `VibraVid/tui/screens/search.py`
- Modify: `VibraVid/tui/screens/detail.py`
- Modify: `VibraVid/tui/screens/downloads.py`
- Modify: `VibraVid/tui/screens/history.py`
- Modify: `VibraVid/tui/screens/queue.py`
- Modify: `VibraVid/tui/screens/settings.py`
- Modify: `VibraVid/tui/screens/system.py`
- Modify: `VibraVid/tui/screens/help.py`
- Modify: `VibraVid/tui/widgets/custom_footer.py`

- [ ] **Step 1: Aggiornare i testi delle schermate TUI per usare `t(...)`**

In ogni file delle schermate, importa `from VibraVid.tui.i18n import t` e sostituisci le stringhe statiche con le relative chiamate `t(...)`.

- [ ] **Step 2: Verificare la suite di test completa**

Esegui: `PYTHONPATH=. .venv/bin/pytest Test/tui -v`
Atteso: PASS (4+ test passati)

- [ ] **Step 3: Commit finale delle modifiche i18n**

```bash
git add VibraVid/tui/screens/ VibraVid/tui/widgets/
git commit -m "feat(tui): localize all TUI screen labels and headings using i18n module"
```
