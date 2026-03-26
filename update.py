# 05.08.25

import os
import shutil
import argparse
from io import BytesIO
from zipfile import ZipFile
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.prompt import Prompt

from VibraVid.upload.version import __author__, __title__
from VibraVid.utils.http_client import create_client


console = Console()
local_path = os.path.join(".")
PROJECT_MARKER_FILE = "update.py"
KEEP_FOLDERS = {"Video", "Conf", ".git"}        # ADD MORE FOLDERS HERE IF NEEDED
KEEP_FILES = {"update.py"}                      # ADD MORE FILES HERE IF NEEDED


def verify_project_directory(directory: str) -> bool:
    """
    Verify that we're in the correct project directory by checking for required files.

    Parameters:
    - directory (str): The path to verify

    Returns:
    - bool: True if this appears to be the correct project directory
    """
    if not os.path.isdir(directory):
        console.print(f"[red]Error: '{directory}' is not a valid directory.")
        return False

    marker_path = os.path.join(directory, PROJECT_MARKER_FILE)
    if not os.path.exists(marker_path):
        console.print(f"[red]Safety check failed: '{PROJECT_MARKER_FILE}' not found in {os.path.abspath(directory)}")
        console.print("[yellow]Please ensure you're running this script from the project root directory.")
        return False

    return True


def dry_run_deletion(directory: str) -> list:
    """
    Simulate what would be deleted without actually deleting anything.

    Parameters:
    - directory (str): The path to the directory

    Returns:
    - list: Items that would be deleted
    """
    if not os.path.exists(directory) or not os.path.isdir(directory):
        return []

    items_to_delete = []
    for item in os.listdir(directory):
        if item in KEEP_FOLDERS or item in KEEP_FILES:
            continue
        item_path = os.path.join(directory, item)
        item_type = "directory" if os.path.isdir(item_path) else "file"
        items_to_delete.append((item, item_type, item_path))

    return items_to_delete


def move_content(source: str, destination: str):
    """
    Move all content from the source folder to the destination folder.

    Parameters:
    - source (str): The path to the source folder.
    - destination (str): The path to the destination folder.
    """
    os.makedirs(destination, exist_ok=True)

    for element in os.listdir(source):
        source_path = os.path.join(source, element)
        destination_path = os.path.join(destination, element)

        if os.path.isdir(source_path):
            move_content(source_path, destination_path)
        else:
            shutil.move(source_path, destination_path)


def keep_specific_items(directory: str, dry_run: bool = False):
    """
    Deletes all items in the given directory except for the preserved folders,
    preserved files, and the '.git' directory.

    Parameters:
    - directory (str): The path to the directory.
    - dry_run (bool): If True, only show what would be deleted without deleting.
    """
    if not verify_project_directory(directory):
        return False

    for item in os.listdir(directory):
        if item in KEEP_FOLDERS or item in KEEP_FILES:
            continue

        item_path = os.path.join(directory, item)
        try:
            if dry_run:
                item_type = "directory" if os.path.isdir(item_path) else "file"
                console.log(f"[yellow][DRY RUN] Would remove {item_type}: {item_path}")
            else:
                if os.path.isdir(item_path):
                    shutil.rmtree(item_path)
                    console.log(f"[green]Removed directory: {item_path}")
                elif os.path.isfile(item_path):
                    os.remove(item_path)
                    console.log(f"[green]Removed file: {item_path}")
        except Exception as e:
            console.log(f"[yellow]Skipping {item_path} due to error: {e}")

    return True


