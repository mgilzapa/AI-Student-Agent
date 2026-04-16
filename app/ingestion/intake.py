import logging
from pathlib import Path
from typing import List, Set

from app.utils.config import load_config
from app.ingestion.file_scanner import is_supported  # aus Task 4

logger = logging.getLogger(__name__)

def get_all_supported_files(raw_dir: Path) -> List[Path]:
    """Durchsucht raw_dir rekursiv nach unterstützten Dateien."""
    supported_files = []
    for file_path in raw_dir.rglob("*"):
        if file_path.is_file() and is_supported(file_path):
            supported_files.append(file_path)
            logger.debug(f"Found supported file: {file_path}")
        elif file_path.is_file():
            logger.info(f"Skipping unsupported file: {file_path}")
    return supported_files

def remove_duplicates(file_list: List[Path]) -> List[Path]:
    """Entfernt Duplikate basierend auf Dateinamen (optional: Hash)."""
    seen: Set[str] = set()
    unique = []
    for f in file_list:
        if f.name not in seen:
            seen.add(f.name)
            unique.append(f)
        else:
            logger.warning(f"Duplicate filename skipped: {f}")
    return unique

def scan_intake() -> List[Path]:
    """Hauptfunktion: liefert Liste eindeutiger, unterstützter Dateien aus data/raw."""
    config = load_config()
    raw_path = config["raw_path"]
    
    if not raw_path.exists():
        logger.error(f"Raw folder does not exist: {raw_path}")
        return []
    
    all_files = get_all_supported_files(raw_path)
    unique_files = remove_duplicates(all_files)
    
    logger.info(f"Intake scan complete: {len(unique_files)} files ready for processing")
    return unique_files

if __name__ == "__main__":
    files = scan_intake()
    print("Gefundene Dateien:")
    for f in files:
        print(f)