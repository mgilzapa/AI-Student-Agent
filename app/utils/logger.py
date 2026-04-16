import logging
import sys
from pathlib import Path

from app.utils.config import load_config

def setup_logger(name: str = None) -> logging.Logger:
    """Richtet das Logging ein (Konsole + Datei)."""
    config = load_config()
    log_level = getattr(logging, config["log_level"].upper(), logging.INFO)
    
    logger = logging.getLogger(name)
    logger.setLevel(log_level)
    
    # Verhindere doppelte Handler
    if logger.handlers:
        return logger
    
    # Format
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console Handler
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)
    
    # File Handler (optional)
    log_file = config.get("log_file")
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    return logger