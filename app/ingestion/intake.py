"""
Raw file intake workflow.

Scans the raw data folder for supported files and returns
a list of unique, supported files ready for processing.

Handles:
- Recursive folder scanning
- Duplicate file detection (by filename)
- Unsupported file filtering
- Logging of all decisions
"""
import logging
from pathlib import Path
from typing import List, Set

from app.utils.config import load_config
from app.ingestion.file_scanner import is_supported

logger = logging.getLogger(__name__)


def get_all_supported_files(raw_dir: Path) -> List[Path]:
    """
    Recursively scan raw_dir for supported files.

    Args:
        raw_dir: Directory to scan

    Returns:
        List of paths to supported files
    """
    supported_files = []

    if not raw_dir.exists():
        logger.error(f"Raw directory does not exist: {raw_dir}")
        return supported_files

    for file_path in raw_dir.rglob("*"):
        if file_path.is_file():
            if is_supported(file_path):
                supported_files.append(file_path)
                logger.debug(f"Found supported file: {file_path}")
            else:
                logger.info(f"Skipping unsupported file: {file_path}")

    logger.info(f"Intake scan: {len(supported_files)} supported file(s) found in {raw_dir}")
    return supported_files


def remove_duplicates(file_list: List[Path]) -> List[Path]:
    """
    Remove duplicate files based on filename.

    When two files have the same name but different paths,
    the first one encountered is kept.

    Args:
        file_list: List of file paths

    Returns:
        List of unique file paths
    """
    seen: Set[str] = set()
    unique = []

    for f in file_list:
        if f.name not in seen:
            seen.add(f.name)
            unique.append(f)
        else:
            logger.warning(f"Duplicate filename skipped: {f} (already have: {f.name})")

    duplicates_removed = len(file_list) - len(unique)
    if duplicates_removed > 0:
        logger.info(f"Removed {duplicates_removed} duplicate(s)")

    return unique


def scan_intake() -> List[Path]:
    """
    Main intake function: scans data/raw for supported files
    and returns a deduplicated list ready for processing.

    Returns:
        List of unique, supported file paths
    """
    config = load_config()
    raw_path = config["raw_path"]

    logger.info(f"Starting intake scan from: {raw_path}")

    if not raw_path.exists():
        logger.error(f"Raw folder does not exist: {raw_path}")
        logger.info(f"Please create the folder: mkdir {raw_path}")
        return []

    all_files = get_all_supported_files(raw_path)
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
