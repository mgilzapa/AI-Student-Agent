"""Local persistence layer for processed documents and chunks."""
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import datetime

from app.chunking.chunker import Chunk

logger = logging.getLogger(__name__)


def _ensure_directory(path: Path) -> None:
    """Ensure directory exists."""
    path.parent.mkdir(parents=True, exist_ok=True)


def save_parsed_document(
    parsed_data: Dict[str, Any],
    output_dir: Path,
    overwrite: bool = False
) -> Optional[Path]:
    """
    Save parsed document to JSON file.

    Args:
        parsed_data: Dictionary containing parsed document data
        output_dir: Directory to save output
        overwrite: Whether to overwrite existing file

    Returns:
        Path to saved file, or None if skipped
    """
    file_name = parsed_data.get("file_name", "unknown")
    safe_name = Path(file_name).stem.replace(" ", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"{safe_name}_{timestamp}.json"

    if output_path.exists() and not overwrite:
        logger.warning(f"Output exists, skipping: {output_path}")
        return None

    _ensure_directory(output_path)

    output_data = {
        "document_id": parsed_data.get("document_id", ""),
        "source_path": parsed_data.get("source_path", ""),
        "file_name": file_name,
        "file_type": parsed_data.get("file_type", ""),
        "module_name": parsed_data.get("module_name", ""),
        "document_type": parsed_data.get("document_type", ""),
        "extracted_text": parsed_data.get("extracted_text", ""),
        "extraction_status": "success" if parsed_data.get("success") else "failed",
        "extraction_notes": parsed_data.get("error_message", ""),
        "processed_at": datetime.utcnow().isoformat(),
        "metadata": parsed_data.get("metadata", {}),
    }

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    logger.info(f"Saved parsed document: {output_path}")
    return output_path


def save_chunks(
    chunks: List[Chunk],
    output_dir: Path,
    document_id: str,
    overwrite: bool = False
) -> Optional[Path]:
    """
    Save chunks to JSONL file (one chunk per line).

    Args:
        chunks: List of Chunk objects
        output_dir: Directory to save output
        document_id: Parent document identifier
        overwrite: Whether to overwrite existing file

    Returns:
        Path to saved file, or None if skipped
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = output_dir / f"chunks_{document_id[:8]}_{timestamp}.jsonl"

    if output_path.exists() and not overwrite:
        logger.warning(f"Chunk output exists, skipping: {output_path}")
        return None

    _ensure_directory(output_path)

    with open(output_path, 'w', encoding='utf-8') as f:
        for chunk in chunks:
            f.write(json.dumps(chunk.to_dict(), ensure_ascii=False) + "\n")

    logger.info(f"Saved {len(chunks)} chunks: {output_path}")
    return output_path


def load_processed_document(file_path: Path) -> Optional[Dict[str, Any]]:
    """Load a previously processed document."""
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        return None

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load {file_path}: {e}")
        return None


def load_chunks(file_path: Path) -> List[Chunk]:
    """Load chunks from JSONL file."""
    chunks = []
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        return chunks

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    chunks.append(Chunk(
                        chunk_id=data["chunk_id"],
                        parent_document_id=data["parent_document_id"],
                        chunk_text=data["chunk_text"],
                        chunk_index=data["chunk_index"],
                        chunk_size=data["chunk_size"],
                        metadata=data.get("metadata", {})
                    ))
    except Exception as e:
        logger.error(f"Failed to load chunks from {file_path}: {e}")

    return chunks
