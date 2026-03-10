# 10.12.23

import os
import sys
import platform
import argparse
from typing import Callable

from rich.console import Console
from rich.prompt import Prompt

from . import call_global_search
from VibraVid.services._base import load_search_functions
from VibraVid.utils import config_manager, start_message, setup_logger
from VibraVid.utils.hooks import execute_hooks, get_last_hook_context
from VibraVid.upload import git_update, binary_update
from VibraVid.upload.version import __version__, __title__


console = Console()
msg = Prompt()
COLOR_MAP = {
    "anime": "red",
    "film_serie": "yellow", 
    "serie": "blue",
    "film": "green"
}
CATEGORY_MAP = {1: "anime", 2: "Film_serie", 3: "serie", 4: "film"}
CLOSE_CONSOLE = config_manager.config.get_bool('DEFAULT', 'close_console')


def run_function(func: Callable[..., None], search_terms: str = None, selections: dict = None) -> None:
    """Run function once or indefinitely based on close_console flag."""
    if selections:
        func(search_terms, selections=selections)
    else:
        func(search_terms)


def initialize():
    """Initialize the application with system checks and setup."""
    setup_logger()
    start_message(False)

    # Windows 7 terminal size fix
    if platform.system() == "Windows" and "7" in platform.version():
        os.system('mode 120, 40')
    
    # Python version check
    if sys.version_info < (3, 7):
        console.log("[red]Install python version > 3.7.16")
        sys.exit(0)

    # Attempt GitHub update
    try:
        git_update()
    except Exception as e:
        console.log(f"[red]Error with loading github: {str(e)}")


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

    parser.add_argument('-sv', '--s_video', type=str, help='Select video tracks. Example:  1. select best video (best) 2. Select 4K+HEVC video (res="3840*":codecs=hvc1:for=best)')
    parser.add_argument('-sa', '--s_audio', type=str, help='Select audio tracks. Example:  1. Select all (all) 2. Select best eng audio (lang=en:for=best) 3. Select best 2, and language is ja or en (lang="ja|en":for=best2)')
    parser.add_argument('-ss', '--s_subtitle', type=str, help='Select subtitle tracks. Example:  1. Select all subs (all) 2. Select all subs containing "English" (name="English":for=all)')
    parser.add_argument('--auto-select', dest='auto_select', type=str, choices=['true','false'], help='Auto-select streams based on config filters (overrides config). false=interactive selection')

    parser.add_argument('--use_proxy', action='store_true', help='Enable proxy for requests')
    parser.add_argument('--extension', type=str, help='Output file extension (mkv, mp4)')

    parser.add_argument('-UP', '--update', action='store_true', help='Auto-update to latest version (binary only)')
    parser.add_argument('--version', action='version', version=f'{__title__} {__version__}')
    
    # Provider subcommands for DRM helpers
    provider_subparsers = parser.add_subparsers(dest='provider', help='DRM provider commands')

    # PlayReady commands
    pr_parser = provider_subparsers.add_parser('pyplayready', help='PlayReady helper commands')
    pr_sub = pr_parser.add_subparsers(dest='action', help='pyplayready actions')

    pr_create = pr_sub.add_parser('create-device', help='Create a PlayReady device file')
    pr_create.add_argument('-c', '--cert', required=True, help='Group certificate file (e.g., bgroupcert.dat)')
    pr_create.add_argument('-k', '--key', required=True, help='Private key file (e.g., zgpriv.dat)')
    pr_create.add_argument('-o', '--output', required=True, help='Output device file path (device.prd)')

    pr_export = pr_sub.add_parser('export-device', help='Export bgroupcert/zgpriv from a .prd')
    pr_export.add_argument('device', help='Path to .prd file')
    pr_export.add_argument('-d', '--output-dir', default='.', help='Output directory')

    pr_test = pr_sub.add_parser('test', help='Test a .prd against a license server using a PSSH')
    pr_test.add_argument('-D', '--device', required=True, help='Path to .prd device file')

    # Widevine commands
    wv_parser = provider_subparsers.add_parser('pywidevine', help='Widevine helper commands')
    wv_sub = wv_parser.add_subparsers(dest='action', help='pywidevine actions')

    wv_create = wv_sub.add_parser('create-device', help='Create a Widevine device file')
    wv_create.add_argument('private_key', help='Private key PEM file (e.g., private_key.pem)')
    wv_create.add_argument('client_id', help='Client ID binary file (e.g., client_id.bin)')
    wv_create.add_argument('-o', '--output', required=True, help='Output device file path (device.wvd)')
    wv_create.add_argument('-t', '--security-level', choices=['L1', 'L3'], default='L1', help='Security level (L1 or L3)')

    wv_export = wv_sub.add_parser('export-device', help='Export private key and client id from a .wvd')
    wv_export.add_argument('device', help='Path to .wvd file')
    wv_export.add_argument('-d', '--output-dir', default='.', help='Output directory')

    wv_test = wv_sub.add_parser('test', help='Test a .wvd device (demo)')
    wv_test.add_argument('device', help='Path to .wvd device file')
    wv_test.add_argument('--privacy', action='store_true', help='Enable privacy mode for Widevine test')

    wv_migrate = wv_sub.add_parser('migrate', help='Migrate a .wvd device to latest format')
    wv_migrate.add_argument('input', help='Input .wvd path')
    wv_migrate.add_argument('-o', '--output', required=False, help='Output path (default: input.v2.wvd)')

    return parser


