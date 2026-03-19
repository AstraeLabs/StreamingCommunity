# 01.10.25 

import os
import sys
import glob
import logging
import importlib
from typing import Dict

from rich.console import Console

from VibraVid.setup import get_is_binary_installation
from VibraVid.utils.os import os_manager


console = Console()
logger = logging.getLogger(__name__)
folder_name = "services"


class LazySearchModule:
    def __init__(self, module_name: str, indice: int, use_for: str = None):
        """
        Lazy loader for a search module.

        Args:
            module_name: Name of the site module (e.g., 'streamingcommunity')
            indice: Sort index for the module
            use_for: Content types this module supports
        """
        self.module_name = module_name
        self.indice = indice
        self._module = None
        self._search_func = None
        self._use_for = use_for
    
    def _load_module(self):
        """Load the module on first access."""
        if self._module is None:
            try:
                self._module = importlib.import_module(
                    f'VibraVid.{folder_name}.{self.module_name}'
                )
                self._search_func = getattr(self._module, 'search')
                self._use_for = getattr(self._module, '_useFor')
            except Exception as e:
                console.print(f"[red]Failed to load module {self.module_name}: {str(e)}")
                raise
    
    def __call__(self, *args, **kwargs):
        """Execute search function when called.
        
        Args:
            *args: Positional arguments to pass to search function
            **kwargs: Keyword arguments to pass to search function
            
        Returns:
            Result from the search function
        """
        self._load_module()
        return self._search_func(*args, **kwargs)
    
    @property
    def use_for(self):
        """Get _useFor attribute (loads module if needed).
        
        Returns:
            List of content types this module supports
        """
        if self._use_for is None:
            self._load_module()

        return self._use_for
    
    def __getitem__(self, index: int):
        """Support tuple unpacking: func, use_for = loaded_functions['name'].
        
        Args:
            index: Index to access (0 for function, 1 for use_for)
            
        Returns:
            Self (as callable) for index 0, use_for for index 1
        """
        if index == 0:
            return self
        elif index == 1:
            return self.use_for
        
        raise IndexError("LazySearchModule only supports indices 0 and 1")


def load_search_functions() -> Dict[str, LazySearchModule]:
    """Load and return all available search functions from site modules.
    
    Returns:
        Dictionary mapping '{module_name}_search' to LazySearchModule instances
    """
    loaded_functions = {}
    
    # Determine base path
    if get_is_binary_installation():
        base_path = os.path.join(sys._MEIPASS, "VibraVid", folder_name)
    else:
        base_path = os.path.dirname(os.path.dirname(__file__))
    
    logger.info(f"Loading site modules from: {base_path}")
    
    modules_metadata = []
    
    # Escape base_path for glob to handle paths with special characters like brackets
    escaped_base_path = os_manager.get_glob_path(base_path)
    found_inits = glob.glob(os.path.join(escaped_base_path, '*', '__init__.py'))
    
    for init_file in found_inits:
        module_name = os.path.basename(os.path.dirname(init_file))
        try:
            with open(init_file, 'r', encoding='utf-8') as f:
                content = f.read()
                
            # Extract indice and _useFor using simple string search (faster than regex)
            indice = None
            use_for = None
            for line in content.split('\n'):
                line = line.strip()
                if not indice and (line.startswith('indice =') or line.startswith('indice=')):
                    try:
                        indice = int(line.split('=')[1].strip())
                    except (ValueError, IndexError):
                        pass
                elif not use_for and (line.startswith('_useFor =') or line.startswith('_useFor=')):
                    try:
                        use_for = line.split('=')[1].strip().strip('"').strip("'")
                    except IndexError:
                        pass
                
                if indice is not None and use_for is not None:
                    break
            
            if indice is not None:
                modules_metadata.append((module_name, indice, use_for))
                
        except Exception as e:
            console.print(f"[yellow]Warning: Could not read metadata from {module_name}: {str(e)}[/yellow]")
    
    # Sort by index and create lazy loaders with consecutive indices
    sorted_modules = sorted(modules_metadata, key=lambda x: x[1])
    for new_indice, (module_name, old_indice, use_for) in enumerate(sorted_modules):
        loaded_functions[f'{module_name}_search'] = LazySearchModule(module_name, new_indice, use_for)

        # Update indice in __init__.py for each module only if changed
        if new_indice == old_indice:
            continue

        init_file = os.path.join(base_path, module_name, '__init__.py')
        logger.info(f"Updating indice for module {module_name}: {old_indice} -> {new_indice}")

        try:
            with open(init_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            with open(init_file, 'w', encoding='utf-8') as f:
                for line in lines:
                    if line.strip().startswith('indice =') or line.strip().startswith('indice='):
                        f.write(f'indice = {new_indice}\n')
                    else:
                        f.write(line)
                        
        except Exception as e:
            console.print(f"[yellow]Warning: Could not update indice in {module_name}: {str(e)}")

    return loaded_functions


def get_folder_name() -> str:
    """Get the folder name where site modules are located.
    
    Returns:
        The folder name as a string
    """
    return folder_name