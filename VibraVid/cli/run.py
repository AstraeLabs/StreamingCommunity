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
from VibraVid.utils.upload import git_update, binary_update
from VibraVid.setup.system import _initialize_paths
from VibraVid.setup.system import (get_ffmpeg_path, get_ffprobe_path, get_bento4_decrypt_path, get_wvd_path, get_prd_path, get_shaka_packager_path, get_dovi_tool_path, get_mkvmerge_path, get_mkvpropedit_path, get_velora_path)
from VibraVid.setup.binary_paths import binary_paths
from VibraVid.utils.upload.version import __version__, __title__

from VibraVid.cli.command.global_search import global_search as call_global_search
from VibraVid.cli.command.download import handle_direct_download
from VibraVid.cli.command.equivalent_command import EquivalentCommandBuilder


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
    'proxy_scope',
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
}

_EQUIVALENT_CMD_EXCLUDED_DESTS = {
    'site', 'search', 'item', 'season', 'episode',
    'down', 'stream_type', 'output', 'headers', 'license_url', 'license_headers', 'key',
    'no_log', 'update', 'dep',
}
equivalent_command_builder = EquivalentCommandBuilder(excluded_dests=_EQUIVALENT_CMD_EXCLUDED_DESTS)


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


def _prescan_site_arg(argv):
    """Scan raw argv for --site's value before argparse runs."""
    for i, tok in enumerate(argv):
        if tok == '--site' and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith('--site='):
            return tok.split('=', 1)[1]
    
    return None


def _resolve_site_module(site_value, search_functions):
    """Resolve a --site value (name or index) to its loaded module, or None if no match."""
    if not site_value:
        return None

    key = site_value.strip().lower()
    for func in search_functions.values():
        if key == str(func.indice) or key == func.module_name.lower():
            try:
                return func.get_module()
            except Exception:
                logger.debug(f"Could not eagerly load site module for '{site_value}' while building CLI options", exc_info=True)
                return None
    
    return None


def _has_help_flag(argv):
    """Whether -h/--help was passed (checked before argparse runs, to branch help display)."""
    return any(tok in ('-h', '--help') for tok in argv)


def _print_site_only_help(site_value, site_module):
    """Print ONLY this site's own CLI options (skips the generic parser dump entirely) and exit."""
    register = getattr(site_module, 'register_cli_args', None)
    site_name = getattr(site_module, '__name__', str(site_module)).rsplit('.', 1)[-1]
    mini_parser = argparse.ArgumentParser(
        prog=f'manual.py --site {site_value} ...',
        description=f'Site-specific options for "{site_name}" (--site {site_value})',
        formatter_class=argparse.RawTextHelpFormatter,
    )

    register(mini_parser)
    mini_parser.print_help()
    raise SystemExit(0)


def setup_argument_parser(search_functions, site_module=None, extra_site_modules=None):
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
    search_group.add_argument('--item', type=int, default=None, metavar='N', help='Select the Nth search result directly, 0-based (requires --site and --search)')
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
    dl_opts.add_argument('--use_proxy', action='store_const', const=True, default=None, help='Route requests through configured proxy')
    dl_opts.add_argument('--proxy-scope', dest='proxy_scope', type=str, choices=['scrap', 'down', 'scrap+down'], metavar='scrap|down|scrap+down', help='Where to apply the proxy: scraping only, downloads only, or both')
    dl_opts.add_argument('--skip-ts', dest='skip_ts', action='store_const', const=True, default=None, help='Skip TS/CAM releases (StreamingCommunity)')
    dl_opts.add_argument('--close-console', dest='close_console', type=str, choices=['true', 'false'], metavar='true|false', help='Exit after last download (overrides config)')
    dl_opts.add_argument('--no-vault-cache', dest='bypass_vault_cache', action='store_const', const=True, default=None, help='Bypass DRM key vault cache; force a fresh CDM license request every run (for dynamic/time-sensitive tokens)')

    # ── Direct download
    dl_group = parser.add_argument_group('Direct download (--down)')
    dl_group.add_argument('--down', metavar='URL', help='Stream URL to download directly (MP4 / HLS / DASH / ISM)')
    dl_group.add_argument('--type', dest='stream_type', choices=['auto', 'mp4', 'hls', 'dash', 'ism'], default='auto', help='Force the stream type instead of auto-detecting (default: auto)')
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

    # ── Site-specific options (only added, and thus only shown in --help, when --site targets this module).
    site_option_dests = []
    register = getattr(site_module, 'register_cli_args', None) if site_module else None
    if callable(register):
        try:
            site_option_dests = list(register(parser) or [])
        except Exception:
            logger.warning(f"register_cli_args() failed for site module '{getattr(site_module, '__name__', site_module)}'", exc_info=True)

    extra_help_sections = []
    for extra_module in extra_site_modules or []:
        if extra_module is site_module:
            continue

        extra_register = getattr(extra_module, 'register_cli_args', None)
        if callable(extra_register):
            try:
                mini_parser = argparse.ArgumentParser(add_help=False, formatter_class=argparse.RawTextHelpFormatter, prog='')
                extra_register(mini_parser)
                if any(g._group_actions for g in mini_parser._action_groups):
                    _, _, section = mini_parser.format_help().partition('\n\n')
                    if section:
                        extra_help_sections.append(section.rstrip('\n'))
            except Exception:
                logger.warning(f"register_cli_args() failed for site module '{getattr(extra_module, '__name__', extra_module)}'", exc_info=True)

    if extra_help_sections:
        parser.epilog = "\n\n".join(extra_help_sections) + "\n\n" + parser.epilog

    logger.debug("Argument parser set up with available sites and options.")
    return parser, site_option_dests


