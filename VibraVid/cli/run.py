# 10.12.23

import os
import re
import sys
import json
import logging
import argparse
import subprocess
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import config_manager, start_message, setup_logger, get_log_file_path
from VibraVid.core.ui.tracker import context_tracker
from VibraVid.services._base import load_search_functions
from VibraVid.utils.hooks import execute_hooks, get_last_hook_context
from VibraVid.upload import git_update, binary_update
from VibraVid.setup.system import _initialize_paths
from VibraVid.setup.system import (get_ffmpeg_path, get_ffprobe_path, get_bento4_decrypt_path, get_mp4dump_path, get_wvd_path, get_prd_path, get_shaka_packager_path, get_dovi_tool_path, get_mkvmerge_path, get_mkvpropedit_path, get_velora_path)
from VibraVid.setup.binary_paths import binary_paths
from VibraVid.upload.version import __version__, __title__

from VibraVid.cli.command.global_search import global_search as call_global_search
from VibraVid.cli.command.download import handle_direct_download


console = Console()
msg = Prompt()
logger = logging.getLogger(__name__)
COLOR_MAP = {
    "anime": "red", 
    "film_serie": "yellow", 
    "serie": "green", 
    "song": "grey35"
}
CATEGORY_MAP = {
    1: "anime", 
    2: "Film_serie", 
    3: "serie", 
    5: "song"
}
CLOSE_CONSOLE = config_manager.config.get_bool('DEFAULT', 'close_console')
PERSISTENT_ARGS = {
    'use_proxy', 
    'extension', 
    'close_console'
}
_VERSION_FLAGS = {
    "FFmpeg": ["-version"],
    "FFprobe": ["-version"],
    "Shaka Packager": ["--version"],
    "dovi_tool": ["--version"],
    "mkvmerge": ["--version"],
    "mkvpropedit": ["--version"],
    "Bento4 (mp4decrypt)": [],
    "Bento4 (mp4dump)": [],
}


def run_function(func: Callable[..., None], search_terms: str = None, selections: dict = None) -> None:
    """Run function once or indefinitely based on close_console flag."""
    if selections:
        func(search_terms, selections=selections)
    else:
        func(search_terms)


def force_exit():
    """Force script termination in any context."""
    logger.info("Forcing script termination.")
    sys.exit(0)