def backup_preserved_folders() -> dict:
    """
    Backup the content of all preserved folders and files to a temporary location.

    Returns:
    - dict: A mapping of original paths to backup paths
    """
    backups = {}
    backup_base = os.path.join(os.path.dirname(os.path.abspath(".")), "_update_backup_temp")
    os.makedirs(backup_base, exist_ok=True)

    for folder in KEEP_FOLDERS - {".git"}:
        folder_path = os.path.join(".", folder)
        if os.path.isdir(folder_path):
            backup_path = os.path.join(backup_base, folder)
            try:
                shutil.copytree(folder_path, backup_path)
                backups[folder] = backup_path
                console.log(f"[cyan]Backed up folder '{folder}' ({sum(len(files) for _, _, files in os.walk(folder_path))} files)")
            except Exception as e:
                console.log(f"[yellow]Could not backup folder '{folder}': {e}")

    for file in KEEP_FILES:
        file_path = os.path.join(".", file)
        if os.path.isfile(file_path):
            backup_path = os.path.join(backup_base, file)
            try:
                shutil.copy2(file_path, backup_path)
                backups[file] = backup_path
                console.log(f"[cyan]Backed up file '{file}'")
            except Exception as e:
                console.log(f"[yellow]Could not backup file '{file}': {e}")

    return backups, backup_base


def restore_preserved_items(backups: dict, backup_base: str):
    """
    Restore all previously backed up preserved folders and files.

    Parameters:
    - backups (dict): Mapping of item names to their backup paths
    - backup_base (str): The temporary backup directory
    """
    for item_name, backup_path in backups.items():
        restore_path = os.path.join(".", item_name)
        try:
            if os.path.isdir(backup_path):
                if os.path.exists(restore_path):
                    shutil.rmtree(restore_path)
                shutil.copytree(backup_path, restore_path)
                console.log(f"[green]Restored folder '{item_name}'")
            elif os.path.isfile(backup_path):
                shutil.copy2(backup_path, restore_path)
                console.log(f"[green]Restored file '{item_name}'")
        except Exception as e:
            console.log(f"[yellow]Could not restore '{item_name}': {e}")

    # Clean up temp backup directory
    try:
        shutil.rmtree(backup_base)
        console.log("[cyan]Cleaned up temporary backup directory.")
    except Exception as e:
        console.log(f"[yellow]Could not clean up backup directory '{backup_base}': {e}")


def print_commit_info(commit_info: dict):
    """
    Print detailed information about the commit in a formatted table.

    Parameters:
    - commit_info (dict): The commit information from GitHub API
    """
    table = Table(show_header=False)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="yellow")

    commit = commit_info['commit']
    commit_date = datetime.strptime(commit['author']['date'], "%Y-%m-%dT%H:%M:%SZ")
    formatted_date = commit_date.strftime("%Y-%m-%d %H:%M:%S")

    table.add_row("Repository", f"{__author__}/{__title__}")
    table.add_row("Commit SHA", commit_info['sha'][:8])
    table.add_row("Author", f"{commit['author']['name']} <{commit['author']['email']}>")
    table.add_row("Date", formatted_date)
    table.add_row("Committer", f"{commit['committer']['name']} <{commit['committer']['email']}>")
    table.add_row("Message", commit['message'])

    if 'stats' in commit_info:
        stats = commit_info['stats']
        table.add_row("Changes", f"+{stats['additions']} -[red]{stats['deletions']} ({stats['total']} total)")

    table.add_row("HTML URL", commit_info['html_url'])
    console.print(Panel.fit(table))


def download_and_extract_latest_commit():
    """
    Download and extract the latest commit from a GitHub repository.
    """
    try:
        api_url = f'https://api.github.com/repos/{__author__}/{__title__}/commits?per_page=1'
        console.log("[green]Requesting latest commit from GitHub repository...")

        headers = {
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': f'{__title__}-updater'
        }
        response = create_client(headers=headers).get(api_url)

        if response.status_code == 200:
            commit_info = response.json()[0]
            commit_sha = commit_info['sha']

            print_commit_info(commit_info)
            zipball_url = f'https://github.com/{__author__}/{__title__}/archive/{commit_sha}.zip'
            console.log("[green]Downloading latest commit zip file...")

            response = create_client().get(zipball_url)
            temp_path = os.path.join(os.path.dirname(os.getcwd()), 'temp_extracted')

            with ZipFile(BytesIO(response.content)) as zip_ref:
                zip_ref.extractall(temp_path)

            console.log("[green]Extracting files...")

            for item in os.listdir(temp_path):
                item_path = os.path.join(temp_path, item)
                destination_path = os.path.join(local_path, item)
                shutil.move(item_path, destination_path)

            shutil.rmtree(temp_path)

            new_folder_name = f"{__title__}-{commit_sha}"
            move_content(new_folder_name, ".")
            shutil.rmtree(new_folder_name)

            console.log("[cyan]Latest commit downloaded and extracted successfully.")
        else:
            console.log(f"[red]Failed to fetch commit information. Status code: {response.status_code}")
    except Exception as e:
        console.print(f"[red]An unexpected error occurred: {e}")


