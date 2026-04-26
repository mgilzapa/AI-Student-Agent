"""
AI Student Agent - Sprint 2 RAG Pipeline Runner

Usage:
    python -m app.main                        # Run full pipeline (ingest + index)
    python -m app.main --index-only           # Only index existing chunks
    python -m app.main --query "deine Frage"  # Ask a question
"""
import hashlib
import logging
import argparse
import json
from pathlib import Path
from typing import List, Dict, Any

from app.utils.config import load_config
from app.utils.logger import setup_logger
from app.ingestion.intake import scan_intake
from app.parsing.parsers import parse_document, ParseResult
from app.chunking.chunker import chunk_document, Chunk
from app.storage.persister import save_parsed_document, save_chunks
from app.embeddings.embedder import Embedder
from app.vectorstore.chroma_db import ChromaVectorStore
from app.rag.query_service import create_query_service
from app.lecture.pipeline import process_lecture

logger = setup_logger("study_agent")


def document_id_for(file_path: Path) -> str:
    """Deterministic document ID based on file path — same file always gets same ID."""
    return hashlib.md5(str(file_path).encode()).hexdigest()


def already_processed(document_id: str, chunks_dir: Path) -> bool:
    """Check if chunks for this document already exist."""
    return len(list(chunks_dir.glob(f"chunks_{document_id[:8]}_*.jsonl"))) > 0


def process_document(file_path: Path, processed_dir: Path) -> Dict[str, Any]:
    """Process a single document through the full pipeline."""
    result = {
        "file": str(file_path),
        "parseSuccess": False,
        "chunkCount": 0,
        "outputPath": None,
        "error": None,
        "skipped": False,
    }

    document_id = document_id_for(file_path)
    chunks_dir = processed_dir / "chunks"

    # Skip if already processed
    if already_processed(document_id, chunks_dir):
        logger.info(f"Skipping (already processed): {file_path.name}")
        result["parseSuccess"] = True
        result["skipped"] = True
        return result

    logger.info(f"Processing: {file_path.name}")

    parsed: ParseResult = parse_document(file_path)

    if not parsed.success:
        logger.error(f"Parse failed: {file_path.name} - {parsed.error_message}")
        result["error"] = parsed.error_message
        return result

    logger.info(f"Parsed {file_path.name}: {len(parsed.extracted_text)} chars")

    module_name = ""
    try:
        raw_root = load_config()["raw_path"].resolve()
        relative_path = file_path.resolve().relative_to(raw_root)
        if len(relative_path.parts) > 1:
            module_name = relative_path.parts[0]
    except Exception:
        module_name = file_path.parent.name if file_path.parent.name else ""

    lecture_result = process_lecture(
    filename=file_path.name,
    text=parsed.extracted_text,
    modul_name=module_name or None,
)
    if lecture_result:
        logger.info(f"Vorlesungszusammenfassung erstellt: {lecture_result['saved_to']}")
        result["lecture_summary"] = lecture_result["saved_to"]

    parsed_data = parsed.to_dict()
    parsed_data["document_id"] = document_id
    parsed_data["module_name"] = module_name
    parsed_data["document_type"] = ""

    saved_path = save_parsed_document(parsed_data, processed_dir / "parsed")
    if saved_path:
        result["outputPath"] = str(saved_path)

    chunks: List[Chunk] = chunk_document(
    text=parsed.extracted_text,
    document_id=document_id,
    file_type=parsed.file_type,
    metadata={
        **parsed.metadata,
        "source": str(file_path),
        "module_name": module_name,
    }
)
    result["chunkCount"] = len(chunks)
    logger.info(f"Generated {len(chunks)} chunks from {file_path.name}")

    if chunks:
        chunk_path = save_chunks(chunks, chunks_dir, document_id)
        if chunk_path:
            logger.info(f"Saved chunks to: {chunk_path}")

    result["parseSuccess"] = True
    return result


