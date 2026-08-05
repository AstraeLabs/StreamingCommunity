from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

import VibraVid.provider.tmdb as tmdb_module
from GUI.searchapp.arr import downloader_service as downloader_module
from GUI.searchapp.arr.downloader_service import ArrDownloaderService
from VibraVid.provider.tmdb import TMDBClient


@pytest.fixture
def service() -> ArrDownloaderService:
    """Build the unit under test without loading ARR config or starting clients."""
    instance = object.__new__(ArrDownloaderService)
    instance.sonarr = None
    instance.radarr = None
    instance._provider_tmdb_cache = {}
    return instance


def _result(
    *,
    name: str = "The Example",
    year: int = 2024,
    media_type: str = "series",
    tmdb_id: int | str | None = None,
    result_id: str = "result-1",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=result_id,
        name=name,
        year=year,
        type=media_type,
        tmdb_id=tmdb_id,
        is_movie=media_type in {"film", "movie", "ova"},
        raw_data={},
        url=f"https://provider.invalid/{result_id}",
    )


def _install_provider(monkeypatch: pytest.MonkeyPatch, results: list[Any], resolver=None) -> Any:
    """Install a tiny searchapp.api replacement so registry discovery never runs."""
    api = SimpleNamespace(search=lambda _query: results)
    if resolver is not None:
        api.resolve_tmdb_id = resolver

    searchapp = ModuleType("searchapp")
    searchapp.__path__ = []
    api_module = ModuleType("searchapp.api")
    api_module.get_api = lambda _provider: api
    searchapp.api = api_module

    monkeypatch.setitem(sys.modules, "searchapp", searchapp)
    monkeypatch.setitem(sys.modules, "searchapp.api", api_module)
    return api


def _set_tmdb_key(monkeypatch: pytest.MonkeyPatch, api_key: str | None) -> SimpleNamespace:
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    tmdb = SimpleNamespace(api_key=api_key)
    monkeypatch.setattr(ArrDownloaderService, "_tmdb_client", staticmethod(lambda: tmdb))
    return tmdb


@pytest.mark.parametrize(
    ("requested_type", "provider_type"),
    [("movie", "movie"), ("tv", "series")],
)
def test_strict_exact_tmdb_identity_is_accepted_and_propagated(
    monkeypatch: pytest.MonkeyPatch,
    service: ArrDownloaderService,
    requested_type: str,
    provider_type: str,
) -> None:
    _set_tmdb_key(monkeypatch, "configured-key")
    candidate = _result(media_type=provider_type, tmdb_id=603)
    _install_provider(monkeypatch, [candidate])

    payload = service._search_and_build_payload(
        "The Example",
        "offline-provider",
        expected_title="The Example",
        expected_year=2024,
        year_range="2023-2025",
        tmdb_id="0603",
        media_type=requested_type,
    )

    assert payload is not None
    assert payload["tmdb_id"] == "603"
    assert payload["type"] == requested_type
    assert payload["is_movie"] is (requested_type == "movie")
    assert payload["raw_data"]["tmdb_id"] == "603"
    assert payload["raw_data"]["type"] == requested_type


def test_strict_tmdb_mismatch_is_rejected_even_for_same_title_and_year(
    monkeypatch: pytest.MonkeyPatch,
    service: ArrDownloaderService,
) -> None:
    _set_tmdb_key(monkeypatch, "configured-key")
    _install_provider(monkeypatch, [_result(tmdb_id=999)])

    payload = service._search_and_build_payload(
        "The Example",
        "offline-provider",
        expected_title="The Example",
        expected_year=2024,
        year_range="2024-2024",
        tmdb_id=603,
        media_type="tv",
    )

    assert payload is None


def test_strict_result_without_id_or_provider_hook_is_unverifiable(
    monkeypatch: pytest.MonkeyPatch,
    service: ArrDownloaderService,
) -> None:
    _set_tmdb_key(monkeypatch, "configured-key")
    _install_provider(monkeypatch, [_result(tmdb_id=None)])

    payload = service._search_and_build_payload(
        "The Example",
        "offline-provider",
        expected_title="The Example",
        expected_year=2024,
        year_range="2024-2024",
        tmdb_id=603,
        media_type="tv",
    )

    assert payload is None


