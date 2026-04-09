# 10.12.23

import os
import sys
import argparse
from pathlib import Path
from typing import Callable

from rich.console import Console
from rich.prompt import Prompt

from . import call_global_search
from VibraVid.utils import config_manager, start_message, setup_logger
from VibraVid.services._base import load_search_functions
from VibraVid.utils.hooks import execute_hooks, get_last_hook_context
from VibraVid.upload import git_update, binary_update
from VibraVid.setup.system import _initialize_paths
from VibraVid.setup.system import (get_ffmpeg_path, get_ffprobe_path, get_bento4_decrypt_path, get_mp4dump_path, get_wvd_path, get_prd_path,  get_n_m3u8dl_re_path, get_shaka_packager_path)
from VibraVid.setup.binary_paths import binary_paths
from VibraVid.upload.version import __version__, __title__


console = Console()
msg = Prompt()
COLOR_MAP = {"anime": "#E63946", "film_serie": "#FFD60A", "serie": "#3891C9", "film": "#06A77D"}
CATEGORY_MAP = {1: "anime", 2: "Film_serie", 3: "serie", 4: "film"}
CLOSE_CONSOLE = config_manager.config.get_bool('DEFAULT', 'close_console')


def run_function(func: Callable[..., None], search_terms: str = None, selections: dict = None) -> None:
    """Run function once or indefinitely based on close_console flag."""
    if selections:
        func(search_terms, selections=selections)
    else:
        func(search_terms)


def force_exit():
    """Force script termination in any context."""
    console.print("\n[red]Closing the application...")
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
        epilog=f"Available sites by name: {available_names}\nAvailable sites by index: {available_indices}"
    )
    
    # Add arguments
    parser.add_argument('-s', '--search', default=None, help='Search terms')
    parser.add_argument('--site', type=str, help='Site by name or index')
    parser.add_argument('--category', type=int, help='Category filter for global search (1=Anime, 2=Movies/Series, 3=Series, 4=Movies)')
    parser.add_argument('--global', dest='global_search', action='store_true', help='Global search across sites')
    parser.add_argument('--close-console', dest='close_console', type=str, choices=['true','false'], help='Set whether to exit after last download (overrides config)')

    parser.add_argument('--auto-first', action='store_true', help='Auto-download first result (use with --site and --search)')
    parser.add_argument('--season', type=str, default=None, help='Season selection (for series, e.g., "1" or "1-3" or "*")')
    parser.add_argument('--episode', type=str, default=None, help='Episode selection (for series, e.g., "1" or "1-5" or "*")')
    parser.add_argument('--year', type=str, default=None, help='Year range filter (e.g., "1990-2015" or "2020")')

    parser.add_argument('-sv', '--video', type=str, help='Select video tracks.')
    parser.add_argument('-sa', '--audio', type=str, help='Select audio tracks.')
    parser.add_argument('-ss', '--subtitle', type=str, help='Select subtitle tracks.')

    parser.add_argument('--use_proxy', action='store_true', help='Enable proxy for requests')
    parser.add_argument('--extension', type=str, help='Output file extension (mkv, mp4)')

    parser.add_argument('-UP', '--update', action='store_true', help='Auto-update to latest version (binary only)')
    parser.add_argument('--dep', action='store_true', help='Show all dependency paths (config, services, binaries)')
    parser.add_argument('--version', action='version', version=f'{__title__} {__version__}')
    return parser


def apply_config_updates(args):
    """Apply command line arguments to configuration."""
    config_updates = {}
    arg_mappings = {
        'sv': 'DOWNLOAD.select_video',
        'sa': 'DOWNLOAD.select_audio',
        'ss': 'DOWNLOAD.select_subtitle',
        'use_proxy': 'REQUESTS.use_proxy',
        'extension': 'PROCESS.extension',
        'close_console': 'DEFAULT.close_console'
    }

    for arg_name, config_key in arg_mappings.items():
        val = getattr(args, arg_name, None)
        if val is None:
            continue

        # convert boolean-like strings
        if arg_name in ('close_console') and isinstance(val, str):
            val = val.lower() == 'true'
        config_updates[config_key] = val

    # Apply updates
    for key, value in config_updates.items():
        section, option = key.split('.')
        config_manager.config.set_key(section, option, value)

    if config_updates:
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
    
    return input_to_function, choice_labels, module_name_to_function


def handle_direct_site_selection(args, input_to_function, module_name_to_function, search_terms, selections=None):
    """Handle direct site selection via command line."""
    if not args.site:
        return False
        
    site_key = str(args.site).strip().lower()
    func_to_run = input_to_function.get(site_key) or module_name_to_function.get(site_key)
    
    if func_to_run is None:
        console.print(f"[red]Unknown site: '{args.site}'.")
        return False
    
    # Handle auto-first option
    if args.auto_first and search_terms:
        database = func_to_run(search_terms, get_onlyDatabase=True)
        if database and hasattr(database, 'media_list') and database.media_list:
            first_item = database.media_list[0]
            item_dict = first_item.__dict__.copy() if hasattr(first_item, '__dict__') else {}
            func_to_run(direct_item=item_dict, selections=selections)
            return True
        else:
            console.print("[yellow]No results found. Falling back to interactive mode.")

    run_function(func_to_run, search_terms=search_terms, selections=selections)
    return True


