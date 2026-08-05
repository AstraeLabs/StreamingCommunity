# 07.05.26

import logging
import time
from itertools import count
from typing import Any

import requests

logger = logging.getLogger("ARR.RADARR")


class RadarrClient:
    """Native Radarr API v3 client with retry, timeout, and error handling."""

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
                logger.warning(f"Radarr request {method} {path} attempt {attempt}/{self.max_retries} failed: {exc}")

        logger.error(f"Radarr request {method} {path} failed after {self.max_retries} attempts")
        raise last_exc

    def _get(self, path: str, params: dict | None = None) -> requests.Response:
        return self._request("GET", path, params=params)

    def _get_safe(self, path: str, params: dict | None = None) -> list[dict[str, Any]]:
        """GET that returns an empty list on any HTTP/network error (no retry)."""
        url = f"{self._base}{path}"
        try:
            resp = requests.get(url, params=params, headers=self._headers, timeout=self.timeout)
            if not resp.ok:
                logger.debug(f"Radarr {path} returned {resp.status_code}, treating as empty")
                return []
            return resp.json()
        except Exception as exc:
            logger.debug(f"Radarr safe GET {path} failed: {exc}")
            return []

    def _post(self, path: str, json_data: dict | None = None) -> requests.Response:
        return self._request("POST", path, json=json_data)

    def _put(self, path: str, json_data: dict | None = None) -> requests.Response:
        return self._request("PUT", path, json=json_data)

    # ── status ───────────────────────────────────────────

    def system_status(self) -> dict[str, Any]:
        """Check Radarr connectivity and API key validity."""
        return self._get("/system/status").json()

    def is_available(self) -> bool:
        """Return True if Radarr is reachable."""
        try:
            self.system_status()
            return True
        except Exception:
            return False

    # ── config ───────────────────────────────────────────

    def get_naming_config(self) -> dict[str, Any]:
        """Get Radarr's naming/folder-format configuration (includes renameMovies)."""
        return self._get("/config/naming").json()

    # ── wanted / missing ─────────────────────────────────

    def wanted_missing(self, page: int = 1, page_size: int = 20) -> dict[str, Any]:
        """Get missing movies (paginated)."""
        return self._get(
            "/wanted/missing",
            params={
                "pageSize": page_size,
                "page": page,
            },
        ).json()

    def get_all_missing(self) -> list[dict[str, Any]]:
        """Iterate all pages and return every missing movie record."""
        all_records: list[dict[str, Any]] = []
        for page in count(1):
            data = self.wanted_missing(page=page)
            records = data.get("records", [])
            if not records:
                break
            all_records.extend(records)
        return all_records

    # ── movies ───────────────────────────────────────────

    def get_movies(self) -> list[dict[str, Any]]:
        """Get all movies in Radarr."""
        return self._get("/movie").json()

    def get_movie_by_id(self, movie_id: int) -> dict[str, Any]:
        """Get a single movie by ID."""
        return self._get(f"/movie/{movie_id}").json()

    def movie_exists(self, movie_id: int) -> bool:
        """Return True if the movie is still present in Radarr."""
        try:
            resp = requests.get(f"{self._base}/movie/{movie_id}", headers=self._headers, timeout=self.timeout)
            return resp.ok
        except Exception as exc:
            logger.debug(f"Radarr movie {movie_id} existence check failed: {exc}")
            return False

    def update_movie_path(self, movie_id: int, new_path: str) -> bool:
        """Update the root path of a movie so Radarr expects files there."""
        try:
            movie = self.get_movie_by_id(movie_id)
            if movie.get("path") == new_path:
                return True
            movie["path"] = new_path
            self._put(f"/movie/{movie_id}", json_data=movie)
            logger.info(f"Updated Radarr movie {movie_id} path to '{new_path}'")
            return True
        except Exception as exc:
            logger.error(f"Failed to update movie path: {exc}")
            return False

    def set_movie_unmonitored(self, movie_id: int) -> bool:
        """Mark a movie as unmonitored."""
        try:
            movie_data = self.get_movie_by_id(movie_id)
            movie_data["monitored"] = False
            self._put(f"/movie/{movie_id}", json_data=movie_data)
            return True
        except Exception as exc:
            logger.error(f"Failed to set movie {movie_id} unmonitored: {exc}")
            return False

    # ── queue ────────────────────────────────────────────

    def queue(self) -> dict[str, Any]:
        return self._get("/queue", params={
                "includeUnknownMovieItems": False,
                "includeMovie": False,
            },
        ).json()

    def is_movie_in_queue(self, movie_id: int) -> bool:
        """Check if a specific movie is already downloading."""
        try:
            records = self.queue().get("records", [])
            return any(r.get("movieId") == movie_id for r in records)
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
            logger.error(f"Failed to fetch Radarr tags: {exc}")
            return {}

    # ── commands ─────────────────────────────────────────

    def command_rescan_movie(self, movie_id: int) -> dict[str, Any]:
        return self._post("/command", json_data={
                "name": "RescanMovie",
                "movieId": movie_id,
            },
        ).json()

    def command_rename_movie(self, movie_id: int) -> dict[str, Any]:
        """Ask Radarr to rename a movie's files to its configured naming format."""
        return self._post("/command", json_data={
                "name": "RenameMovie",
                "movieIds": [movie_id],
            },
        ).json()

    def manual_import_lookup(self, folder_path: str, movie_id: int | None = None) -> list[dict[str, Any]]:
        """Get list of files available for manual import in a folder."""
        params: dict[str, Any] = {"folder": folder_path, "filterExistingFiles": False}
        if movie_id:
            params["movieId"] = movie_id
        return self._get_safe("/manualimport", params=params)

    def manual_import(self, import_items: list[dict[str, Any]]) -> dict[str, Any]:
        """Submit manual import decisions to Radarr."""
        files = []
        for item in import_items:
            path = str(item.get("path", "")).strip()
            if not path:
                continue

            files.append(
                {
                    "path": path,
                    "movieId": item["movieId"],
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
                logger.debug(f"Radarr command {command_id} poll failed: {exc}")
                return "unknown"
            if status in ("completed", "failed", "aborted"):
                return status
            time.sleep(1)
        return "timeout"