def test_strict_provider_hook_can_attest_exact_id_and_propagate_it(
    monkeypatch: pytest.MonkeyPatch,
    service: ArrDownloaderService,
) -> None:
    _set_tmdb_key(monkeypatch, "configured-key")
    candidate = _result(tmdb_id=None, result_id="resolved-by-hook")
    resolver_calls: list[Any] = []

    def resolve_tmdb_id(result: Any) -> int:
        resolver_calls.append(result)
        return 603

    _install_provider(monkeypatch, [candidate], resolver=resolve_tmdb_id)

    payload = service._search_and_build_payload(
        "The Example",
        "offline-provider",
        tmdb_id=603,
        media_type="tv",
    )

    assert payload is not None
    assert payload["tmdb_id"] == "603"
    assert payload["raw_data"]["tmdb_id"] == "603"
    assert resolver_calls == [candidate]


def test_anonymous_results_do_not_share_provider_tmdb_cache(
    monkeypatch: pytest.MonkeyPatch,
    service: ArrDownloaderService,
) -> None:
    _set_tmdb_key(monkeypatch, "configured-key")

    def anonymous_result(attested_tmdb_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            id=None,
            url=None,
            path_id=None,
            slug=None,
            provider_language="it",
            name="The Example",
            year=2024,
            type="series",
            tmdb_id=None,
            attested_tmdb_id=attested_tmdb_id,
            is_movie=False,
            raw_data={},
        )

    _install_provider(
        monkeypatch,
        [anonymous_result(999), anonymous_result(603)],
        resolver=lambda result: result.attested_tmdb_id,
    )

    payload = service._search_and_build_payload(
        "The Example",
        "offline-provider",
        tmdb_id=603,
        media_type="tv",
    )

    assert payload is not None
    assert payload["tmdb_id"] == "603"


def test_strict_mode_rejects_request_without_target_tmdb_id(
    monkeypatch: pytest.MonkeyPatch,
    service: ArrDownloaderService,
) -> None:
    _set_tmdb_key(monkeypatch, "configured-key")
    _install_provider(monkeypatch, [_result(tmdb_id=603)])

    payload = service._search_and_build_payload(
        "The Example",
        "offline-provider",
        expected_title="The Example",
        expected_year=2024,
        year_range="2024-2024",
        tmdb_id=None,
        media_type="tv",
    )

    assert payload is None


def test_tmdb_env_keeps_strict_mode_enabled_if_client_import_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TMDB_API_KEY", "configured-key")
    monkeypatch.setattr(ArrDownloaderService, "_tmdb_client", staticmethod(lambda: None))

    assert ArrDownloaderService._strict_tmdb_matching_enabled() is True


def test_without_tmdb_key_legacy_title_and_year_fallback_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
    service: ArrDownloaderService,
) -> None:
    _set_tmdb_key(monkeypatch, None)
    _install_provider(monkeypatch, [_result(tmdb_id=None)])

    payload = service._search_and_build_payload(
        "The Example",
        "offline-provider",
        expected_title="The Example",
        expected_year=2024,
        year_range="2024-2024",
        tmdb_id=603,
        media_type="tv",
    )

    assert payload is not None
    assert payload["name"] == "The Example"


@pytest.mark.parametrize(
    ("requested_type", "provider_type"),
    [("tv", "movie"), ("movie", "series")],
)
def test_strict_tmdb_id_cannot_cross_movie_tv_namespace(
    monkeypatch: pytest.MonkeyPatch,
    service: ArrDownloaderService,
    requested_type: str,
    provider_type: str,
) -> None:
    _set_tmdb_key(monkeypatch, "configured-key")
    _install_provider(monkeypatch, [_result(media_type=provider_type, tmdb_id=603)])

    payload = service._search_and_build_payload(
        "The Example",
        "offline-provider",
        tmdb_id=603,
        media_type=requested_type,
    )

    assert payload is None


def test_ita_preference_does_not_bypass_strict_tmdb_check(
    monkeypatch: pytest.MonkeyPatch,
    service: ArrDownloaderService,
) -> None:
    _set_tmdb_key(monkeypatch, "configured-key")
    monkeypatch.setattr(
        downloader_module.json,
        "load",
        lambda _file: {"ARR": {"download_italian_anime_default": True}},
    )
    _install_provider(
        monkeypatch,
        [_result(name="The Example (ITA)", tmdb_id=999, result_id="ita-wrong-id")],
    )

    payload = service._search_and_build_payload(
        "The Example",
        "animeworld",
        expected_title="The Example",
        expected_year=2024,
        year_range="2024-2024",
        tmdb_id=603,
        media_type="tv",
        season_number=1,
    )

    assert payload is None


def test_anime_season_match_does_not_bypass_strict_tmdb_check(
    monkeypatch: pytest.MonkeyPatch,
    service: ArrDownloaderService,
) -> None:
    _set_tmdb_key(monkeypatch, "configured-key")
    _install_provider(
        monkeypatch,
        [_result(name="The Example 2", tmdb_id=999, result_id="season-two-wrong-id")],
    )

    payload = service._search_and_build_payload(
        "The Example",
        "animeunity",
        expected_title="The Example",
        tmdb_id=603,
        media_type="tv",
        season_number=2,
    )

    assert payload is None