def apply_config_updates(args):
    """Apply command line arguments to configuration."""
    config_updates = {}
    arg_mappings = {
        's_video': 'DOWNLOAD.select_video',
        's_audio': 'DOWNLOAD.select_audio',
        's_subtitle': 'DOWNLOAD.select_subtitle',
        'auto_select': 'DOWNLOAD.auto_select',
        'use_proxy': 'REQUESTS.use_proxy',
        'extension': 'PROCESS.extension',
        'close_console': 'DEFAULT.close_console'
    }

    for arg_name, config_key in arg_mappings.items():
        val = getattr(args, arg_name, None)
        if val is None:
            continue

        # convert boolean-like strings
        if arg_name in ('close_console', 'auto_select') and isinstance(val, str):
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
        available_sites = ", ".join(sorted(module_name_to_function.keys()))
        console.print(f"[red]Unknown site: '{args.site}'. Available: [yellow]{available_sites}")
        return False
    
    # Handle auto-first option
    if args.auto_first and search_terms:
        try:
            database = func_to_run(search_terms, get_onlyDatabase=True)
            if database and hasattr(database, 'media_list') and database.media_list:
                first_item = database.media_list[0]
                item_dict = first_item.__dict__.copy() if hasattr(first_item, '__dict__') else {}
                func_to_run(direct_item=item_dict, selections=selections)
                return True
            else:
                console.print("[yellow]No results found. Falling back to interactive mode.")
        except Exception as e:
            console.print(f"[red]Auto-first failed: {str(e)}")
    
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


def main():
    execute_hooks('pre_run')
    initialize()

    try:
        search_functions = load_search_functions()
        parser = setup_argument_parser(search_functions)
        args = parser.parse_args()

        # Handle provider subcommands (pywidevine / pyplayready)
        provider = getattr(args, 'provider', None)
        if provider:
            action = getattr(args, 'action', None)

            if provider == 'pywidevine':
                from VibraVid.cli.command.create_device import (create_widevine_device, export_wvd_device, test_device as wv_test, migrate_device)

                if action == 'create-device':
                    return create_widevine_device(args)
                if action == 'export-device':
                    return export_wvd_device(args.device, getattr(args, 'output_dir', '.'))
                if action == 'test':
                    return wv_test(args.device, getattr(args, 'privacy', False))
                if action == 'migrate':
                    return migrate_device(args.input, getattr(args, 'output', None))

            if provider == 'pyplayready':
                from VibraVid.cli.command.create_device import (create_playready_device, export_prd_device, test_playready_device)
                
                if action == 'create-device':
                    return create_playready_device(args)
                if action == 'export-device':
                    return export_prd_device(args.device, getattr(args, 'output_dir', '.'))
                if action == 'test':
                    return test_playready_device(args)
        
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
        if args.season is not None or args.episode is not None:
            selections = {}
            if args.season is not None:
                selections['season'] = args.season
            if args.episode is not None:
                selections['episode'] = args.episode

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