def apply_config_updates(args):
    """Apply command line arguments to configuration."""
    arg_mappings = {
        'video':         'DOWNLOAD.select_video',
        'audio':         'DOWNLOAD.select_audio',
        'subtitle':      'DOWNLOAD.select_subtitle',
        'use_proxy':     'REQUESTS.use_proxy',
        'proxy_scope':   'REQUESTS.proxy_scope',
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

    context_tracker.cli_site = args.site

    # Handle auto-first / --item (direct result selection by index, 0 for auto-first)
    requested_index = 0 if args.auto_first else args.item
    if requested_index is not None and search_terms:
        try:
            database = func_to_run(search_terms, get_onlyDatabase=True, selections=selections)
        except Exception as e:
            console.print(f"[yellow]Direct item search failed ({e}). Falling back to interactive mode.")
            logger.warning(f"Direct item search raised an exception, falling back to interactive mode: {e}")
            database = None

        media_list = getattr(database, 'media_list', None) if database else None
        if media_list:
            if 0 <= requested_index < len(media_list):
                logger.info(f"Direct item selection: executing result at index {requested_index} directly.")
                context_tracker.cli_search = search_terms
                context_tracker.cli_item = requested_index
                item = media_list[requested_index]
                item_dict = item.__dict__.copy() if hasattr(item, '__dict__') else {}
                func_to_run(direct_item=item_dict, selections=selections)
                return True
            else:
                console.print(f"[red]--item {requested_index} is out of range (found {len(media_list)} results). Falling back to interactive mode.")
                logger.warning(f"--item {requested_index} out of range for {len(media_list)} results.")
        else:
            console.print("[yellow]No results found. Falling back to interactive mode.")
            logger.info("Direct item selection enabled but no results found for search terms.")

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
        argv = sys.argv[1:]
        search_functions = load_search_functions()
        prescanned_site = _prescan_site_arg(argv)
        help_requested = _has_help_flag(argv)
        site_module = _resolve_site_module(prescanned_site, search_functions) if prescanned_site else None

        # `--site X --help`: show ONLY that site's own options, skip the generic dump entirely.
        if help_requested and site_module is not None and callable(getattr(site_module, 'register_cli_args', None)):
            _print_site_only_help(prescanned_site, site_module)

        # Plain `--help` (no --site, or a site with nothing site-specific to show): the usual
        # generic parser, plus every other site's options aggregated so they're discoverable.
        extra_site_modules = None
        if help_requested and site_module is None:
            extra_site_modules = []
            for func in search_functions.values():
                if not func.has_cli_args:
                    continue
                try:
                    extra_site_modules.append(func.get_module())
                except Exception:
                    logger.debug(f"Could not load site module '{func.module_name}' to list its CLI options for --help", exc_info=True)

        parser, site_option_dests = setup_argument_parser(search_functions, site_module=site_module, extra_site_modules=extra_site_modules)
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
        context_tracker.bypass_vault_cache = getattr(args, 'bypass_vault_cache', None)
        site_options = {'drm': getattr(args, 'drm', None)}
        site_options.update({dest: getattr(args, dest, None) for dest in site_option_dests})
        context_tracker.site_options = site_options

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
            equivalent_command_builder.log_equivalent_command(args, parser, context_tracker, site_option_dests)
            return

        if not close_console_flag:
            while True:
                category = get_user_site_selection(args, choice_labels)

                if category == "global":
                    logger.info("User selected global search from interactive menu.")
                    call_global_search(args.search)

                if category in input_to_function:
                    logger.info(f"User selected site '{category}' from interactive menu.")
                    context_tracker.cli_site = category
                    run_function(input_to_function[category], search_terms=args.search, selections=selections)
                    equivalent_command_builder.log_equivalent_command(args, parser, context_tracker, site_option_dests)

                user_response = msg.ask("\n[cyan]Do you want to perform another search? (y/n)", choices=["y", "n"], default="n")
                if user_response.lower() != 'y':
                    break

            force_exit()

        else:
            category = get_user_site_selection(args, choice_labels)

            if category == "global":
                call_global_search(args.search)

            if category in input_to_function:
                context_tracker.cli_site = category
                run_function(input_to_function[category], search_terms=args.search, selections=selections)
                equivalent_command_builder.log_equivalent_command(args, parser, context_tracker, site_option_dests)

            force_exit()

    finally:
        log_file_path = get_log_file_path()
        if log_file_path:
            console.print(f"\n[dim]Log: {log_file_path}[/dim]")
        
        logger.info("Script execution completed.")
        execute_hooks('post_run', context=get_last_hook_context('post_download') or get_last_hook_context('post_run'))