def setup_argument_parser(search_functions):
    """Setup and return configured argument parser."""
    module_info = {}
    for func in search_functions.values():
        module_info[func.module_name] = func.indice

    available_names = ", ".join(sorted(module_info.keys()))
    available_indices = ", ".join([f"{idx}={name.capitalize()}" for name, idx in sorted(module_info.items(), key=lambda x: x[1])])

    parser = argparse.ArgumentParser(
        description='Script to download movies, series and anime.',
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=f"Sites by name:  {available_names}\nSites by index: {available_indices}"
    )

    # ── Search & selection
    search_group = parser.add_argument_group('Search & selection')
    search_group.add_argument('-s', '--search', default=None, metavar='QUERY', help='Search terms')
    search_group.add_argument('--site', type=str, metavar='NAME|INDEX', help='Target site (name or index)')
    search_group.add_argument('--global', dest='global_search', action='store_true', help='Search across all sites')
    search_group.add_argument('--category', type=int, metavar='N', help='Category filter for global search\n  1=Anime  2=Movies/Series  3=Series  4=Movies')
    search_group.add_argument('--auto-first', action='store_true', help='Auto-select first result (requires --site and --search)')
    search_group.add_argument('--year', type=str, metavar='RANGE', help='Year filter, e.g. "2020" or "1990-2015"')

    # ── Series navigation
    series_group = parser.add_argument_group('Series navigation')
    series_group.add_argument('--season', type=str, default=None, metavar='SEL', help='Season selection, e.g. "1", "1-3", "*"')
    series_group.add_argument('--episode', type=str, default=None, metavar='SEL', help='Episode selection, e.g. "1", "1-5", "*"')

    # ── Track selection
    track_group = parser.add_argument_group('Track selection')
    track_group.add_argument('-sv', '--video', type=str, metavar='SPEC', help='Video track filter (e.g. "best", "1080p")')
    track_group.add_argument('-sa', '--audio', type=str, metavar='SPEC', help='Audio track filter (e.g. "ita|it")')
    track_group.add_argument('-ss', '--subtitle', type=str, metavar='SPEC', help='Subtitle track filter (e.g. "ita|eng")')

    # ── Download options
    dl_opts = parser.add_argument_group('Download options')
    dl_opts.add_argument('--extension', type=str, metavar='EXT', help='Output container (mkv, mp4)')
    dl_opts.add_argument('--use_proxy', action='store_true', help='Route requests through configured proxy')
    dl_opts.add_argument('--skip-ts', dest='skip_ts', action='store_const', const=True, default=None, help='Skip TS/CAM releases (StreamingCommunity)')
    dl_opts.add_argument('--close-console', dest='close_console', type=str, choices=['true', 'false'], metavar='true|false', help='Exit after last download (overrides config)')

    # ── Direct download
    dl_group = parser.add_argument_group('Direct download (--down)')
    dl_group.add_argument('--down', metavar='URL', help='Stream URL to download directly (MP4 / HLS / DASH / ISM)')
    dl_group.add_argument('-o', '--output', metavar='PATH', help='Output file path (extension auto-appended if omitted)')
    dl_group.add_argument('--headers', action='append', metavar='Key:Value', help='HTTP header. Repeatable.')
    dl_group.add_argument('--license-url', dest='license_url', metavar='URL', help='DRM license server URL (Widevine / PlayReady)')
    dl_group.add_argument('--license-headers', dest='license_headers', action='append', metavar='Key:Value', help='HTTP header for DRM license request. Repeatable.')
    dl_group.add_argument('--key', action='append', metavar='KID:KEY', help='Decryption key in KID:KEY hex format. Repeatable.')
    dl_group.add_argument('--drm', choices=['widevine', 'playready', 'auto'], default='auto', help='DRM system (default: auto)')
    dl_group.add_argument('--max-segments', dest='max_segments', type=int, default=None, metavar='N', help='Limit download to first N segments (HLS/DASH/ISM)')
    dl_group.add_argument('--max-time', dest='max_time', type=str, default=None, metavar='HH:MM:SS|SEC', help='Limit downloaded duration, e.g. "00:05:00" or 300 (HLS/DASH/ISM)')

    # ── Utility
    util_group = parser.add_argument_group('Utility')
    util_group.add_argument('--no-log', action='store_true', help='Disable log file for this run')
    util_group.add_argument('-UP', '--update', action='store_true', help='Auto-update to latest version (binary only)')
    util_group.add_argument('--dep', action='store_true', help='Show dependency paths (config, services, binaries)')
    util_group.add_argument('--version', action='version', version=f'{__title__} {__version__}')

    logger.debug("Argument parser set up with available sites and options.")
    return parser


def apply_config_updates(args):
    """Apply command line arguments to configuration."""
    arg_mappings = {
        'video':         'DOWNLOAD.select_video',
        'audio':         'DOWNLOAD.select_audio',
        'subtitle':      'DOWNLOAD.select_subtitle',
        'use_proxy':     'REQUESTS.use_proxy',
        'extension':     'PROCESS.extension',
        'close_console': 'DEFAULT.close_console',
        'skip_ts':       'DEFAULT.skip_ts_versions',
    }

    persistent_updates = {}
    session_updates = {}

    for arg_name, config_key in arg_mappings.items():
        val = getattr(args, arg_name, None)
        if val is None:
            continue

        if arg_name == 'close_console' and isinstance(val, str):
            val = val.lower() == 'true'

        if arg_name in PERSISTENT_ARGS:
            persistent_updates[config_key] = val
        else:
            session_updates[config_key] = val

    for key, value in {**persistent_updates, **session_updates}.items():
        section, option = key.split('.')
        config_manager.config.set_key(section, option, value)

    if persistent_updates:
        config_manager.save_config()


def build_function_mappings(search_functions):
    """Build mappings between indices/names and functions."""
    input_to_function = {}
    choice_labels = {}
    module_name_to_function = {}

    for func in search_functions.values():
        module_name = func.module_name
        site_index = str(func.indice)
        input_to_function[site_index] = func
        choice_labels[site_index] = (module_name.capitalize(), func.use_for.lower())
        module_name_to_function[module_name.lower()] = func

    logger.debug(f"Built function mappings: {input_to_function.keys()} and module names: {module_name_to_function.keys()}")
    return input_to_function, choice_labels, module_name_to_function


