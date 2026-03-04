# 03.03.24

import sys
import logging
from typing import Dict, List, Any


# External library
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt
from rich import box


# Internal utilities
from .message import start_message



class TVShowManager:
    def __init__(self):
        """
        Initialize TVShowManager with default values.
        """
        self.console = Console()
        self.tv_shows: List[Dict[str, Any]] = []
        self.slice_start = 0
        self.slice_end = 10
        self.step = self.slice_end
        self.column_info = []

    def add_column(self, column_info: Dict[str, Dict[str, str]]) -> None:
        """
        Add column information.
    
        Parameters:
            - column_info (Dict[str, Dict[str, str]]): Dictionary containing column names, their colors, and justification.
        """
        self.column_info = column_info

    def add_tv_show(self, tv_show: Dict[str, Any]) -> None:
        """
        Add a TV show to the list of TV shows.

        Parameters:
            - tv_show (Dict[str, Any]): Dictionary containing TV show details.
        """
        if tv_show:
            self.tv_shows.append(tv_show)

    def display_data(self, data_slice: List[Dict[str, Any]]) -> None:
        """
        Display TV show data in a tabular format.

        Parameters:
            - data_slice (List[Dict[str, Any]]): List of dictionaries containing TV show details to display.
        """
        if not data_slice:
            logging.error("Nothing to display.")
            return 404
            
        if not self.column_info:
            logging.error("Error: Column information not configured.")
            return 404

        # Create table with specified style
        table = Table(
            box=box.ROUNDED,
            show_header=True,
            header_style="cyan",
            border_style="blue",
            padding=(0, 1)
        )

        # Add columns dynamically based on provided column information
        for col_name, col_style in self.column_info.items():
            color = col_style.get("color", "white")
            width = col_style.get("width", None)
            justify = col_style.get("justify", "center")
            
            table.add_column(
                col_name, 
                style=color,
                justify=justify,
                width=width
            )

        # Add rows dynamically based on available TV show data
        for idx, entry in enumerate(data_slice):
            if entry:
                row_data = [str(entry.get(col_name, '')) for col_name in self.column_info.keys()]
                style = "dim" if idx % 2 == 1 else None
                table.add_row(*row_data, style=style)

        self.console.print(table)

    def run(self, force_int_input: bool = False, max_int_input: int = 0) -> str:
        """
        Run the TV show manager application.

        Parameters:
            - force_int_input(bool): If True, only accept integer inputs from 0 to max_int_input
            - max_int_input (int): range of row to show
        
        Returns:
            str: Last command executed before breaking out of the loop.
        """
        if not self.tv_shows:
            logging.error("Error: No data available for display.")
            sys.exit(0)
            
        if not self.column_info:
            logging.error("Error: Columns not configured.")
            return ""

        total_items = len(self.tv_shows)
        last_command = ""

        while True:
            start_message()
            
            # Check and adjust slice indices if out of bounds
            current_slice = self.tv_shows[self.slice_start:self.slice_end]
            if not current_slice and total_items > 0:
                self.slice_start = 0
                self.slice_end = min(self.step, total_items)
                current_slice = self.tv_shows[self.slice_start:self.slice_end]
            
            result_func = self.display_data(current_slice)
            if result_func == 404:
                sys.exit(1)

            # Handle pagination and user input
            if self.slice_end < total_items:
                self.console.print("\n[green]Press [red]Enter [green]for next page, [red]'q' [green]to quit.")

                if not force_int_input:
                    prompt_msg = ("\n[cyan]Insert media index [yellow](e.g., 1), [red]* [cyan]to download all media, [yellow](e.g., 1-2) [cyan]for a range of media, or [yellow](e.g., 3-*) [cyan]to download from a specific index to the end")
                    key = Prompt.ask(prompt_msg)

                else:
                    # Include empty string in choices to allow pagination with Enter key
                    choices = [""] + [str(i) for i in range(max_int_input + 1)] + ["q", "quit"]
                    prompt_msg = "[cyan]Insert media [red]index"
                    key = Prompt.ask(prompt_msg, choices=choices, show_choices=False)

                last_command = key

                if key.lower() in ["q", "quit"]:
                    break
                elif key == "":
                    self.slice_start += self.step
                    self.slice_end += self.step
                    if self.slice_end > total_items:
                        self.slice_end = total_items
                else:
                    break

            else:
                # Last page handling
                self.console.print("\n[green]You've reached the end. [red]Enter [green]for first page, [red]'q' [green]to quit.")
                
                if not force_int_input:
                    prompt_msg = ("\n[cyan]Insert media index [yellow](e.g., 1), [red]* [cyan]to download all media, [yellow](e.g., 1-2) [cyan]for a range of media, or [yellow](e.g., 3-*) [cyan]to download from a specific index to the end")
                    key = Prompt.ask(prompt_msg)
                else:
                    # Include empty string in choices to allow pagination with Enter key
                    choices = [""] + [str(i) for i in range(max_int_input + 1)] + ["q", "quit"]
                    prompt_msg = "[cyan]Insert media [red]index"
                    key = Prompt.ask(prompt_msg, choices=choices, show_choices=False)

                last_command = key

                if key.lower() in ["q", "quit"]:
                    break

                elif key == "":
                    self.slice_start = 0
                    self.slice_end = self.step
                else:
                    break

        return last_command

    def clear(self) -> None:
        """
        Clear all TV shows data.
        """
        self.tv_shows = []
        self.slice_start = 0
        self.slice_end = self.step