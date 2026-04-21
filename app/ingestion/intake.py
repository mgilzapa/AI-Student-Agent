"""
Raw file intake workflow.

Scans the raw data folder and optionally an Obsidian vault
for supported files ready for processing.
"""
import logging
from pathlib import Path
from typing import List, Set

from app.utils.config import load_config
from app.ingestion.file_scanner import is_supported

logger = logging.getLogger(__name__)


def get_all_supported_files(directory: Path) -> List[Path]:
    """Recursively scan directory for supported files."""
    supported_files = []

    if not directory.exists():
        logger.error(f"Directory does not exist: {directory}")
        return supported_files

    for file_path in directory.rglob("*"):
        if file_path.is_file():
            if is_supported(file_path):
                supported_files.append(file_path)
            else:
                logger.debug(f"Skipping unsupported file: {file_path}")

    logger.info(f"Found {len(supported_files)} supported file(s) in {directory}")
    return supported_files


def remove_duplicates(file_list: List[Path]) -> List[Path]:
    """Remove duplicate files based on filename."""
    seen: Set[str] = set()
    unique = []

    for f in file_list:
        if f.name not in seen:
            seen.add(f.name)
            unique.append(f)
        else:
            logger.warning(f"Duplicate skipped: {f.name}")

    return unique


def scan_intake() -> List[Path]:
    """
    Scan data/raw/ and optionally the Obsidian vault.
    Returns a deduplicated list of supported files.
    """
    config = load_config()
    all_files = []

    # Scan data/raw/
    raw_path = config["raw_path"]
    if raw_path.exists():
        all_files.extend(get_all_supported_files(raw_path))
    else:
        logger.warning(f"Raw folder does not exist: {raw_path}")

    # Scan Obsidian vault if configured
    vault_path = config.get("vault_path")
    if vault_path:
        if vault_path.exists():
            logger.info(f"Scanning Obsidian vault: {vault_path}")
            all_files.extend(get_all_supported_files(vault_path))
        else:
            logger.warning(f"Obsidian vault path not found: {vault_path}")
    else:
        logger.info("No Obsidian vault configured (OBSIDIAN_VAULT not set)")

    unique_files = remove_duplicates(all_files)
    logger.info(f"Intake complete: {len(unique_files)} unique file(s) ready for processing")
    return unique_files


if __name__ == "__main__":
    from app.utils.logger import setup_logger
    setup_logger("intake")

    files = scan_intake()
    print("\nFiles ready for processing:")
    for f in files:
        print(f"  - {f}")
