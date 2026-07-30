import locale
import os
from typing import Optional

CATALOGS = {
    "it": {
        "providers": "Provider",
        "seasons": "Stagioni",
        "episodes": "Episodi",
        "download": "Download",
        "queue": "Coda",
        "search": "Cerca",
        "results_found": "{count} risultati trovati",
        "active_downloads": "Download Attivi",
        "completed_downloads": "Download Completati",
        "settings": "Impostazioni",
        "system_info": "Info Sistema",
        "keyboard_help": "Aiuto Tastiera",
        "categories": "Categorie",
        "select_category": "Seleziona Categoria",
        "filter_provider": "Filtra Provider",
        "back": "Indietro",
        "confirm": "Conferma",
        "cancel": "Annulla",
        "error": "Errore",
        "success": "Successo",
        "loading": "Caricamento...",
        "no_results": "Nessun risultato trovato",
        "details": "Dettagli",
        "play": "Riproduci",
        "stop": "Interrompi",
        "pause": "Pausa",
        "resume": "Riprendi",
    },
    "en": {
        "providers": "Providers",
        "seasons": "Seasons",
        "episodes": "Episodes",
        "download": "Download",
        "queue": "Queue",
        "search": "Search",
        "results_found": "{count} results found",
        "active_downloads": "Active Downloads",
        "completed_downloads": "Completed Downloads",
        "settings": "Settings",
        "system_info": "System Info",
        "keyboard_help": "Keyboard Help",
        "categories": "Categories",
        "select_category": "Select Category",
        "filter_provider": "Filter Provider",
        "back": "Back",
        "confirm": "Confirm",
        "cancel": "Cancel",
        "error": "Error",
        "success": "Success",
        "loading": "Loading...",
        "no_results": "No results found",
        "details": "Details",
        "play": "Play",
        "stop": "Stop",
        "pause": "Pause",
        "resume": "Resume",
    },
}


def detect_system_language() -> str:
    lang = os.environ.get("LC_ALL", "") or os.environ.get("LANG", "")
    if not lang:
        try:
            loc = locale.getlocale()
            if loc and loc[0]:
                lang = loc[0]
        except Exception:
            pass

    if not lang:
        try:
            loc_def = locale.getdefaultlocale()
            if loc_def and loc_def[0]:
                lang = loc_def[0]
        except Exception:
            pass

    if lang and lang.lower().startswith("it"):
        return "it"
    return "en"


_CURRENT_LANG: str = detect_system_language()


def set_language(lang: str) -> None:
    global _CURRENT_LANG
    if lang in CATALOGS:
        _CURRENT_LANG = lang


def get_language() -> str:
    return _CURRENT_LANG


def t(key: str, default: Optional[str] = None, **kwargs) -> str:
    lang_catalog = CATALOGS.get(_CURRENT_LANG, CATALOGS["en"])
    template = lang_catalog.get(key)
    if template is None:
        template = CATALOGS["en"].get(key, default if default is not None else key)

    if kwargs and template:
        try:
            return template.format(**kwargs)
        except (KeyError, ValueError):
            return template
    return template
