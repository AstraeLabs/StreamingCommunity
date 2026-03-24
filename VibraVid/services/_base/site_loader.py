# 01.10.25 

import os
import sys
import glob
import logging
import importlib
import importlib.util
from typing import Dict

from rich.console import Console

from VibraVid.setup import get_is_binary_installation
from VibraVid.utils import config_manager
from VibraVid.utils.os import os_manager


console = Console()
logger = logging.getLogger(__name__)
folder_name = "services"
imp_sources = config_manager.config.get_list("DEFAULT", "imp_service")


class LazySearchModule:
    def __init__(self, module_name: str, indice: int, use_for: str = None, source: str = "default", base_path: str = None):
        """
        Lazy loader for a search module.

        Args:
            module_name: Name of the site module (e.g., 'streamingcommunity')
            indice: Sort index for the module
            use_for: Content types this module supports
            source: Source of the module ('default' or custom path)
            base_path: Base path for custom sources
        """
        self.module_name = module_name
        self.indice = indice
        self._module = None
        self._search_func = None
        self._use_for = use_for
        self.source = source
        self.base_path = base_path
    
    def _load_module(self):
        """Load the module on first access."""
        if self._module is None:
            try:
                if self.source.lower() == "default":
                    self._module = importlib.import_module(f'VibraVid.{folder_name}.{self.module_name}')
                else:
                    # Load from custom path
                    paths_to_add = [self.base_path]
                    
                    # Also add module directory for relative imports
                    module_dir = os.path.join(self.base_path, self.module_name)
                    if module_dir != self.base_path:
                        paths_to_add.append(module_dir)
                    
                    # Add paths temporarily
                    added_paths = []
                    for path in paths_to_add:
                        if path not in sys.path:
                            sys.path.insert(0, path)
                            added_paths.append(path)
                            logger.info(f"Added path to sys.path for '{self.module_name}': {path}")
                    
                    try:
                        logger.info(f"Loading module '{self.module_name}' from custom path: {self.base_path}")
                        spec = importlib.util.spec_from_file_location(self.module_name, os.path.join(self.base_path, self.module_name, '__init__.py'), submodule_search_locations=[module_dir])
                        if spec and spec.loader:
                            self._module = importlib.util.module_from_spec(spec)
                            sys.modules[self.module_name] = self._module
                            spec.loader.exec_module(self._module)
                        else:
                            raise ImportError(f"Could not load module {self.module_name} from {self.base_path}")
                    
                    finally:
                        # Remove added paths
                        for path in added_paths:
                            if path in sys.path:
                                sys.path.remove(path)
                                logger.info(f"Removed path from sys.path: {path}")
                
                self._search_func = getattr(self._module, 'search', None)
                if self._search_func is None:
                    raise AttributeError(f"Module '{self.module_name}' does not have a 'search' function")
                
                self._use_for = getattr(self._module, '_useFor', None)
                if self._use_for is None:
                    raise AttributeError(f"Module '{self.module_name}' does not define '_useFor'")
                
                logger.info(f"Successfully loaded module '{self.module_name}' from source '{self.source}'")
            except Exception as e:
                console.print(f"[red]Failed to load module {self.module_name} from source '{self.source}': {str(e)}")
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
    modules_metadata = []
    loaded_module_names = set()
    
    for source in imp_sources:
        if source.lower() == "default":
            if get_is_binary_installation():
                base_path = os.path.join(sys._MEIPASS, "VibraVid", folder_name)
            else:
                base_path = os.path.dirname(os.path.dirname(__file__))
        else:
            base_path = source
        
        if not os.path.isdir(base_path):
            logger.error(f"Import source path not found: {base_path}")
            continue
        
        logger.info(f"Loading site modules from source '{source}': {base_path}")
        
        # Escape base_path for glob to handle paths with special characters like brackets
        escaped_base_path = os_manager.get_glob_path(base_path)
        found_inits = glob.glob(os.path.join(escaped_base_path, '*', '__init__.py'))
        
        source_modules = []
        for init_file in found_inits:
            module_name = os.path.basename(os.path.dirname(init_file))
            
            # Skip if already loaded from a previous source
            if module_name in loaded_module_names:
                logger.info(f"Skipping duplicate module '{module_name}' from source '{source}'")
                continue
            
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
                    source_modules.append((module_name, indice, use_for, source, base_path))
                    loaded_module_names.add(module_name)
                    logger.info(f"Found module '{module_name}' from source '{source}': use_for={use_for}, indice={indice}")
                    
            except Exception as e:
                console.print(f"[yellow]Warning: Could not read metadata from {module_name}: {str(e)}[/yellow]")
        
        modules_metadata.extend(source_modules)
    
    # Sort by index and create lazy loaders with consecutive indices
    sorted_modules = sorted(modules_metadata, key=lambda x: x[1])
    for new_indice, (module_name, old_indice, use_for, source, base_path) in enumerate(sorted_modules):
        loaded_functions[f'{module_name}_search'] = LazySearchModule(module_name, new_indice, use_for, source, base_path)

        # Update indice in __init__.py for each module only if changed and from default source
        if source.lower() == "default":
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

    logger.info(f"Successfully loaded {len(loaded_functions)} search functions from {len(imp_sources)} source(s)")
    return loaded_functions


def get_folder_name() -> str:
    """Get the folder name where site modules are located.
    
    Returns:
        The folder name as a string
    """
    return folder_name