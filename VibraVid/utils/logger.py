# 29.01.24

import os
import sys
import logging
from datetime import datetime
from pathlib import Path
from logging.handlers import RotatingFileHandler

from VibraVid.utils import config_manager


# INFO    → info, warning, error, critical
# WARNING → warning, error, critical
# ERROR   → error, critical
# DEBUG   → debug, info, warning, error, critical
conf_log_level = config_manager.config.get("DEFAULT", "log_level").upper()
LOG_LEVEL = getattr(logging, conf_log_level)

_log_file = None

def setup_logger(name=None):
    global _log_file
    app_base_path = config_manager.base_path
    
    cache_dir = Path(os.path.join(app_base_path, ".cache"))
    log_dir = cache_dir / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        print(f"Warning: Could not create log directory {log_dir}: {e}", file=sys.stderr)

    if _log_file is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        _log_file = log_dir / f"{timestamp}.log"

    log_format = logging.Formatter(
        '[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%H:%M:%S'
    )

    logger = logging.getLogger(name)
    logger.setLevel(LOG_LEVEL)

    root_logger = logging.getLogger()
    root_logger.setLevel(LOG_LEVEL)

    if not root_logger.handlers:
        try:
            file_handler = RotatingFileHandler(
                str(_log_file),
                maxBytes=10*1024*1024,
                backupCount=5,
                encoding='utf-8'
            )
            file_handler.setFormatter(log_format)
            file_handler.setLevel(LOG_LEVEL)  # ← era fisso a INFO, ora usa la variabile
            root_logger.addHandler(file_handler)
            
            logging.captureWarnings(True)
            root_logger.info(f"--- Logging initialized: {_log_file} ---")
        except Exception as e:
            print(f"Error: Could not create file handler for {_log_file}: {e}", file=sys.stderr)
            raise

    return logger

# Init
logger = setup_logger()