def get_user_site_selection(args, choice_labels):
    """Get site selection from user (interactive or category-based)."""
    legend_text = " | ".join([f"[{color}]{cat.capitalize()}[/{color}]" for cat, color in COLOR_MAP.items()])
    legend_text += " | [magenta]Global[/magenta]"
    console.print(f"\n[cyan]Category Legend: {legend_text}")
    
    choice_keys = list(choice_labels.keys()) + ["global"]
    prompt_message = "[cyan]Insert site: " + ", ".join([
        f"[{COLOR_MAP.get(label[1], 'white')}]({key}) {label[0]}[/{COLOR_MAP.get(label[1], 'white')}]" 
        for key, label in choice_labels.items()
    ]) + ", [magenta](global) Global[/magenta]"
    return msg.ask(prompt_message, choices=choice_keys, default="0", show_choices=False, show_default=False)


def get_logs_directory() -> str:
    """Get the logs directory path."""
    app_base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    logs_dir = Path(os.path.join(app_base_path, ".cache", "logs"))
    return str(logs_dir)


def show_dependencies(search_functions):
    """Show all dependency paths: config files, services, and external binaries."""
    console.print(f"  [yellow]Config:[/] [white]{config_manager.config_file_path}[/]")
    console.print(f"  [yellow]Login:[/]  [white]{config_manager.login_file_path}[/]")
    console.print(f"  [yellow]Logs:[/]   [white]{get_logs_directory()}[/]")
    console.print(f"  [yellow]Binary:[/] [white]{binary_paths.get_binary_directory()}[/]")
    console.print()
    
    console.print("[bold cyan]Available Services:")
    for func in sorted(search_functions.values(), key=lambda x: x.indice):
        if func.source.lower() == "default":
            base_path = func.base_path if func.base_path else "N/A"
            service_path = f"{base_path}\\{func.module_name}" if base_path != "N/A" else "N/A"
        else:
            service_path = f"{func.base_path}\\{func.module_name}" if func.base_path else "N/A"
        
        console.print(f"  [{COLOR_MAP.get(func.use_for, 'white')}][{func.indice}][/] [yellow]{func.module_name.capitalize()}[/]: [white]{service_path}[/]")
    console.print()
    
    console.print("[bold cyan]External Dependencies:")
    deps = {
        "FFmpeg": get_ffmpeg_path(),
        "FFprobe": get_ffprobe_path(),
        "Bento4 (mp4decrypt)": get_bento4_decrypt_path(),
        "Bento4 (mp4dump)": get_mp4dump_path(),
        "N_m3u8DL-RE": get_n_m3u8dl_re_path(),
        "Shaka Packager": get_shaka_packager_path(),
    }
    
    for dep_name, dep_path in deps.items():
        status = "[green]✓[/]" if dep_path else "[red]✗[/]"
        path_display = dep_path if dep_path else "[red]Not found[/]"
        console.print(f"  {status} [yellow]{dep_name}:[/] [white]{path_display}[/]")
    console.print()
    
    console.print("[bold cyan]DRM Device Files:[/]")
    drm_devices = {
        "Widevine (.wvd)": get_wvd_path(),
        "PlayReady (.prd)": get_prd_path(),
    }
    for device_name, device_path in drm_devices.items():
        status = "[green]✓[/]" if device_path else "[red]✗[/]"
        path_display = device_path if device_path else "[red]Not found[/]"
        console.print(f"  {status} [yellow]{device_name}:[/] [white]{path_display}[/]")


def main():
    try:
        search_functions = load_search_functions()
        parser = setup_argument_parser(search_functions)
        args = parser.parse_args()
        if hasattr(args, 'dep') and args.dep:
            show_dependencies(search_functions)
            return

        # Initialize
        setup_logger()
        _initialize_paths()
        execute_hooks('pre_run')
        start_message(False)
        try:
            git_update()
        except Exception as e:
            console.log(f"[red]Error loading github: {str(e)}")

        # Handle auto-update
        if args.update:
            console.print("\n[cyan]  AUTO-UPDATE MODE")
            success = binary_update()
            
            if success:
                console.print("\n[green]Update process initiated successfully!")
            else:
                console.print("\n[yellow]Update was not performed")
            return
        
        apply_config_updates(args)

        # Determine close_console for this run (CLI overrides config)
        close_console_flag = None
        if hasattr(args, 'close_console') and args.close_console is not None:
            close_console_flag = args.close_console.lower() == 'true'
        if close_console_flag is None:
            close_console_flag = config_manager.config.get_bool('DEFAULT', 'close_console')

        # Build selections dictionary from season and episode arguments
        selections = None
        if args.season is not None or args.episode is not None or args.year is not None:
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
                    call_global_search(args.search)

                if category in input_to_function:
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
        execute_hooks('post_run', context=get_last_hook_context('post_download') or get_last_hook_context('post_run'))