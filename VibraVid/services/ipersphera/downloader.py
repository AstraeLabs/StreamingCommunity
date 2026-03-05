# 16.12.25

import os

from bs4 import BeautifulSoup
from rich.console import Console
from rich.prompt import Prompt

from VibraVid.utils import config_manager, start_message
from VibraVid.utils.http_client import create_client_curl, get_headers
from VibraVid.services._base import site_constants, Entries

from VibraVid.core.downloader import MEGA_Downloader


console = Console()
msg = Prompt()
extension_output = config_manager.config.get("PROCESS", "extension")


def download_film(select_title: Entries) -> str:
    """
    Downloads a film using the provided Entries information.
    """
    start_message()
    console.print(f"\n[yellow]Download: [red]{site_constants.SITE_NAME} → [cyan]{select_title.name} \n")
    
    # Extract proton url
    proton_url = None
    try:
        response = create_client_curl(headers=get_headers()).get(select_title.url)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, 'html.parser')
        for link in soup.find_all('a', href=True):
            href = link['href']
            if 'uprot' in href:
                proton_url = href
                break
    
    except Exception as e:
        console.print(f"[red]Site: {site_constants.SITE_NAME}, request error: {e}, get proton URL")
        return None
    
    # Extract mega link
    mega_link = None
    response = create_client_curl(headers=get_headers()).get(str(proton_url).strip())
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')

    for link in soup.find_all('a'):
        href = link['href']
        if 'mega' in href:
            mega_link = href
            break

    # Define the filename and path for the downloaded film
    if select_title.type == "film":
        title_path = os.path.join(site_constants.MOVIE_FOLDER, str(select_title.name).replace(extension_output, ""))
    else:
        title_path = os.path.join(site_constants.SERIES_FOLDER, str(select_title.name).replace(extension_output, ""))

    # Download from MEGA
    mega = MEGA_Downloader(
        choose_files=True
    )
    output_path = mega.download_url(
        url=mega_link,
        dest_path=title_path
    )
    return output_path