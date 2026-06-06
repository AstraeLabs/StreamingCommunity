# 07.05.26

"""
Downloader Service — replaces the standalone Downloader.py from VibraVidArr.

Instead of spawning a subprocess (`VibraVid --search ...`), this service
directly calls the VibraVid internal streaming API (`get_api(site).search()` /
`start_download()`) using the same pipeline that the GUI uses.
"""

import datetime
import json
import logging
import pathlib
import time
from typing import Any, Dict, List, Optional, Tuple

from .clients.sonarr_client import SonarrClient
from .clients.radarr_client import RadarrClient

logger = logging.getLogger("ARR")


class ArrDownloaderService:
    """Downloads media by invoking VibraVid's native streaming API pipeline."""

    def __init__(self, sonarr: SonarrClient, radarr: RadarrClient):
        self.sonarr = sonarr
        self.radarr = radarr
        self.last_error: Optional[str] = None

    # ── public ───────────────────────────────────────────

    def download(self, item: dict) -> bool:
        """Dispatch a single missing item (serie or movie) to VibraVid's pipeline."""
        content_type = item.get("content_type")
        if content_type == "serie":
            return self._process_serie(item)
        elif content_type == "movie":
            return self._process_movie(item)
        else:
            logger.error(f"Unknown content_type: {content_type}")
            return False

    # ── serie ────────────────────────────────────────────

    def _process_serie(self, serie: dict) -> bool:
        from searchapp.views import _run_download_in_thread
        self.last_error = None

        title = serie["title"]
        series_id = serie.get("id")
        provider = (serie.get("provider") or "").strip()
        any_success = False

        tmdb_id = serie.get("tmdbId")
        titles = self._resolve_sonarr_title(title, series_id, tmdb_id) or [title]
        logger.info(f"[_process_serie] Title='{title}', Title candidates={titles}, TMDB ID='{tmdb_id}'")

        year = serie.get("year")
        year_range = self._build_year_range(year)

        for season in serie.get("seasons", []):
            season_num = season["number"]
            for episode in season.get("episodes", []):
                ep_num = episode["episodeNumber"]
                ep_id = episode.get("id")

                if not ep_id:
                    logger.warning(
                        f"S{season_num}E{ep_num} of '{title}' has no episode ID, skipping"
                    )
                    continue

                if self.sonarr.is_episode_in_queue(ep_id):
                    logger.info(f"S{season_num}E{ep_num} of '{title}' already in Sonarr queue, skipping")
                    continue

                if not self._is_sonarr_episode_still_monitored(series_id, ep_id):
                    logger.info(f"S{season_num}E{ep_num} of '{title}' is now unmonitored in Sonarr, skipping")
                    self.last_error = "sonarr_unmonitored"
                    continue

                display_title = f"{titles[0]} - S{season_num} E{ep_num}"
                logger.info(f"⏳ Downloading '{display_title}' via {provider or 'configured fallback order'}")

                item_payload, provider, matched_title = self._search_with_fallback_titles(
                    titles, provider,
                    year_range=year_range,
                    expected_year=year,
                    tmdb_id=serie.get("tmdbId"),
                    media_type="tv",
                    season_number=season_num,
                )
                if not item_payload:
                    logger.error(f"✖️ Could not find '{title}' using candidates: {titles}")
                    self.last_error = "search_no_results"
                    continue

                # Use Sonarr's path for the series, fallback to OUTPUT config root
                series_root = serie.get("path", "")
                if not series_root:
                    series_root = self._fallback_series_root(title)

                # Target folder: series root + season subfolder
                target_folder = str(pathlib.Path(series_root).joinpath(f"S{season_num:02d}"))
                logger.info(f"[S{season_num}E{ep_num}] Target folder (Sonarr's path): '{target_folder}'")

                download_folder = self._translate_path(target_folder, reverse=True)
                if download_folder != target_folder:
                    logger.info(f"[S{season_num}E{ep_num}] Download folder (VibraVid path): '{download_folder}'")

                # Download directly to VibraVid's equivalent path
                future = _run_download_in_thread(
                    site=provider,
                    item_payload=item_payload,
                    season=str(season_num),
                    episodes=str(ep_num),
                    media_type="Serie",
                    output_path=download_folder,
                )
                any_success = True

                try:
                    future.result(timeout=7200)  # wait for download to actually finish
                    time.sleep(2)

                    # Get series root path for rescan
                    series_root = serie.get("path", "")
                    if not series_root:
                        series_root = self._fallback_series_root(title)
                    logger.info(f"[S{season_num}E{ep_num}] Using series root path: '{series_root}'")

                    # Rescan series on the new path
                    try:
                        self.sonarr.command_rescan_series(serie["id"])
                        time.sleep(1)
                        self.sonarr.command_downloaded_episodes_scan(self._translate_path(target_folder))
                        logger.info(f"Rescan/import scan completed for S{season_num}E{ep_num}")
                    except Exception as scan_exc:
                        logger.warning(f"Rescan failed: {scan_exc}")

                    # Verify import state without manual import payload
                    imported = False
                    for _ in range(24):  # Wait up to 120 seconds
                        try:
                            episode = self.sonarr.get_episode(ep_id)
                            if episode.get("hasFile") or episode.get("episodeFileId"):
                                imported = True
                                break
                        except Exception as exc:
                            logger.warning(f"Failed to verify Sonarr episode import: {exc}")
                        time.sleep(5)
                    if not imported:
                        result_name = item_payload.get("name", matched_title)
                        result_year = item_payload.get("year", year)
                        fallback_folder = self._get_vibrativo_serie_output(series_root, result_name, season_num, result_year)
                        if fallback_folder and fallback_folder != target_folder:
                            logger.warning(
                                f"S{season_num}E{ep_num} import not confirmed from '{target_folder}', "
                                f"trying fallback scan path '{fallback_folder}'"
                            )
                            try:
                                self.sonarr.command_downloaded_episodes_scan(self._translate_path(fallback_folder))
                            except Exception as fallback_scan_exc:
                                logger.warning(f"Fallback rescan failed: {fallback_scan_exc}")
                            for _ in range(12):
                                try:
                                    episode = self.sonarr.get_episode(ep_id)
                                    if episode.get("hasFile") or episode.get("episodeFileId"):
                                        imported = True
                                        break
                                except Exception as exc:
                                    logger.warning(f"Failed to verify Sonarr episode import after fallback scan: {exc}")
                                time.sleep(5)

                    if not imported:
                        logger.error(f"S{season_num}E{ep_num} import not confirmed in Sonarr")
                        self.last_error = "import_not_confirmed"
                        any_success = False
                        continue

                    logger.info(f"S{season_num}E{ep_num} of '{title}' completed and imported")
                except Exception as exc:
                    logger.error(f"S{season_num}E{ep_num} of '{title}' failed: {exc}")
                    self.last_error = str(exc)
                    # Don't unmonitor on failure → stays in Sonarr's wanted list for retry
                    any_success = False

        return any_success

    # ── movie ────────────────────────────────────────────

    def _process_movie(self, movie: dict) -> bool:
        from searchapp.views import _run_download_in_thread
        self.last_error = None

        title = movie["title"]
        movie_id = movie["id"]
        tmdb_id = movie.get("tmdbId")
        provider = (movie.get("provider") or "").strip()

        if self.radarr.is_movie_in_queue(movie_id):
            logger.info(f"'{title}' already in Radarr queue, skipping")
            return False

        if not self._is_radarr_movie_still_monitored(movie_id):
            logger.info(f"'{title}' is now unmonitored in Radarr, skipping")
            self.last_error = "radarr_unmonitored"
            return False

        titles = self._resolve_radarr_title(title, movie_id, tmdb_id) or [title]

        year = movie.get("year")
        year_range = self._build_year_range(year)

        logger.info(
            f"⏳ Downloading movie '{titles[0]}' ({year}) "
            f"via {provider or 'configured fallback order'}; candidates={titles}"
        )

        item_payload, provider, matched_title = self._search_with_fallback_titles(
            titles, provider,
            year_range=year_range,
            expected_year=year,
            tmdb_id=tmdb_id,
            media_type="movie",
        )
        if not item_payload:
            logger.error(f"Could not find movie '{title}' using candidates: {titles}")
            self.last_error = "search_no_results"
            return False

        # Use Radarr's path for the movie, fallback to OUTPUT config root
        target_folder = movie.get("path", "")
        if not target_folder:
            target_folder = self._fallback_movie_root(title)
        logger.info(f"[_process_movie] Target folder (Radarr's path): '{target_folder}'")

        download_folder = self._translate_path(target_folder, reverse=True)
        if download_folder != target_folder:
            logger.info(f"[_process_movie] Download folder (VibraVid path): '{download_folder}'")

        future = _run_download_in_thread(
            site=provider,
            item_payload=item_payload,
            season=None,
            episodes=None,
            media_type="Film",
            output_path=download_folder,
        )

        try:
            future.result(timeout=7200)  # wait for download to actually finish
            time.sleep(2)

            result_name = item_payload.get("name", matched_title)
            result_year = item_payload.get("year", year)
            fallback_folder = self._get_vibrativo_movie_output(target_folder, result_name, result_year)

            # Rescan movie on the new path
            try:
                self.radarr.command_rescan_movie(movie_id)
                time.sleep(1)
                self.radarr.command_downloaded_movies_scan(self._translate_path(target_folder))
                logger.info(f"Rescan/import scan completed for '{title}'")
            except Exception as scan_exc:
                logger.warning(f"Rescan failed: {scan_exc}")

            # Verify import state without manual import payload
            imported = False
            for _ in range(60):  # Wait up to 300 seconds
                try:
                    movie_obj = self.radarr.get_movie_by_id(movie_id)
                    if movie_obj.get("hasFile") or movie_obj.get("movieFileId"):
                        imported = True
                        break
                except Exception as exc:
                    logger.warning(f"Failed to verify Radarr movie import: {exc}")
                time.sleep(5)
            if not imported:
                if fallback_folder and fallback_folder != target_folder:
                    logger.warning(
                        f"Movie '{title}' import not confirmed from '{target_folder}', "
                        f"trying fallback scan path '{fallback_folder}'"
                    )
                    try:
                        self.radarr.command_downloaded_movies_scan(self._translate_path(fallback_folder))
                    except Exception as fallback_scan_exc:
                        logger.warning(f"Fallback movie rescan failed: {fallback_scan_exc}")
                    for _ in range(24):
                        try:
                            movie_obj = self.radarr.get_movie_by_id(movie_id)
                            if movie_obj.get("hasFile") or movie_obj.get("movieFileId"):
                                imported = True
                                break
                        except Exception as exc:
                            logger.warning(f"Failed to verify Radarr movie import after fallback scan: {exc}")
                        time.sleep(5)

            if not imported:
                logger.error(f"Movie '{title}' import not confirmed in Radarr")
                self.last_error = "import_not_confirmed"
                return False

            logger.info(f"'{title}' completed and imported")
            return True
        except Exception as exc:
            logger.error(f"'{title}' failed: {exc}")
            self.last_error = str(exc)
            # Don't unmonitor on failure → stays in Radarr's wanted list for retry
            return False

    # ── helpers ──────────────────────────────────────────

    def _is_radarr_movie_still_monitored(self, movie_id: Optional[int]) -> bool:
        """Read Radarr live state before starting a movie download."""
        if not self.radarr or not movie_id:
            return True
        try:
            movie = self.radarr.get_movie_by_id(movie_id)
            return movie.get("monitored", True) is not False
        except Exception as exc:
            logger.warning(f"Could not verify Radarr monitored state before download: {exc}")
            return True

    def _is_sonarr_episode_still_monitored(self, series_id: Optional[int], episode_id: Optional[int]) -> bool:
        """Read Sonarr live state before starting an episode download."""
        if not self.sonarr:
            return True
        try:
            if series_id:
                series = self.sonarr.get_series_by_id(series_id)
                if series.get("monitored", True) is False:
                    return False
            if episode_id:
                episode = self.sonarr.get_episode(episode_id)
                if episode.get("monitored", True) is False:
                    return False
        except Exception as exc:
            logger.warning(f"Could not verify Sonarr monitored state before download: {exc}")
        return True

    @staticmethod
    def _translate_path(path: str, reverse: bool = False) -> str:
        """Translate paths between VibraVid and Radarr/Sonarr Docker containers.

        Reads path_mapping from ARR config. Each entry maps a host prefix to a container prefix.
        Example: {"/media/Media/Film": "/media/Film"}
        """
        if not path:
            return path
        conf_path = pathlib.Path(__file__).parent.parent.parent.parent / "Conf" / "config.json"
        try:
            with open(conf_path, encoding="utf-8") as _f:
                mapping = json.load(_f).get("ARR", {}).get("path_mapping", {})
                if not isinstance(mapping, dict):
                    return path
        except Exception:
            return path

        sort_index = 1 if reverse else 0
        for host_prefix, container_prefix in sorted(mapping.items(), key=lambda item: len(item[sort_index]), reverse=True):
            source_prefix, target_prefix = (container_prefix, host_prefix) if reverse else (host_prefix, container_prefix)
            source_prefix = source_prefix.rstrip("/\\")
            target_prefix = target_prefix.rstrip("/\\")
            if path == source_prefix or path.startswith(source_prefix + "/") or path.startswith(source_prefix + "\\"):
                translated = target_prefix + path[len(source_prefix):]
                logger.info(f"[path_map] '{path}' → '{translated}'")
                return translated
        if reverse:
            logger.info(f"[path_map] No reverse mapping matched '{path}', leaving it unchanged")
        return path

    @staticmethod
    def _strip_accents(text: str) -> str:
        """Replace accented characters with their ASCII base: à→a, è→e, ì→i, ò→o, ù→u, etc."""
        import unicodedata
        return "".join(
            c for c in unicodedata.normalize("NFKD", text)
            if unicodedata.category(c) != "Mn"  # Mn = combining marks (the accent part)
        )

    @staticmethod
    def _titles_are_compatible(title: str, result_name: str) -> bool:
        """Check that result_name shares enough significant words with title.

        Guards against accepting completely unrelated titles that happen to match
        the year range (e.g. 'My Teacher' when searching 'My Hero Academia').
        Requires at least 50% of the significant words (>3 chars) in the search
        title to appear in the result title. If the search has no significant
        words, the check is skipped and True is returned.
        """
        import re

        def sig_words(s: str):
            s = ArrDownloaderService._strip_accents(s)
            return {w.lower() for w in re.split(r'\W+', s) if len(w) > 3}

        sw = sig_words(title)
        if not sw:
            # Non-ASCII title (e.g. Japanese/Korean) — can't verify by word match,
            # reject to force TMDB ID check or fallback providers
            return False
        rw = sig_words(result_name)
        overlap = sw & rw
        ratio = len(overlap) / len(sw)
        if len(sw) <= 2:
            return ratio == 1.0
        return ratio >= 0.5

    @staticmethod
    def _normalize_title(title: str) -> str:
        import re

        title = ArrDownloaderService._strip_accents(title or "").lower()
        title = re.sub(r"[^\w\s]", " ", title)
        title = re.sub(r"\s+", " ", title).strip()
        return title

    @staticmethod
    def _verify_title_match(result_name: str, expected_title: str,
                            result_year: Optional[int] = None,
                            expected_year: Optional[int] = None) -> bool:
        """Verify a search result matches the expected title/year from ARR metadata.

        Uses normalized string comparison (lowercase, accents removed, punctuation stripped).
        """
        if not result_name or not expected_title:
            return False

        import re
        import unicodedata

        def normalize(s: str) -> str:
            """Normalize: lowercase, remove accents, remove punctuation, collapse spaces."""
            s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
            s = re.sub(r'[^\w\s]', ' ', s.lower())
            s = re.sub(r'\s+', ' ', s).strip()
            return s

        rn = normalize(result_name)
        et = normalize(expected_title)

        # Exact match or one contains the other (after normalization)
        if rn == et or et in rn or rn in et:
            # Year check with +/- 1 year tolerance
            if expected_year is not None and result_year is not None:
                try:
                    return abs(int(result_year) - int(expected_year)) <= 1
                except (ValueError, TypeError):
                    pass
            return True

        return False

    def _search_with_fallback(
        self,
        title: str,
        primary_provider: str,
        **kwargs,
    ) -> tuple[Optional[Dict[str, Any]], str]:
        """Try primary_provider first, then the fallback list from ARR config.

        Returns (payload, used_provider). payload is None if nothing found anywhere.
        """
        providers = self._provider_order(primary_provider)
        logger.info(f"[fallback] Search '{title}' — provider order: {providers}")
        for index, provider in enumerate(providers):
            label = "primary" if index == 0 else "fallback"
            logger.info(f"[fallback] Trying '{provider}' for '{title}'")
            payload = self._search_and_build_payload(title, provider, **kwargs)
            if payload:
                logger.info(
                    f"[fallback] Found on {label} '{provider}': "
                    f"name='{payload.get('name')}' year={payload.get('year')}"
                )
                logger.debug(f"[fallback] Payload dump: {json.dumps(payload, default=str, ensure_ascii=False)}")
                return payload, provider
            logger.warning(f"[fallback] '{title}' not found on '{provider}' either")

        logger.error(f"[fallback] '{title}' not found on any provider (tried: {providers})")
        return None, primary_provider or (providers[0] if providers else "")

    def _provider_order(self, primary_provider: str) -> List[str]:
        """Return primary provider followed by configured ARR provider fallbacks."""
        conf_path = pathlib.Path(__file__).parent.parent.parent.parent / "Conf" / "config.json"
        try:
            with open(conf_path, encoding="utf-8") as _f:
                fallback_list: list = json.load(_f).get("ARR", {}).get("provider_fallback", [])
        except Exception as _exc:
            logger.warning(f"[fallback] Could not read provider_fallback from config: {_exc}")
            fallback_list = []

        providers = []
        if primary_provider:
            providers.append(primary_provider)
        providers.extend(provider for provider in fallback_list if provider and provider not in providers)
        if not providers:
            providers = ["streamingcommunity"]
        return providers

    def _search_with_fallback_titles(
        self,
        titles: List[str],
        primary_provider: str,
        **kwargs,
    ) -> Tuple[Optional[Dict[str, Any]], str, str]:
        """Try title candidates inside each provider, preserving provider order.

        Returns the accepted payload, provider, and title that produced the
        match. If every candidate fails, payload is None and the returned title
        is the first candidate for logging/path fallback purposes.
        """
        candidates = self._unique_titles(titles)
        providers = self._provider_order(primary_provider)
        logger.info(f"[title_fallback] Provider order: {providers}; title candidates: {candidates}")

        for provider_index, provider in enumerate(providers):
            label = "primary" if provider_index == 0 else "fallback"
            for title in candidates:
                logger.info(f"[title_fallback] Trying {label} '{provider}' with title '{title}'")
                payload = self._search_and_build_payload(
                    title,
                    provider,
                    expected_title=title,
                    **kwargs,
                )
                if payload:
                    logger.info(f"[title_fallback] Accepted title '{title}' on {label} '{provider}'")
                    return payload, provider, title

                logger.warning(f"[title_fallback] No match for title '{title}' on '{provider}'")

        fallback_provider = primary_provider or (providers[0] if providers else "")
        return None, fallback_provider, candidates[0] if candidates else ""

    def _search_and_build_payload(self, title: str, provider: str,
                                  year_range: Optional[str] = None,
                                  expected_title: Optional[str] = None,
                                  expected_year: Optional[int] = None,
                                  tmdb_id: Optional[int] = None,
                                  media_type: str = "tv",
                                  season_number: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Search VibraVid's streaming API for a title and return an item_payload dict.

        Verifies candidate results using TMDB id, media type, title compatibility,
        and year range. Localized title fallback is handled before this method.
        """
        try:
            from searchapp.api import get_api

            api = get_api(provider)

            # Strip accents from search query: à→a, è→e, ì→i, ò→o, ù→u …
            search_query = self._strip_accents(title).strip()
            if search_query != title:
                logger.info(f"[search] Stripped accents: '{title}' → '{search_query}'")

            logger.info(
                f"[search] provider='{provider}' query='{search_query}' "
                f"expected_tmdb={tmdb_id} year_range={year_range}"
            )

            # Search using the normalized title
            results = api.search(search_query)

            if not results:
                logger.warning(f"[search] No results for '{search_query}' on '{provider}'")
                return None

            logger.info(f"[search] {len(results)} result(s) from '{provider}' for '{search_query}':")
            for i, r in enumerate(results[:5]):
                r_tmdb = getattr(r, 'tmdb_id', None) or 'N/A'
                logger.info(f"[search]   [{i}] '{r.name}' ({r.year}) type={r.type} tmdb_id={r_tmdb}")

            # Parse year range into integers
            year_start = None
            year_end = None
            if year_range:
                try:
                    parts = year_range.split("-")
                    year_start = int(parts[0])
                    year_end = int(parts[1])
                except (ValueError, IndexError):
                    logger.debug(f"[search] Could not parse year_range '{year_range}'")

            expected_tmdb_str = str(tmdb_id) if tmdb_id else ""

            best = None
            if media_type == "tv" and provider in {"animeunity", "animeworld"} and season_number:
                try:
                    season_int = int(season_number)
                except (TypeError, ValueError):
                    season_int = 0
                if season_int > 1:
                    expected_season_title = self._normalize_title(f"{title} {season_int}")
                    season_best = next(
                        (
                            r for r in results
                            if self._normalize_title(r.name or "") == expected_season_title
                            or self._normalize_title(r.name or "").startswith(expected_season_title + " ")
                        ),
                        None,
                    )
                    if season_best:
                        best = season_best
                        logger.info(
                            f"[search] ACCEPT '{season_best.name}' — "
                            f"season-specific anime match for S{season_int}"
                        )
                    else:
                        logger.warning(
                            f"[search] No season-specific anime result for S{season_int} "
                            f"on '{provider}', rejecting generic results"
                        )
                        return None

            for r in results:
                if best is not None:
                    break
                r_name = r.name or ""
                r_year = r.year or ""
                r_tmdb = str(getattr(r, 'tmdb_id', '') or '')
                r_type = str(getattr(r, 'type', '') or '').lower()

                if media_type == "tv" and r_type == "movie":
                    logger.warning(
                        f"[type_check] SKIP '{r_name}' ({r_year}) — "
                        "movie result cannot satisfy a Sonarr TV request"
                    )
                    continue

                # ── TMDB ID check (highest priority) ──────────────────────
                if expected_tmdb_str and r_tmdb:
                    if r_tmdb != expected_tmdb_str:
                        logger.warning(
                            f"[tmdb_check] SKIP '{r_name}' ({r_year}) — "
                            f"tmdb_id mismatch: got={r_tmdb} expected={expected_tmdb_str}"
                        )
                        continue
                    best = r
                    logger.info(f"[tmdb_check] MATCH '{r_name}' ({r_year}) — tmdb_id={r_tmdb} ✓")
                    break

                # ── Title compatibility check ──────────────────────────────
                if not self._titles_are_compatible(title, r_name):
                    logger.warning(
                        f"[title_check] SKIP '{r_name}' ({r_year}) — "
                        f"title too different from '{title}'"
                    )
                    continue

                # ── Year range check ──────────────────────────────────────
                if year_start is not None and year_end is not None:
                    if not r_year:
                        # No year on result but title matches well — accept it
                        best = r
                        logger.info(
                            f"[search] ACCEPT '{r_name}' (no year) — "
                            f"title match, year unverifiable"
                        )
                        break
                    try:
                        if not (year_start <= int(r_year) <= year_end):
                            logger.debug(
                                f"[search] SKIP '{r_name}' ({r_year}) — "
                                f"year out of range [{year_start}-{year_end}]"
                            )
                            continue
                    except (ValueError, TypeError):
                        continue

                best = r
                logger.info(
                    f"[search] ACCEPT '{r_name}' ({r_year}) — "
                    f"title+year match (no tmdb_id to verify)"
                )
                break

            # Last-chance fallback: first title-compatible result
            if best is None and results:
                first = results[0]
                f_tmdb = str(getattr(first, 'tmdb_id', '') or '')
                if expected_tmdb_str and f_tmdb and f_tmdb != expected_tmdb_str:
                    logger.error(
                        f"[tmdb_check] HARD REJECT '{first.name}' ({first.year}) on '{provider}' — "
                        f"tmdb_id mismatch: got={f_tmdb} expected={expected_tmdb_str}. "
                        f"Trying next provider."
                    )
                    return None
                if self._titles_are_compatible(title, first.name or ""):
                    f_year = first.year or ""
                    year_ok = True
                    if year_start is not None and year_end is not None and f_year:
                        try:
                            year_ok = year_start <= int(f_year) <= year_end
                        except (ValueError, TypeError):
                            year_ok = False
                    if year_ok:
                        best = first
                        logger.info(
                            f"[search] ACCEPT first result '{first.name}' ({first.year or 'no year'}) — "
                            f"title match fallback"
                        )
                    else:
                        logger.warning(
                            f"[search] SKIP first result '{first.name}' ({first.year}) — "
                            f"year out of range [{year_start}-{year_end}]"
                        )
                else:
                    logger.warning(
                        f"[title_check] SKIP first result '{first.name}' ({first.year}) — "
                        f"title too different from '{title}'"
                    )

            if best is None:
                logger.error(
                    f"[search] No match for '{expected_title or title}' on '{provider}' "
                    f"(year_range={year_range}, expected_tmdb={tmdb_id}). "
                    f"Top result was: '{results[0].name}' ({results[0].year})"
                )
                return None

            # ── ITA preference ────────────────────────────────────────────
            # If download_italian_anime_default=true and the best result is not
            # already an ITA version, look for one among the remaining results.
            _conf_path = pathlib.Path(__file__).parent.parent.parent.parent / "Conf" / "config.json"
            try:
                with open(_conf_path, encoding="utf-8") as _f:
                    _prefer_ita = json.load(_f).get("ARR", {}).get("download_italian_anime_default", True)
            except Exception:
                _prefer_ita = True

            season_int_for_ita = 0
            try:
                season_int_for_ita = int(season_number or 0)
            except (TypeError, ValueError):
                season_int_for_ita = 0

            if _prefer_ita and "(ITA)" not in (best.name or "").upper():
                expected_season_title = (
                    self._normalize_title(f"{title} {season_int_for_ita}")
                    if media_type == "tv" and provider in {"animeunity", "animeworld"} and season_int_for_ita > 1
                    else None
                )
                ita = next(
                    (r for r in results
                     if "(ITA)" in (r.name or "").upper()
                     and self._titles_are_compatible(title, r.name or "")
                     and (
                         expected_season_title is None
                         or self._normalize_title((r.name or "").replace("(ITA)", "")) == expected_season_title
                         or self._normalize_title((r.name or "").replace("(ITA)", "")).startswith(expected_season_title + " ")
                     )),
                    None,
                )
                if ita:
                    logger.info(
                        f"[ita] Preferring ITA version '{ita.name}' "
                        f"over '{best.name}' (download_italian_anime_default=true)"
                    )
                    best = ita
                else:
                    logger.info(f"[ita] No ITA version available, keeping '{best.name}'")

            payload = {**best.__dict__, "is_movie": best.is_movie}
            logger.debug(f"[search] Payload: {json.dumps(payload, default=str, ensure_ascii=False)}")
            return payload

        except Exception as exc:
            logger.error(f"Search failed for '{title}' on {provider}: {exc}")
            return None

    @staticmethod
    def _unique_titles(titles: List[Optional[str]]) -> List[str]:
        """Return non-empty title candidates deduplicated by normalized title.

        Preserves the first occurrence so caller-provided priority order remains
        intact while removing case/accent/punctuation-equivalent duplicates.
        """
        unique: List[str] = []
        seen = set()
        for raw_title in titles:
            candidate = str(raw_title or "").strip()
            if not candidate:
                continue
            key = ArrDownloaderService._normalize_title(candidate)
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(candidate)
        return unique

    def _get_tmdb_title_candidates(self, tmdb_id: Optional[int], media_type: str) -> List[str]:
        """Return localized and alternative TMDB titles for search fallback.

        Includes Italian and English details plus TMDB alternative titles, then
        normalizes/deduplicates them while preserving priority order.
        """
        if not tmdb_id:
            return []

        titles: List[str] = []
        try:
            from VibraVid.provider.tmdb import tmdb_client as tmdb

            endpoint = "movie" if media_type == "movie" else "tv"
            detail_key = "title" if media_type == "movie" else "name"
            for lang in ["it", "en"]:
                try:
                    details = tmdb._make_request(f"{endpoint}/{tmdb_id}", {"language": lang})
                    titles.append(details.get(detail_key, ""))
                except Exception as details_exc:
                    logger.debug(f"Failed to get TMDB {lang} title for {media_type}/{tmdb_id}: {details_exc}")

                try:
                    titles.extend(tmdb.get_alternative_titles(tmdb_id, media_type, lang))
                except Exception as alt_exc:
                    logger.debug(f"Failed to get TMDB {lang} alternative titles for {media_type}/{tmdb_id}: {alt_exc}")
        except Exception as tmdb_exc:
            logger.debug(f"Failed to collect TMDB title candidates for {media_type}/{tmdb_id}: {tmdb_exc}")

        return self._unique_titles(titles)

    def _resolve_sonarr_title(self, title: str, series_id: Optional[int], tmdb_id: Optional[int] = None) -> List[str]:
        """Build ordered Sonarr title candidates for VibraVid searches.

        Starts with Sonarr's originalTitle and localized title, then adds TMDB
        localized/alternative titles, a secondary Sonarr list lookup by
        title/slug/originalTitle, and finally the title from the ARR item.
        """
        titles: List[Optional[str]] = []

        if series_id:
            try:
                series = self.sonarr.get_series_by_id(series_id)
                sonarr_title = series.get("title", "")
                sonarr_original = series.get("originalTitle", "")
                logger.info(
                    f"[_resolve_sonarr_title] Sonarr title='{sonarr_title}', "
                    f"originalTitle='{sonarr_original}'"
                )
                titles.extend([sonarr_original, sonarr_title])
            except Exception as exc:
                logger.debug(f"Sonarr series lookup by ID {series_id} failed: {exc}")

        titles.extend(self._get_tmdb_title_candidates(tmdb_id, "tv"))

        if not self._unique_titles(titles):
            # Secondary lookup: if direct lookup by ID and TMDB lookup are
            # unavailable, scan Sonarr's series list and match by title/slug/originalTitle.
            try:
                series_list = self.sonarr.get_series()
                title_lower = title.lower()
                for s in series_list:
                    s_title = s.get("title", "").lower()
                    s_slug = s.get("titleSlug", "").lower()
                    s_original = s.get("originalTitle", "").lower()
                    if title_lower in (s_title, s_slug, s_original):
                        titles.extend([s.get("originalTitle"), s.get("title")])
                        break
            except Exception as exc:
                logger.debug(f"Sonarr series list fallback failed: {exc}")

        titles.append(title)
        return self._unique_titles(titles)

    def _resolve_radarr_title(self, title: str, movie_id: int, tmdb_id: Optional[int] = None) -> List[str]:
        """Build ordered Radarr title candidates for VibraVid searches.

        Starts with Radarr's originalTitle and localized title, then adds TMDB
        localized/alternative titles and finally the title from the ARR item.
        This replaces the old single-title behavior while keeping the original
        resolver name for callers.
        """
        titles: List[Optional[str]] = []

        try:
            movie = self.radarr.get_movie_by_id(movie_id)
            original = movie.get("originalTitle")
            radarr_title = movie.get("title")
            logger.info(f"[_resolve_radarr_title] Radarr title='{radarr_title}', originalTitle='{original}'")
            titles.extend([original, radarr_title])
        except Exception as exc:
            logger.debug(f"Radarr movie lookup by ID {movie_id} failed: {exc}")

        titles.extend(self._get_tmdb_title_candidates(tmdb_id, "movie"))
        titles.append(title)
        return self._unique_titles(titles)

    @staticmethod
    def _build_year_range(year) -> Optional[str]:
        if not year:
            return None
        try:
            y = int(year)
            now = datetime.datetime.now().year
            if y >= (now - 1):
                return f"{y}-9999"
            else:
                return f"{y}-{y + 1}"
        except (ValueError, TypeError):
            return None

    def _fallback_series_root(self, title: str) -> str:
        from VibraVid.utils import config_manager
        base = config_manager.config.get("OUTPUT", "root_path")
        folder = config_manager.config.get("OUTPUT", "serie_folder_name")
        return str(pathlib.Path(base).joinpath(folder, title))

    def _fallback_movie_root(self, title: str) -> str:
        from VibraVid.utils import config_manager
        base = config_manager.config.get("OUTPUT", "root_path")
        folder = config_manager.config.get("OUTPUT", "movie_folder_name")
        return str(pathlib.Path(base).joinpath(folder, title))

    def _get_vibrativo_serie_output(self, arr_series_path: str, title: str, season_num: int, year: Optional[int] = None) -> str:
        """Compute the VibraVid output path relative to Sonarr's root folder."""
        if not arr_series_path:
            return ""
        try:
            from VibraVid.services._base.tv_display_manager import map_episode_path
            import pathlib

            # Pass the year as string if available to match VibraVid's exact logic
            series_year = str(year) if year else None
            path_components, _ = map_episode_path(series_name=title, series_year=series_year, season_number=season_num)

            if "\\" in arr_series_path:
                root = pathlib.PureWindowsPath(arr_series_path).parent
            else:
                root = pathlib.PurePosixPath(arr_series_path).parent

            # Append ONLY the series folder (path_components[0]), ignoring the season subfolder
            if path_components:
                root = root / path_components[0]

            return str(root)
        except Exception as exc:
            logger.debug(f"Could not compute VibraVid serie output path: {exc}")
        return ""

    def _get_vibrativo_movie_output(self, arr_movie_path: str, title: str, year: Optional[int] = None) -> str:
        """Compute the VibraVid output path relative to Radarr's root folder."""
        if not arr_movie_path:
            return ""
        try:
            from VibraVid.services._base.tv_display_manager import map_movie_path
            import pathlib

            # Pass the year as string if available
            title_year = str(year) if year else None
            path_components, _ = map_movie_path(title_name=title, title_year=title_year)

            if "\\" in arr_movie_path:
                root = pathlib.PureWindowsPath(arr_movie_path).parent
            else:
                root = pathlib.PurePosixPath(arr_movie_path).parent

            for part in path_components:
                root = root / part.strip()  # strip trailing spaces from year-less format
            return str(root)
        except Exception as exc:
            logger.debug(f"Could not compute VibraVid movie output path: {exc}")
        return ""

    def _confirm_episode_import(self, series_id: int, episode_id: int,
                                scan_folders: Optional[list] = None,
                                season_folder: Optional[str] = None) -> bool:
        """Try to import episode files from each candidate folder into Sonarr."""
        # Back-compat: accept the old season_folder kwarg
        if scan_folders is None:
            scan_folders = [season_folder] if season_folder else []

        for folder in scan_folders:
            if not folder:
                continue
            try:
                lookup_items = self.sonarr.manual_import_lookup(folder, series_id=series_id)
                import_payload = []
                for item in lookup_items:
                    path = str(item.get("path", "")).strip()
                    if not path:
                        continue

                    # Sonarr v3 requires seriesId and episodeIds at root level for POST
                    ep_ids = [ep["id"] for ep in item.get("episodes", []) if "id" in ep]
                    if not ep_ids:
                        ep_ids = [episode_id]  # Fallback to the requested episode if not parsed

                    post_item = dict(item)
                    post_item["seriesId"] = series_id
                    post_item["episodeIds"] = ep_ids
                    import_payload.append(post_item)

                if import_payload:
                    self.sonarr.manual_import(import_payload)
                    logger.info(f"Manual import submitted for {len(import_payload)} file(s) from '{folder}'")
                    break
            except Exception as exc:
                logger.warning(f"Sonarr manual import from '{folder}' failed: {exc}")

        # Verify import state: episode must have an attached file id.
        for _ in range(24):  # Wait up to 120 seconds
            try:
                episode = self.sonarr.get_episode(episode_id)
                if episode.get("hasFile") or episode.get("episodeFileId"):
                    return True
            except Exception as exc:
                logger.warning(f"Failed to verify Sonarr episode import: {exc}")
            time.sleep(5)

        return False

    def _confirm_movie_import(self, movie_id: int,
                              scan_folders: Optional[list] = None,
                              movie_root: Optional[str] = None) -> bool:
        """Try to import movie files from each candidate folder into Radarr."""
        # Back-compat: accept the old movie_root kwarg
        if scan_folders is None:
            scan_folders = [movie_root] if movie_root else []

        for folder in scan_folders:
            if not folder:
                continue
            try:
                lookup_items = self.radarr.manual_import_lookup(folder, movie_id=movie_id)
                import_payload = []
                for item in lookup_items:
                    path = str(item.get("path", "")).strip()
                    if not path:
                        continue

                    post_item = dict(item)
                    post_item["movieId"] = movie_id
                    import_payload.append(post_item)

                if import_payload:
                    self.radarr.manual_import(import_payload)
                    logger.info(f"Manual import submitted for {len(import_payload)} file(s) from '{folder}'")
                    break
            except Exception as exc:
                logger.warning(f"Radarr manual import from '{folder}' failed: {exc}")

        # Verify import state: movie must have an attached file id or hasFile=True.
        for _ in range(60):  # Wait up to 300 seconds
            try:
                movie = self.radarr.get_movie_by_id(movie_id)
                if movie.get("hasFile") or movie.get("movieFileId"):
                    return True
            except Exception as exc:
                logger.warning(f"Failed to verify Radarr movie import: {exc}")
            time.sleep(5)
        return False
