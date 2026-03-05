# 3.12.23

import os
import platform

from rich.console import Console

from VibraVid.utils import config_manager


console = Console()
CLEAN = config_manager.config.get_bool('DEFAULT', 'show_message')
SHOW = config_manager.config.get_bool('DEFAULT', 'show_message')


def start_message(clean: bool=True):
    """Display a stylized start message in the console."""
    msg = r'''
[green]→[purple]       ___                                      [yellow]           [purple] _    ___ __              _    ___     __
[green]→[purple]      /   |  ______________ _      ______ ______[yellow]   _  __   [purple]| |  / (_) /_  _________ | |  / (_)___/ /
[green]→[purple]     / /| | / ___/ ___/ __ \ | /| / / __ `/ ___/[yellow]  | |/_/   [purple]| | / / / __ \/ ___/ __ `/ | / / / __  / 
[green]→[purple]    / ___ |/ /  / /  / /_/ / |/ |/ / /_/ / /    [yellow] _>  <     [purple]| |/ / / /_/ / /  / /_/ /| |/ / / /_/ /  
[green]→[purple]   /_/  |_/_/  /_/   \____/|__/|__/\__,_/_/     [yellow]/_/|_|     [purple]|___/_/_.___/_/   \__,_/ |___/_/\__,_/                                                                                                 
    '''

    if CLEAN and clean: 
        os.system("cls" if platform.system() == 'Windows' else "clear")
        # console.clear() DA NON USARE CHE DIO CANE CREA PROBLEMI
    
    if SHOW:
        console.print(f"[purple]{msg}")