def handle_direct_site_selection(args, input_to_function, module_name_to_function, search_terms, selections=None):
    """Handle direct site selection via command line."""
    if not args.site:
        return False

    site_key = str(args.site).strip().lower()
    func_to_run = input_to_function.get(site_key) or module_name_to_function.get(site_key)

    if func_to_run is None:
        console.print(f"[red]Unknown site: '{args.site}'.")
        logger.warning(f"User provided unknown site: '{args.site}'")
        return False

    # Handle auto-first option
    if args.auto_first and search_terms:
        database = func_to_run(search_terms, get_onlyDatabase=True)
        if database and hasattr(database, 'media_list') and database.media_list:
            logger.info("Auto-first enabled: executing first search result directly.")
            first_item = database.media_list[0]
            item_dict = first_item.__dict__.copy() if hasattr(first_item, '__dict__') else {}
            func_to_run(direct_item=item_dict, selections=selections)
            return True
        else:
            console.print("[yellow]No results found. Falling back to interactive mode.")
            logger.info("Auto-first enabled but no results found for search terms.")

    run_function(func_to_run, search_terms=search_terms, selections=selections)
    return True


def get_user_site_selection(args, choice_labels):
    """Get site selection from user (interactive or category-based)."""
    legend_text = " | ".join([f"[{color}]{cat.capitalize()}[/{color}]" for cat, color in COLOR_MAP.items()])
    console.print(f"\n[cyan]Category: {legend_text}")

    choice_keys = list(choice_labels.keys()) + ["global"]
    site_entries = [
        f"{key}: [{COLOR_MAP.get(label[1], 'white')}]{label[0]}[/{COLOR_MAP.get(label[1], 'white')}]"
        for key, label in choice_labels.items()
    ] + ["[magenta](global) Global[/magenta]"]

    site_rows = [" | ".join(site_entries[i:i + 6]) for i in range(0, len(site_entries), 6)]
    for row in site_rows:
        console.print(row)
    
    console.print()
    return msg.ask("[cyan]Insert site index[/cyan]", choices=choice_keys, default="0", show_choices=False, show_default=False)


def get_logs_directory() -> str:
    """Get the logs directory path."""
    app_base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    logs_dir = Path(os.path.join(app_base_path, ".cache", "logs"))
    return str(logs_dir)


def _extract_version(text: str) -> str:
    """Pull a version-like token (e.g. 6.1.1, v80.0, 1.6.0.0) out of CLI output."""
    lines = text.splitlines()
    for line in lines:
        if "bento4" in line.lower():
            m = re.search(r"v?(\d+(?:\.\d+){1,3})", line)
            if m:
                return m.group(1)
    
    for line in lines:
        if "version" in line.lower():
            m = re.search(r"v?(\d+(?:\.\d+){1,3})", line)
            if m:
                return m.group(1)
    
    m = re.search(r"v?(\d+(?:\.\d+){1,3})", text)
    return m.group(1) if m else ""


def _probe_binary_version(dep_name: str, path: str) -> str:
    """Best-effort version string for an external binary; '' if it cannot be determined."""
    if not path:
        return ""
    
    try:
        if dep_name == "Velora":
            out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5)
            raw = (out.stdout or out.stderr).strip()
            first = raw.splitlines()[0] if raw else ""
            try:
                return str(json.loads(first).get("version", "")) or _extract_version(raw)
            except Exception:
                return _extract_version(raw)

        flags = _VERSION_FLAGS.get(dep_name, ["--version"])
        out = subprocess.run([path, *flags], capture_output=True, text=True, timeout=5)
        return _extract_version(f"{out.stdout}\n{out.stderr}")
    except Exception:
        return ""


def show_dependencies(search_functions):
    """Show all dependency paths: config files, services, and external binaries."""
    console.print(f"  [yellow]Config:[/] [white]{config_manager.config_file_path}[/]")
    console.print(f"  [yellow]Login:[/]  [white]{config_manager.login_file_path}[/]")
    console.print(f"  [yellow]Logs:[/]   [white]{get_logs_directory()}[/]")
    console.print(f"  [yellow]Binary:[/] [white]{binary_paths.get_binary_directory()}[/]")
    console.print()

    console.print("[bold cyan]External Dependencies:")
    deps = {
        "FFmpeg": get_ffmpeg_path(),
        "FFprobe": get_ffprobe_path(),
        "Bento4 (mp4decrypt)": get_bento4_decrypt_path(),
        "Bento4 (mp4dump)": get_mp4dump_path(),
        "Shaka Packager": get_shaka_packager_path(),
        "dovi_tool": get_dovi_tool_path(),
        "mkvmerge": get_mkvmerge_path(),
        "mkvpropedit": get_mkvpropedit_path(),
        "Velora": get_velora_path(),
    }

    for dep_name, dep_path in deps.items():
        status = "[green]OK[/]" if dep_path else "[red]NO[/]"
        path_display = dep_path if dep_path else "[red]Not found[/]"
        version = _probe_binary_version(dep_name, dep_path) if dep_path else ""
        version_display = f" [green](v{version})[/]" if version else ""
        console.print(f"  {status} [yellow]{dep_name}:[/]{version_display} [white]{path_display}[/]")
    console.print()

    console.print("[bold cyan]DRM Device Files:[/]")
    drm_devices = {
        "Widevine": get_wvd_path(),
        "PlayReady": get_prd_path(),
    }
    for device_name, device_path in drm_devices.items():
        status = "[green]OK[/]" if device_path else "[red]NO[/]"
        path_display = device_path if device_path else "[red]Not found[/]"
        console.print(f"  {status} [yellow]{device_name}:[/] [white]{path_display}[/]")


