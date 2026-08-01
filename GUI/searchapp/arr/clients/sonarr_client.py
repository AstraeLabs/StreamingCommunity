# 07.05.26

import logging
import time
from itertools import count
from typing import Any

import requests

logger = logging.getLogger("ARR.SONARR")


class SonarrClient:
    """Native Sonarr API v3 client with retry, timeout, and error handling."""

    def __init__(self, url: str, api_key: str, timeout: int = 15, max_retries: int = 3):
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._base = f"{self.url}/api/v3"
        self._headers = {"X-Api-Key": self.api_key}

    # ── helpers ──────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Execute an HTTP request with retry logic."""
        url = f"{self._base}{path}"
        kwargs.setdefault("headers", self._headers)
        kwargs.setdefault("timeout", self.timeout)

        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                logger.warning(f"Sonarr request {method} {path} attempt {attempt}/{self.max_retries} failed: {exc}")

        logger.error(f"Sonarr request {method} {path} failed after {self.max_retries} attempts")
        raise last_exc

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        return self._request("GET", path, params=params)

    def _get_safe(self, path: str, params: dict | None = None) -> list[dict[str, Any]]:
        """GET that returns an empty list on any HTTP/network error (no retry).

        Use for optional/informational endpoints (e.g. manualimport lookup)
        where a 4xx/5xx should be treated as "nothing found" rather than a hard error.
        """
        url = f"{self._base}{path}"
        try:
            resp = requests.get(url, params=params, headers=self._headers, timeout=self.timeout)
            if not resp.ok:
                logger.debug(f"Sonarr {path} returned {resp.status_code}, treating as empty")
                return []
            return resp.json()
        except Exception as exc:
            logger.debug(f"Sonarr safe GET {path} failed: {exc}")
            return []

    def _post(self, path: str, json_data: dict | None = None) -> requests.Response:
        return self._request("POST", path, json=json_data)

    def _put(self, path: str, json_data: dict | None = None) -> requests.Response:
        return self._request("PUT", path, json=json_data)

    # ── status ───────────────────────────────────────────

    def system_status(self) -> dict[str, Any]:
        """Check Sonarr connectivity and API key validity."""
        return self._get("/system/status").json()

    def is_available(self) -> bool:
        """Return True if Sonarr is reachable."""
        try:
            self.system_status()
            return True
        except Exception:
            return False

    # ── config ───────────────────────────────────────────

    def get_naming_config(self) -> dict[str, Any]:
        """Get Sonarr's naming/folder-format configuration (includes seasonFolderFormat)."""
        return self._get("/config/naming").json()

    # ── wanted / missing ─────────────────────────────────

    def wanted_missing(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Get missing episodes (paginated)."""
        return self._get("/wanted/missing", params={
                "includeSeries": True,
                "pageSize": page_size,
                "page": page,
            },
        ).json()

    def get_all_missing(self) -> list[dict[str, Any]]:
        """Iterate all pages and return every missing episode record."""
        all_records: list[dict[str, Any]] = []
        for page in count(1):
            data = self.wanted_missing(page=page)
            records = data.get("records", [])
            if not records:
                break
            all_records.extend(records)
        return all_records

    # ── series ───────────────────────────────────────────

    def get_series(self) -> list[dict[str, Any]]:
        """Get all series in Sonarr."""
        return self._get("/series").json()

    def get_series_by_id(self, series_id: int) -> dict[str, Any]:
        """Get a single series by ID."""
        return self._get(f"/series/{series_id}").json()

    def series_exists(self, series_id: int) -> bool:
        """Return True if the series is still present in Sonarr."""
        try:
            resp = requests.get(f"{self._base}/series/{series_id}", headers=self._headers, timeout=self.timeout)
            return resp.ok
        except Exception as exc:
            logger.debug(f"Sonarr series {series_id} existence check failed: {exc}")
            return False

    def update_series_path(self, series_id: int, new_path: str) -> bool:
        """Update the root path of a series so Sonarr expects files there."""
        try:
            series = self.get_series_by_id(series_id)
            if series.get("path") == new_path:
                return True
            series["path"] = new_path
            self._put(f"/series/{series_id}", json_data=series)
            logger.info(f"Updated Sonarr series {series_id} path to '{new_path}'")
            return True
        except Exception as exc:
            logger.error(f"Failed to update series path: {exc}")
            return False

    # ── episodes ─────────────────────────────────────────

    def get_episode(self, episode_id: int) -> dict[str, Any]:
        return self._get(f"/episode/{episode_id}").json()

    def get_episodes_for_series(self, series_id: int) -> list[dict[str, Any]]:
        """Get all episodes for a specific series."""
        return self._get("/episode", params={"seriesId": series_id}).json()

    def set_episode_unmonitored(self, episode_ids: list[int]) -> bool:
        """Mark episodes as unmonitored so they disappear from wanted/missing."""
        try:
            self._put("/episode/monitor", json_data={
                    "episodeIds": episode_ids,
                    "monitored": False,
                },
            )
            return True
        except Exception as exc:
            logger.error(f"Failed to set episodes unmonitored: {exc}")
            return False

    # ── queue ────────────────────────────────────────────

    def queue(self) -> dict[str, Any]:
        return self._get("/queue", params={
                "includeUnknownSeriesItems": False,
                "includeSeries": False,
                "includeEpisode": False,
            },
        ).json()

    def is_episode_in_queue(self, episode_id: int) -> bool:
        """Check if a specific episode is already downloading."""
        try:
            records = self.queue().get("records", [])
            return any(r.get("episodeId") == episode_id for r in records)
        except Exception:
            return False

    # ── tags ─────────────────────────────────────────────

    def get_tags(self) -> list[dict[str, Any]]:
        return self._get("/tag").json()

    def get_tags_map(self) -> dict[int, str]:
        """Return {tag_id: tag_label_lowercase}."""
        try:
            return {t["id"]: t["label"].lower() for t in self.get_tags()}
        except Exception as exc:
            logger.error(f"Failed to fetch Sonarr tags: {exc}")
            return {}

    # ── commands ─────────────────────────────────────────

    def command_rescan_series(self, series_id: int) -> dict[str, Any]:
        return self._post("/command", json_data={
                "name": "RescanSeries",
                "seriesId": series_id,
            },
        ).json()

    def command_rename_series(self, series_id: int) -> dict[str, Any]:
        """Ask Sonarr to rename a series' files to its configured naming format."""
        return self._post("/command", json_data={
                "name": "RenameSeries",
                "seriesIds": [series_id],
            },
        ).json()

    def command_series_search(self, series_id: int) -> dict[str, Any]:
        """Trigger a search for all missing episodes of a series."""
        return self._post("/command", json_data={
                "name": "SeriesSearch",
                "seriesId": series_id,
            },
        ).json()

    def manual_import_lookup(self, folder_path: str, series_id: int | None = None) -> list[dict[str, Any]]:
        """Get list of files available for manual import in a folder.

        Returns [] (not raises) if the folder is missing, empty, or Sonarr returns an error.
        """
        params: dict[str, Any] = {"folder": folder_path, "filterExistingFiles": False}
        if series_id:
            params["seriesId"] = series_id
        return self._get_safe("/manualimport", params=params)

    def manual_import(self, import_items: list[dict[str, Any]]) -> dict[str, Any]:
        """Submit manual import decisions to Sonarr"""
        files = []
        for item in import_items:
            path = str(item.get("path", "")).strip()
            episode_ids = [e["id"] for e in (item.get("episodes") or []) if e.get("id")]
            if not path or not episode_ids:
                continue

            files.append(
                {
                    "path": path,
                    "seriesId": item["seriesId"],
                    "episodeIds": episode_ids,
                    "quality": item.get("quality"),
                    "languages": item.get("languages"),
                    "releaseGroup": item.get("releaseGroup") or "",
                    "indexerFlags": item.get("indexerFlags", 0),
                }
            )

        if not files:
            return {}

        return self._post("/command", json_data={
                "name": "ManualImport",
                "files": files,
                "importMode": "Move",
            },
        ).json()

    def get_command(self, command_id: int) -> dict[str, Any]:
        """Poll a queued command's state."""
        return self._get(f"/command/{command_id}").json()

    def wait_command(self, command_id: int, timeout: int = 120) -> str:
        """Block until a command reaches a terminal state; return its status."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                status = self.get_command(command_id).get("status", "")
            except Exception as exc:
                logger.debug(f"Sonarr command {command_id} poll failed: {exc}")
                return "unknown"
            if status in ("completed", "failed", "aborted"):
                return status
            time.sleep(1)
        return "timeout"