def load_chunks_for_indexing(processed_dir: Path) -> List[Dict[str, Any]]:
    """Load all chunks from .jsonl files."""
    chunks_dir = processed_dir / "chunks"
    chunks = []

    if not chunks_dir.exists():
        logger.warning(f"Chunks directory does not exist: {chunks_dir}")
        return chunks

    for f in sorted(chunks_dir.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                chunks.append(json.loads(line))

    return [c for c in chunks if c.get("chunk_text", "").strip()]


def index_chunks(config: Dict[str, Any]) -> None:
    """Index all chunks into ChromaDB using OpenAI embeddings."""
    logger.info("-" * 50)
    logger.info("Indexing chunks into ChromaDB...")

    chunks = load_chunks_for_indexing(config["processed_path"])

    if not chunks:
        logger.warning("No chunks found. Run pipeline first.")
        return

    embedder = Embedder(model_name=config["embedding_model"])
    vector_store = ChromaVectorStore(
        persist_dir=config["chroma_path"],
        collection_name="study_chunks"
    )

    # Only index chunks not already in Chroma
    existing_ids = set(vector_store.collection.get()["ids"])
    new_chunks = [c for c in chunks if c["chunk_id"] not in existing_ids]

    if not new_chunks:
        logger.info("All chunks already indexed — nothing to do.")
        return

    logger.info(f"Generating embeddings for {len(new_chunks)} new chunks...")
    texts = [c["chunk_text"] for c in new_chunks]
    embeddings = embedder.embed_batch(texts)

    BATCH_SIZE = 500
    for i in range(0, len(new_chunks), BATCH_SIZE):
        batch_chunks = new_chunks[i:i+BATCH_SIZE]
        batch_embeddings = embeddings[i:i+BATCH_SIZE]
        vector_store.add(
            ids=[c["chunk_id"] for c in batch_chunks],
            embeddings=batch_embeddings,
            documents=[c["chunk_text"] for c in batch_chunks],
            metadatas=[
                {
                    "document_id": c.get("document_id", ""),
                    "source": c.get("metadata", {}).get("source", c.get("source", "")),
                    "module_name": c.get("metadata", {}).get("module_name", ""),
                    "chunk_index": str(c.get("chunk_index", "")),
                }
                for c in batch_chunks
            ],
        )
        logger.info(f"Indexed batch {i//BATCH_SIZE + 1} ({min(i+BATCH_SIZE, len(new_chunks))}/{len(new_chunks)})")

    logger.info(f"Done — {vector_store.count()} total documents in ChromaDB")


def run_pipeline(config: Dict[str, Any]) -> None:
    """Run full ingestion + indexing pipeline."""
    logger.info("=" * 50)
    logger.info("AI Student Agent - Sprint 2 RAG Pipeline")
    logger.info("=" * 50)

    logger.info("Scanning for supported files...")
    files = scan_intake()

    if not files:
        logger.info("No supported files found.")
        return

    logger.info(f"Found {len(files)} file(s) — processing...")
    results = [process_document(f, config["processed_path"]) for f in files]

    successful   = sum(1 for r in results if r.get("parseSuccess"))
    skipped      = sum(1 for r in results if r.get("skipped"))
    new          = successful - skipped
    total_chunks = sum(r["chunkCount"] for r in results)

    logger.info(f"Total: {len(results)} | Neu: {new} | Übersprungen: {skipped} | Chunks: {total_chunks}")

    index_chunks(config)


def ask_question(config: Dict[str, Any], question: str) -> None:
    """Ask a question using the RAG system."""
    embedder = Embedder(model_name=config["embedding_model"])
    vector_store = ChromaVectorStore(
        persist_dir=config["chroma_path"],
        collection_name="study_chunks"
    )
    rag = create_query_service(
        vector_store=vector_store,
        embedder=embedder,
        top_k=config["top_k"]
    )

    result = rag.ask(question)

    print(f"\nFrage: {question}")
    print(f"\nAntwort:\n{result['answer']}")
    print("\nQuellen:")
    for src in result["sources"]:
        print(f"  - {src['source']} (score: {src['score']})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI Study Agent - Sprint 2 RAG Pipeline")
    parser.add_argument("--query", type=str, help="Ask a question against indexed study material")
    parser.add_argument("--index-only", action="store_true", help="Only index chunks, skip ingestion")
    args = parser.parse_args()

    config = load_config()

    if args.query:
        ask_question(config, args.query)
    elif args.index_only:
        index_chunks(config)
    else:
        run_pipeline(config)
