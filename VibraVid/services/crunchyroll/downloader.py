# 16.03.25

import os
import re
import time
from urllib.parse import urlparse, parse_qs

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import config_manager, os_manager, start_message
from VibraVid.services._base import site_constants, Entries
from VibraVid.services._base.tv_display_manager import map_movie_path, map_episode_path
from VibraVid.services._base.tv_download_manager import process_season_selection, process_episode_download
from VibraVid.core.utils.language import resolve_locale

from VibraVid.core.downloader import DASH_Downloader

from .client import get_playback_session, CrunchyrollClient
from .scrapper import GetSerieInfo


console = Console()
msg = Prompt()
extension_output = config_manager.config.get("PROCESS", "extension")
CR_LICENSE_URL = 'https://www.crunchyroll.com/license/v1/license/widevine'


def _make_dash_audio_track(mpd_url: str, locale: str, headers: dict, license_headers: dict) -> dict:
    """Build an ``other_tracks`` audio entry for DASH_Downloader."""
    return {
        "type":            "audio",
        "manifest":        "dash",
        "url":             mpd_url,
        "language":        locale,
        "headers":         headers,
        "license_url":     CR_LICENSE_URL,
        "license_headers": license_headers,
    }


def _subtitles_to_other_tracks(subtitles: list) -> list:
    tracks = []
    for sub in subtitles or []:
        if not isinstance(sub, dict):
            continue

        sub_url = sub.get("url")
        if not sub_url:
            continue

        track = {"type": "subtitle", "url": sub_url, "language": sub.get("language") or "und", "name": sub.get("label") or sub.get("name") or sub.get("language") or "Subtitle",}
        fmt = str(sub.get("format") or "").strip().lower().lstrip(".")
        if fmt:
            track["extension"] = fmt
            track["format"] = fmt

        if sub.get("closed_caption"):
            track["cc"] = True

        tracks.append(track)

    return tracks


def parse_select_audio_filter(select_audio: str) -> list:
    """
    Parse select_audio config format to extract language codes.

    Config examples:
        "lang='ita|eng':for=best"   → ["it-IT", "en-US"]
        "lang='it-IT|ar-SA'"        → ["it-IT", "ar-SA"]
        "for=all"                   → []  (use all available tracks)
        ""                          → []  (no filter)

    Returns:
        List of resolved locales (e.g., ["it-IT", "en-US"])
        Empty list = no filter / use all tracks
    """
    if not select_audio:
        return []

    select_audio = select_audio.strip()

    # "for=all" → no filter
    if "for=all" in select_audio.lower():
        return []

    lang_match = re.search(r"lang=['\"]([^'\"]+)['\"]", select_audio)

    if lang_match:
        raw_codes = [c.strip() for c in lang_match.group(1).split('|') if c.strip()]
    else:
        if "=" not in select_audio:
            raw_codes = [c.strip() for c in select_audio.split('|') if c.strip()]
        else:
            return []

    locales = []
    seen = set()
    for code in raw_codes:
        locale = resolve_locale(code)

        if locale is None:
            console.print(f"[yellow]Warning: language code '{code}' not recognised, skipping")
            continue

        if locale not in seen:
            locales.append(locale)
            seen.add(locale)

    console.print(f"[green]Requested audio locales: {locales}")
    return locales


def _build_license_headers(base_headers: dict, content_id: str, mpd_url: str, fallback_token: str) -> dict:
    """Build Widevine license request headers."""
    query_params = parse_qs(urlparse(mpd_url).query)
    playback_guid = (query_params.get('playbackGuid') or [fallback_token])[0]

    headers = base_headers.copy()
    headers.update({
        "x-cr-content-id": content_id,
        "x-cr-video-token": playback_guid,
    })
    return headers


def download_film(select_title: Entries) -> str:
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{select_title.name} \n")

    # Initialize Crunchyroll client
    client = CrunchyrollClient()

    # Define filename and path
    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = os.path.join(site_constants.MOVIE_FOLDER, *path_components) if path_components else site_constants.MOVIE_FOLDER
    movie_name = f"{filename}.{extension_output}"

    # Extract media ID
    url_id = select_title.get('url').split('/')[-1]
    preferred_locales = parse_select_audio_filter(config_manager.config.get("DOWNLOAD", "select_audio", default=""))

    # Resolve all requested locales in a single playback call, then split into main + extras
    main_id = url_id
    extra_audio_tracks = []
    if preferred_locales:
        versions = client.get_versions_by_locales(url_id, preferred_locales)
        main_version = next((v for v in versions if v["audio_locale"] == preferred_locales[0]), None)
        time.sleep(5)  # Small delay to avoid rate limiting between calls

        if main_version:
            main_id = main_version["guid"]

        for v in versions:
            if v["guid"] == main_id:
                continue

            hdrs = client._get_headers()
            extra_license_hdrs = _build_license_headers(hdrs, v["guid"], v["mpd_url"], v.get("token"))
            extra_audio_tracks.append(_make_dash_audio_track(v["mpd_url"], v["audio_locale"], hdrs, extra_license_hdrs))
        
        if extra_audio_tracks:
            console.print(f"[dim]Extra audio: {[v['language'] for v in extra_audio_tracks]}")

    mpd_url, mpd_headers, mpd_list_sub, token, audio_locale = get_playback_session(client, main_id, None)
    license_headers = _build_license_headers(mpd_headers, main_id, mpd_url, token)
    other_tracks = _subtitles_to_other_tracks(mpd_list_sub)
    other_tracks.extend(extra_audio_tracks)

    return DASH_Downloader(
        mpd_url=mpd_url,
        mpd_headers=mpd_headers,
        license_url=CR_LICENSE_URL,
        license_headers=license_headers,
        other_tracks=other_tracks or None,
        output_path=os.path.join(movie_path, movie_name),
    ).start()