def main():
    try:
        search_functions = load_search_functions()
        parser = setup_argument_parser(search_functions)
        args = parser.parse_args()
        setup_logger(no_log=getattr(args, 'no_log', False))

        if hasattr(args, 'dep') and args.dep:
            show_dependencies(search_functions)
            return

        # Initialize
        _initialize_paths()

        # Check critical dependencies before proceeding
        ffmpeg_path = get_ffmpeg_path()
        ffprobe_path = get_ffprobe_path()
        if not ffmpeg_path or not ffprobe_path:
            missing_tools = []
            if not ffmpeg_path:
                missing_tools.append("FFmpeg")
            if not ffprobe_path:
                missing_tools.append("FFprobe")

            console.print(f"[red]Missing required dependency: {', '.join(missing_tools)}.[/red]")
            logger.error(f"Missing required dependency: {missing_tools}")
            raise SystemExit(1)

        # Execute pre-run hooks with context from post-download if available, otherwise with empty context
        execute_hooks('pre_run')
        start_message(False)

        # Attempt git update but continue even if it fails (e.g., no network, git not available)
        try:
            git_update()
        except Exception as e:
            logger.error(f"Error during git update: {str(e)}")
            console.log(f"[red]Error loading github: {str(e)}")

        # Handle auto-update
        if args.update:
            console.print("\n[cyan]  AUTO-UPDATE MODE")
            logger.info("User initiated auto-update via command line.")
            success = binary_update()

            if success:
                console.print("\n[green]Update process initiated successfully!")
            else:
                console.print("\n[yellow]Update was not performed")
            return

        apply_config_updates(args)

        # Propagate CLI download limits to the service flow
        context_tracker.max_segments = getattr(args, 'max_segments', None)
        context_tracker.max_time = getattr(args, 'max_time', None)

        # ── Direct download (--down) — handled before interactive site selection ──
        if handle_direct_download(args):
            return

        # If we reach this point, we're in interactive mode (either normal or with --site specified)
        close_console_flag = None
        if hasattr(args, 'close_console') and args.close_console is not None:
            close_console_flag = args.close_console.lower() == 'true'
        if close_console_flag is None:
            close_console_flag = config_manager.config.get_bool('DEFAULT', 'close_console')

        # Build selections dictionary from season/episode/year arguments
        selections = None
        if args.season is not None or args.episode is not None or args.year is not None:
            logger.info(f"Building selections from command line arguments: season={args.season}, episode={args.episode}, year={args.year}")
            selections = {}
            if args.season is not None:
                selections['season'] = args.season
            if args.episode is not None:
                selections['episode'] = args.episode
            if args.year is not None:
                selections['year'] = args.year

        if getattr(args, 'global_search', False):
            call_global_search(args.search)
            return

        input_to_function, choice_labels, module_name_to_function = build_function_mappings(search_functions)
        if handle_direct_site_selection(args, input_to_function, module_name_to_function, args.search, selections):
            return

        if not close_console_flag:
            while True:
                category = get_user_site_selection(args, choice_labels)

                if category == "global":
                    logger.info("User selected global search from interactive menu.")
                    call_global_search(args.search)

                if category in input_to_function:
                    logger.info(f"User selected site '{category}' from interactive menu.")
                    run_function(input_to_function[category], search_terms=args.search, selections=selections)

                user_response = msg.ask("\n[cyan]Do you want to perform another search? (y/n)", choices=["y", "n"], default="n")
                if user_response.lower() != 'y':
                    break

            force_exit()

        else:
            category = get_user_site_selection(args, choice_labels)

            if category == "global":
                call_global_search(args.search)

            if category in input_to_function:
                run_function(input_to_function[category], search_terms=args.search, selections=selections)

            force_exit()

    finally:
        log_file_path = get_log_file_path()
        if log_file_path:
            console.print(f"\n[dim]Log: {log_file_path}[/dim]")
        
        logger.info("Script execution completed.")
        execute_hooks('post_run', context=get_last_hook_context('post_download') or get_last_hook_context('post_run'))
