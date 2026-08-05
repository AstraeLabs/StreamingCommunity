# 16.03.25

import os
import time
from urllib.parse import parse_qs, urlparse

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.core.downloader import DASH_Downloader
from VibraVid.core.utils.language import resolve_locale
from VibraVid.core.utils.selector import FilterSpec
from VibraVid.services._base import Entries, anime_folder, movie_folder, site_constants
from VibraVid.services._base.tv_display_manager import map_episode_path, map_movie_path
from VibraVid.services._base.tv_download_manager import process_episode_download, process_season_selection
from VibraVid.utils import config_manager, os_manager, start_message

from .client import CrunchyrollClient, get_episode_chapters, get_playback_session
from .manifest_builder import build_unified_manifest
from .scrapper import GetSerieInfo

console = Console()
msg = Prompt()
extension_output = config_manager.config.get("PROCESS", "extension")
CR_LICENSE_URL = "https://www.crunchyroll.com/license/v1/license/widevine"


def _make_dash_audio_track(mpd_url: str, locale: str, headers: dict, license_headers: dict) -> dict:
    """Build an ``other_tracks`` audio entry for DASH_Downloader."""
    return {
        "type": "audio",
        "manifest": "dash",
        "url": mpd_url,
        "language": locale,
        "headers": headers,
        "license_url": CR_LICENSE_URL,
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

        track = {
            "type": "subtitle",
            "url": sub_url,
            "language": sub.get("language") or "und",
            "name": sub.get("label") or sub.get("name") or sub.get("language") or "Subtitle",
        }
        fmt = str(sub.get("format") or "").strip().lower().lstrip(".")
        if fmt:
            track["extension"] = fmt
            track["format"] = fmt

        if sub.get("closed_caption"):
            track["cc"] = True

        tracks.append(track)

    return tracks


def _merge_subtitles(base: list, extra: list) -> list:
    """Union subtitle lists by (language, closed_caption), preferring entries already in ``base``."""
    merged = list(base or [])
    seen = {(s.get("language"), bool(s.get("closed_caption"))) for s in merged if isinstance(s, dict)}

    for sub in extra or []:
        if not isinstance(sub, dict):
            continue
        key = (sub.get("language"), bool(sub.get("closed_caption")))
        if key in seen:
            continue
        seen.add(key)
        merged.append(sub)

    return merged


def parse_select_audio_filter(select_audio: str) -> list:
    """
    Parse select_audio config format (shared FilterSpec grammar, e.g. "ita|it", "l=ita", "all") to extract language codes.

    Returns:
        List of resolved locales (e.g., ["it-IT", "en-US"])
        Empty list = no filter / use all tracks
    """
    if not select_audio:
        return []

    spec = FilterSpec.parse(select_audio.strip(), "audio")

    # "for=all" / "all" -> no filter, use every track
    if spec.select_all or spec.drop:
        return []

    raw_codes = [c.strip() for c in (spec.langs or "").split("|") if c.strip()]
    if not raw_codes:
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

    return locales


def _build_license_headers(base_headers: dict, content_id: str, mpd_url: str, fallback_token: str) -> dict:
    """Build Widevine license request headers."""
    query_params = parse_qs(urlparse(mpd_url).query)
    playback_guid = (query_params.get("playbackGuid") or [fallback_token])[0]

    headers = base_headers.copy()
    headers.update(
        {
            "x-cr-content-id": content_id,
            "x-cr-video-token": playback_guid,
        }
    )
    return headers


def download_film(select_title: Entries) -> str:
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{select_title.name} \n")

    # Initialize Crunchyroll client
    client = CrunchyrollClient()
    client.clear_all_sessions()  # release anything leaked by a crashed/killed previous run

    # Define filename and path
    path_components, filename = map_movie_path(select_title.name, select_title.year)
    movie_path = movie_folder(*path_components)
    movie_name = f"{filename}.{extension_output}"

    # Extract media ID
    url_id = select_title.get("url").split("/")[-1]
    preferred_locales = parse_select_audio_filter(config_manager.config.get("DOWNLOAD", "select_audio", default=""))

    # Resolve requested locale to GUID using a single slot open, then fetch extras one by one
    main_id = url_id
    extra_audio_tracks = []
    if preferred_locales:
        available = client.get_available_versions(url_id)
        time.sleep(2)

        locale_to_guid = {v["audio_locale"]: v["guid"] for v in available}
        for locale in preferred_locales:
            if locale in locale_to_guid:
                main_id = locale_to_guid[locale]
                break

        for locale in preferred_locales[1:]:
            guid = locale_to_guid.get(locale)
            if not guid or guid == main_id:
                continue
            try:
                time.sleep(2)
                ex_mpd_url, ex_hdrs, _, ex_token, _ = get_playback_session(client, guid, None)
                ex_license_hdrs = _build_license_headers(ex_hdrs, guid, ex_mpd_url, ex_token)
                extra_audio_tracks.append(_make_dash_audio_track(ex_mpd_url, locale, ex_hdrs, ex_license_hdrs))
            except Exception as e:
                console.print(f"[yellow]Error fetching audio {locale}: {e}")

        if extra_audio_tracks:
            console.print(f"[dim]Extra audio: {[v['language'] for v in extra_audio_tracks]}")

    mpd_url, mpd_headers, mpd_list_sub, token, audio_locale = get_playback_session(client, main_id, None)
    license_headers = _build_license_headers(mpd_headers, main_id, mpd_url, token)
    other_tracks = _subtitles_to_other_tracks(mpd_list_sub)
    other_tracks.extend(extra_audio_tracks)
    client.close()

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
    client.clear_all_sessions()  # release anything leaked by a crashed/killed previous run
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} -> [cyan]{scrape_serie.series_name} [white]\\ [magenta]{obj_episode.name} ([cyan]S{index_season_selected}E{index_episode_selected}) \n")

    path_components, filename = map_episode_path(
        scrape_serie.series_name,
        getattr(scrape_serie, "year", None),
        index_season_selected,
        index_episode_selected,
        obj_episode.name,
    )
    title_path = os_manager.get_sanitize_path(anime_folder(*path_components))
    title_name = f"{filename}.{extension_output}"

    # Get media ID and main_guid
    url_id = obj_episode.url.split("/")[-1]
    main_guid = getattr(obj_episode, "main_guid", None)

    # Parse preferred audio locales (single metadata cache call)
    preferred_locales = parse_select_audio_filter(config_manager.config.get("DOWNLOAD", "select_audio", default=""))
    _, urls_by_locale, resolved_main_guid = scrape_serie._get_episode_audio_locales(url_id) if preferred_locales else ([], {}, None)
    main_guid = main_guid or resolved_main_guid

    # Determine GUID for primary language
    main_id = url_id
    main_locale = None
    for locale in preferred_locales:
        if locale in urls_by_locale:
            main_id = urls_by_locale[locale].split("/")[-1]
            main_locale = locale
            break

    # Get playback session for main language (main_guid unlocks the complete subtitle set)
    mpd_url, mpd_headers, mpd_list_sub, token, audio_locale = get_playback_session(client, main_id, main_guid)
    all_subtitles = _merge_subtitles(mpd_list_sub, [])
    license_headers = _build_license_headers(mpd_headers, main_id, mpd_url, token)

    # Fetch extra audio tracks for other preferred locales
    extra_locales = []
    for locale in preferred_locales[1:]:
        if locale not in urls_by_locale:
            console.print(f"[yellow]Locale {locale} not available for this episode")
            continue

        extra_guid = urls_by_locale[locale].split("/")[-1]
        if extra_guid == main_id:
            continue

        try:
            extra_mpd_url, extra_mpd_headers, extra_subs, extra_token, _ = get_playback_session(client, extra_guid, None)
            extra_license_hdrs = _build_license_headers(extra_mpd_headers, extra_guid, extra_mpd_url, extra_token)
            extra_locales.append(
                {
                    "locale": locale,
                    "mpd_url": extra_mpd_url,
                    "headers": extra_mpd_headers,
                    "license_headers": extra_license_hdrs,
                }
            )
            all_subtitles = _merge_subtitles(all_subtitles, extra_subs)
            time.sleep(5)  # Small delay to avoid rate limiting between calls
        except Exception as e:
            console.print(f"[yellow]Errore fetch audio {locale}: {e}")

    if not extra_locales:
        console.print(f"[dim]No extra audio (only {audio_locale})")

    manifest_json, merged_keys = build_unified_manifest(
        main_locale=main_locale or audio_locale or main_id,
        main_mpd_url=mpd_url,
        main_mpd_headers=mpd_headers,
        main_license_headers=license_headers,
        extra_locales=extra_locales,
        subtitles=all_subtitles,
        license_url=CR_LICENSE_URL,
    )

    if not manifest_json:
        console.print("[red]Could not build a unified manifest for this episode, aborting.")
        return None, True, "Could not build unified manifest"

    chapters = get_episode_chapters(client, url_id)

    return DASH_Downloader(
        mpd_url=mpd_url,
        mpd_content=manifest_json,
        mpd_headers=mpd_headers,
        key=merged_keys,
        license_url=CR_LICENSE_URL,
        chapters=chapters or None,
        output_path=os.path.join(title_path, title_name),
    ).start()


def download_series(
    select_season: Entries, season_selection: str = None, episode_selection: str = None, scrape_serie=None
) -> None:
    """
    Handle downloading a complete series.
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
            episode_selection=episode_selection,
        )

    process_season_selection(
        scrape_serie=scrape_serie,
        seasons_count=seasons_count,
        season_selection=season_selection,
        episode_selection=episode_selection,
        download_episode_callback=download_episode_callback,
    )