def test_resolve_tvdb_request_id_through_tmdb_client(
    monkeypatch: pytest.MonkeyPatch,
    service: ArrDownloaderService,
) -> None:
    calls: list[tuple[int, str, str]] = []

    class FakeTMDB:
        api_key = "configured-key"

        def get_tmdb_id_by_external_id(
            self,
            external_id: int,
            external_source: str,
            media_type: str,
        ) -> int:
            calls.append((external_id, external_source, media_type))
            return 456

    service.sonarr = SimpleNamespace(get_series_by_id=lambda series_id: {"id": series_id, "tvdbId": 123})
    fake_tmdb = FakeTMDB()
    monkeypatch.setattr(ArrDownloaderService, "_tmdb_client", staticmethod(lambda: fake_tmdb))
    request = {"id": 7, "title": "TVDB-only show"}

    resolved = service._resolve_request_tmdb_id(request, "tv")

    assert resolved == 456
    assert request["tmdbId"] == 456
    assert calls == [(123, "tvdb_id", "tv")]


@pytest.mark.parametrize(
    ("direct_tmdb_id", "expected"),
    [(603, 603), (999, None)],
)
def test_direct_sonarr_tmdb_must_agree_with_tvdb_mapping(
    monkeypatch: pytest.MonkeyPatch,
    service: ArrDownloaderService,
    direct_tmdb_id: int,
    expected: int | None,
) -> None:
    monkeypatch.delenv("TMDB_API_KEY", raising=False)
    tmdb = SimpleNamespace(
        api_key="configured-key",
        get_tmdb_id_by_external_id=lambda _external_id, _source, _media_type: 603,
    )
    monkeypatch.setattr(ArrDownloaderService, "_tmdb_client", staticmethod(lambda: tmdb))
    request = {"id": 7, "title": "The Example", "tmdbId": direct_tmdb_id, "tvdbId": 123}

    assert service._resolve_request_tmdb_id(request, "tv") == expected
    if expected is None:
        assert service.last_error == "external_id_conflict"


@pytest.mark.parametrize(
    ("media_type", "expected"),
    [("tv", 456), ("movie", 789)],
)
def test_tmdb_client_resolves_external_id_in_requested_namespace(
    monkeypatch: pytest.MonkeyPatch,
    media_type: str,
    expected: int,
) -> None:
    client = TMDBClient("offline-key")
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_request(endpoint: str, params: dict[str, str]) -> dict[str, list[dict[str, int]]]:
        calls.append((endpoint, params))
        return {
            "tv_results": [{"id": 456}],
            "movie_results": [{"id": 789}],
        }

    monkeypatch.setattr(client, "_make_request", fake_request)

    resolved = client.get_tmdb_id_by_external_id(123, "tvdb_id", media_type)

    assert resolved == expected
    assert calls == [
        (
            "find/123",
            {"external_source": "tvdb_id", "language": "en-US"},
        )
    ]


def test_tmdb_client_rejects_ambiguous_external_id(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TMDBClient("offline-key")
    monkeypatch.setattr(
        client,
        "_make_request",
        lambda _endpoint, _params: {"tv_results": [{"id": 456}, {"id": 789}]},
    )

    assert client.get_tmdb_id_by_external_id(123, "tvdb_id", "tv") is None


def test_tmdb_client_reads_tvdb_id_from_exact_tv_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TMDBClient("offline-key")
    calls: list[str] = []

    def fake_request(endpoint: str) -> dict[str, int]:
        calls.append(endpoint)
        return {"tvdb_id": 123}

    monkeypatch.setattr(client, "_make_request", fake_request)

    assert client.get_external_id(603, "tv", "tvdb_id") == 123
    assert calls == ["tv/603/external_ids"]


def test_shared_tmdb_key_can_be_refreshed_after_login_reload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tmdb_module, "_configured_api_key", lambda: "new-live-key")
    monkeypatch.setattr(tmdb_module, "api_key", tmdb_module.api_key)
    monkeypatch.setattr(tmdb_module.tmdb_client, "api_key", "old-key")
    monkeypatch.setattr(
        tmdb_module.tmdb_client,
        "_warned_no_api_key",
        tmdb_module.tmdb_client._warned_no_api_key,
    )

    assert tmdb_module.refresh_api_key() == "new-live-key"
    assert tmdb_module.tmdb_client.api_key == "new-live-key"
