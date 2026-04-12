# 29.01.24

import os
import sys
import logging
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler

from VibraVid.utils import config_manager

_log_file = None



def setup_logger(name=None):
    """
    Configures a logger that writes to a timestamped file in the .cache/logs directory.
    """
    global _log_file
    app_base_path = config_manager.base_path
    
    cache_dir = Path(os.path.join(app_base_path, ".cache"))
    log_dir = cache_dir / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Warning: Could not create log directory {log_dir}: {e}", file=sys.stderr)

    # 2. Create/Get filename with timestamp (shared across all calls in same session)
    if _log_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _log_file = log_dir / f"{timestamp}.log"

    # 3. Define format
    log_format = logging.Formatter(
        '[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # 4. Setup specific logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    # 5. Root logger configuration (Handles everything)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    
    if not root_logger.handlers:
        try:
            file_handler = RotatingFileHandler(
                str(_log_file),  # Ensure path is string
                maxBytes=10*1024*1024, # 10MB
                backupCount=5,
                encoding='utf-8'
            )
            file_handler.setFormatter(log_format)
            file_handler.setLevel(logging.INFO)
            root_logger.addHandler(file_handler)
            
            # Capture warnings from the 'warnings' module
            logging.captureWarnings(True)
            root_logger.info(f"--- Logging initialized: {_log_file} ---")
        except Exception as e:
            print(f"Error: Could not create file handler for {_log_file}: {e}", file=sys.stderr)
            raise

    return logger


# Init
logger = setup_logger()