from pathlib import Path

SUPPORTED_EXTENSIONS = {".pdf", ".pptx", ".md", ".txt"}

def is_supported(file_path: Path) -> bool:
    return file_path.suffix.lower() in SUPPORTED_EXTENSIONS