def main_upload(auto_confirm=None, dry_run=False):
    """
    Main function to update to the latest commit of a GitHub repository with safety checks.

    Parameters:
    - auto_confirm (str or None): 'y', 'n', or None for interactive prompt
    - dry_run (bool): If True, only show what would be deleted
    """
    if not verify_project_directory("."):
        console.print("[red]Aborted: Cannot verify project directory. Refusing to proceed.")
        return

    current_dir = os.path.abspath(".")
    console.print(f"[cyan]Project directory: {current_dir}")

    # Show what will be preserved
    console.print("\n[green]Items that will be PRESERVED:")
    for folder in sorted(KEEP_FOLDERS - {".git"}):
        folder_path = os.path.join(".", folder)
        status = "[green]found" if os.path.isdir(folder_path) else "[yellow]not found (will be skipped)"
        console.print(f"  [cyan]📁 folder: {folder} {status}")
        
    for file in sorted(KEEP_FILES):
        file_path = os.path.join(".", file)
        status = "[green]found" if os.path.isfile(file_path) else "[yellow]not found (will be skipped)"
        console.print(f"  [cyan]📄 file: {file} {status}")

    # Show what will be deleted
    items_to_delete = dry_run_deletion(".")
    if items_to_delete:
        console.print("\n[yellow]Files and folders that will be DELETED:")
        for item_name, item_type, item_path in items_to_delete:
            console.print(f"  [red]• {item_type}: {item_name}")
        console.print()

    # First confirmation
    if auto_confirm is None:
        cmd_insert = Prompt.ask(
            "[red]Are you sure you want to proceed with the update?",
            choices=['y', 'n'],
            default='n',
            show_choices=True
        )
    else:
        cmd_insert = auto_confirm

    if cmd_insert.lower().strip() not in ('y', 'yes'):
        console.print("[yellow]Operation cancelled.")
        return

    # Second confirmation
    console.print("\n[red]WARNING: This action cannot be undone!")
    confirmation_phrase = "DELETE MY FILES"
    user_input = Prompt.ask(
        f"[red]Type '{confirmation_phrase}' to confirm deletion",
        default=""
    )

    if user_input.strip() != confirmation_phrase:
        console.print("[yellow]Confirmation phrase incorrect. Operation cancelled.")
        return

    # Dry run mode
    if dry_run:
        console.print("\n[cyan]DRY RUN MODE - No files will be deleted")
        keep_specific_items(".", dry_run=True)
        return

    # Backup preserved folders/files before deletion
    console.print("\n[cyan]Backing up preserved items...")
    backups, backup_base = backup_preserved_folders()

    # Perform actual deletion
    if keep_specific_items("."):
        download_and_extract_latest_commit()
        console.print("\n[cyan]Restoring preserved items...")
        restore_preserved_items(backups, backup_base)
        console.print("[red]Update completed successfully.")
    else:
        console.print("[red]Deletion aborted due to safety check failure.")
        if backups:
            console.print("[cyan]Restoring backups due to failure...")
            restore_preserved_items(backups, backup_base)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Safely update to the latest commit of a GitHub repository.",
        epilog="SAFETY: This script will only work from the project root directory."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting anything."
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Skip first confirmation (you'll still need to type the confirmation phrase)."
    )
    group.add_argument(
        "-n", "--no",
        action="store_true",
        help="Automatically cancel without prompting."
    )

    args = parser.parse_args()

    if args.no:
        main_upload("n")
    elif args.yes:
        main_upload("y", dry_run=args.dry_run)
    else:
        main_upload(dry_run=args.dry_run)