def download_episode(obj_episode, index_season_selected, index_episode_selected, scrape_serie, main_guid=None):
    """
    Downloads a specific episode from the specified season.
    """
    start_message()
    client = scrape_serie.client
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    # Define filename and path
    path_components, filename = map_episode_path(scrape_serie.series_name, getattr(scrape_serie, 'year', None), index_season_selected, index_episode_selected, obj_episode.name)
    title_path = os_manager.get_sanitize_path(os.path.join(site_constants.SERIES_FOLDER, *path_components))
    title_name = f"{filename}.{extension_output}"

    # Get media ID and main_guid
    url_id = obj_episode.url.split('/')[-1]
    main_guid = getattr(obj_episode, 'main_guid', None)

    # Parse preferred audio locales (single metadata cache call)
    preferred_locales = parse_select_audio_filter(config_manager.config.get("DOWNLOAD", "select_audio", default=""))
    _, urls_by_locale, _ = scrape_serie._get_episode_audio_locales(url_id) if preferred_locales else ([], {}, None)

    # Determine GUID for primary language
    main_id = url_id
    for locale in preferred_locales:
        if locale in urls_by_locale:
            main_id = urls_by_locale[locale].split('/')[-1]
            break

    # Get playback session for main language
    mpd_url, mpd_headers, mpd_list_sub, token, audio_locale = get_playback_session(client, main_id, main_guid)

    # Build extra audio list (all locales after the first, using cached urls_by_locale)
    extra_audio_tracks = []
    for locale in preferred_locales[1:]:
        if locale not in urls_by_locale:
            console.print(f"[yellow]Locale {locale} not available for this episode")
            continue

        extra_guid = urls_by_locale[locale].split('/')[-1]
        if extra_guid == main_id:
            continue

        try:
            extra_mpd_url, extra_mpd_headers, _, extra_token, _ = get_playback_session(client, extra_guid, None)
            extra_license_hdrs = _build_license_headers(extra_mpd_headers, extra_guid, extra_mpd_url, extra_token)
            extra_audio_tracks.append(_make_dash_audio_track(extra_mpd_url, locale, extra_mpd_headers, extra_license_hdrs))
            time.sleep(5)  # Small delay to avoid rate limiting between calls
        except Exception as e:
            console.print(f"[yellow]Errore fetch audio {locale}: {e}")

    if extra_audio_tracks:
        console.print(f"[green]Extra audio tracks found: {[v['language'] for v in extra_audio_tracks]}")
    else:
        console.print(f"[dim]No extra audio (only {audio_locale})")

    # License headers
    license_headers = _build_license_headers(mpd_headers, main_id, mpd_url, token)
    other_tracks = _subtitles_to_other_tracks(mpd_list_sub)
    other_tracks.extend(extra_audio_tracks)

    return DASH_Downloader(
        mpd_url=mpd_url,
        mpd_headers=mpd_headers,
        license_url=CR_LICENSE_URL,
        license_headers=license_headers,
        other_tracks=other_tracks or None,
        output_path=os.path.join(title_path, title_name)
    ).start()


def download_series(select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie = None) -> None:
    """
    Handle downloading a complete series.

    Parameters:
        - select_season (Entries): Series metadata from search
        - season_selection (str, optional): Pre-defined season selection
        - episode_selection (str, optional): Pre-defined episode selection
        - scrape_serie (Any, optional): Pre-existing scraper instance to avoid recreation
    """
    start_message()
    if not scrape_serie:
        scrape_serie = GetSerieInfo(select_season.url.split("/")[-1])
        scrape_serie.getNumberSeason()
    seasons_count = len(scrape_serie.seasons_manager)

    def download_episode_callback(season_number: int, download_all: bool, episode_selection: str = None):
        """Callback to handle episode downloads for a specific season"""
        def download_video_callback(obj_episode, season_idx, episode_idx):
            return download_episode(obj_episode, season_idx, episode_idx, scrape_serie)
        
        process_episode_download(
            index_season_selected=season_number,
            scrape_serie=scrape_serie,
            download_video_callback=download_video_callback,
            download_all=download_all,
            episode_selection=episode_selection
        )

    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback
    )