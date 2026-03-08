# 11.02.25

import os
import inspect

from VibraVid.utils import config_manager
from .site_loader import folder_name as lazy_loader_folder


def get_site_name_from_stack():
    for frame_info in inspect.stack():
        file_path = frame_info.filename
        
        if f"{lazy_loader_folder}{os.sep}" in file_path:
            parts = file_path.split(f"{lazy_loader_folder}{os.sep}")
            
            if len(parts) > 1:
                site_name = parts[1].split(os.sep)[0]
                if site_name not in ('_base', 'site_loader', '__pycache__'):
                    return site_name
    
    return None

class SiteConstant:
    @property
    def SITE_NAME(self) -> str:
        return get_site_name_from_stack()
    
    @property
    def ROOT_PATH(self) -> str:
        return config_manager.config.get('OUTPUT', 'root_path')
    
    @property
    def FULL_URL(self) -> str:
        return config_manager.domain.get(self.SITE_NAME, 'full_url').rstrip('/')
    
    @property
    def SERIES_FOLDER(self):
        base_path = self.ROOT_PATH
        serie_folder = config_manager.config.get('OUTPUT', 'serie_folder_name')
        if '%{site_name}' in serie_folder:
            serie_folder = serie_folder.replace('%{site_name}', self.SITE_NAME)
        
        return os.path.join(base_path, serie_folder)
    
    @property
    def MOVIE_FOLDER(self):
        base_path = self.ROOT_PATH
        movie_folder = config_manager.config.get('OUTPUT', 'movie_folder_name')
        if '%{site_name}' in movie_folder:
            movie_folder = movie_folder.replace('%{site_name}', self.SITE_NAME)
        
        return os.path.join(base_path, movie_folder)
    
    @property
    def ANIME_FOLDER(self):
        base_path = self.ROOT_PATH
        anime_folder = config_manager.config.get('OUTPUT', 'anime_folder_name')
        if '%{site_name}' in anime_folder:
            anime_folder = anime_folder.replace('%{site_name}', self.SITE_NAME)
        
        return os.path.join(base_path, anime_folder)

site_constants = SiteConstant()