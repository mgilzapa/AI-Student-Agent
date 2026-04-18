"""Chunking pipeline for splitting parsed documents into retrievable chunks."""
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A single chunk of text from a document."""
    chunk_id: str
    parent_document_id: str
    chunk_text: str
    chunk_index: int
    chunk_size: int
    metadata: Dict[str, Any] = field(default_factory=dict)
    embedding: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "parent_document_id": self.parent_document_id,
            "chunk_text": self.chunk_text,
            "chunk_index": self.chunk_index,
            "chunk_size": self.chunk_size,
            "metadata": self.metadata,
            "embedding": self.embedding,
        }

    def has_embedding(self) -> bool:
        """Check if chunk has an embedding."""
        return bool(self.embedding)


def chunk_by_characters(
    text: str,
    chunk_size: int = 500,
    overlap: int = 50,
    document_id: str = None,
    metadata: Dict[str, Any] = None
) -> List[Chunk]:
    """
    Split text into overlapping chunks by character count.

    Args:
        text: The text to chunk
        chunk_size: Maximum characters per chunk
        overlap: Number of overlapping characters between chunks
        document_id: Parent document identifier
        metadata: Metadata to attach to each chunk

    Returns:
        List of Chunk objects
    """
    if not text or not text.strip():
        return []

    chunks = []
    start = 0
    text_length = len(text)
    metadata = metadata or {}

    chunk_index = 0
    while start < text_length:
        end = start + chunk_size
        chunk_text = text[start:end]

        # Try to break at sentence boundary
        if end < text_length:
            last_period = chunk_text.rfind('.')
            last_newline = chunk_text.rfind('\n')
            break_point = max(last_period, last_newline)

            if break_point > chunk_size // 2:  # Only break if we're past halfway
                chunk_text = chunk_text[:break_point + 1]
                end = start + break_point + 1

        chunk = Chunk(
            chunk_id=str(uuid.uuid4()),
            parent_document_id=document_id or str(uuid.uuid4()),
            chunk_text=chunk_text.strip(),
            chunk_index=chunk_index,
            chunk_size=len(chunk_text),
            metadata=metadata.copy()
        )

        if chunk.chunk_text:  # Only add non-empty chunks
            chunks.append(chunk)
            chunk_index += 1

        # Move start position
        if end >= text_length:
            break
        start = end - overlap if end - overlap > start else end

    logger.info(f"Created {len(chunks)} chunks from {text_length} characters")
    return chunks


def chunk_document(
    text: str,
    document_id: str,
    file_type: str = "unknown",
    chunk_size: int = 500,
    overlap: int = 50,
    metadata: Dict[str, Any] = None,
    generate_embeddings: bool = False
) -> List[Chunk]:
    """
    Main entry point for chunking a document.

    Args:
        text: Extracted text to chunk
        document_id: Unique document identifier
        file_type: Type of source document
        chunk_size: Target chunk size in characters
        overlap: Overlap between chunks
        metadata: Document metadata to propagate

    Returns:
        List of Chunk objects
    """
    base_metadata = {
        "file_type": file_type,
        "created_at": datetime.utcnow().isoformat(),
    }
    if metadata:
        base_metadata.update(metadata)

    # Adjust chunk size based on file type
    if file_type == "pptx":
        # Slides are usually shorter, use smaller chunks
        chunk_size = min(chunk_size, 300)

    return chunk_by_characters(
        text=text,
        chunk_size=chunk_size,
        overlap=overlap,
        document_id=document_id,
        metadata=base_metadata
    )
