import os
from dotenv import load_dotenv
from pathlib import Path

# Basisverzeichnis des Projekts (wo .env liegt)
BASE_DIR = Path(__file__).parent.parent.parent

def load_config():
    load_dotenv(BASE_DIR / ".env")
    return {
        "raw_path": BASE_DIR / os.getenv("DATA_RAW_PATH", "data/raw"),
        "processed_path": BASE_DIR / os.getenv("DATA_PROCESSED_PATH", "data/processed"),
        "exports_path": BASE_DIR / os.getenv("DATA_EXPORTS_PATH", "data/exports"),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
    }
