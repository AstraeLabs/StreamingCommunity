import os
import pytest
from VibraVid.tui.i18n import t, set_language, get_language, detect_system_language

def test_i18n_default_and_fallback():
    set_language("en")
    assert get_language() == "en"
    assert t("seasons") == "Seasons"
    
    set_language("it")
    assert get_language() == "it"
    assert t("seasons") == "Stagioni"

def test_i18n_format_args():
    set_language("it")
    assert "5" in t("results_found", count=5)
    set_language("en")
    assert "5" in t("results_found", count=5)

def test_i18n_unknown_key():
    set_language("it")
    assert t("unknown_key_xyz") == "unknown_key_xyz"
    assert t("unknown_key_xyz", default="Default Value") == "Default Value"

def test_i18n_detect_system_language(monkeypatch):
    monkeypatch.setenv("LANG", "it_IT.UTF-8")
    assert detect_system_language() == "it"
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert detect_system_language() == "en"
