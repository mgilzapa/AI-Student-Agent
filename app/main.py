"""
AI Student Agent - Sprint 2 RAG Pipeline Runner

This script runs the full ingestion and RAG pipeline:
1. Scans raw inputs
2. Parses eligible files
3. Creates standardized outputs
4. Generates chunks
5. Generates OpenAI embeddings
6. Stores in ChromaDB
7. Enables grounded Q&A

Usage:
    python app/main.py                        # Run full pipeline (ingest + index)
    python app/main.py --index-only           # Only index existing chunks
    python app/main.py --query "deine Frage"  # Ask a question
"""
import logging
import uuid
import argparse
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

logger = setup_logger("study_agent")


def process_document(file_path: Path, processed_dir: Path) -> Dict[str, Any]:
    """Process a single document through the full pipeline."""
    result = {
        "file": str(file_path),
        "parse_success": False,
        "chunkCount": 0,
        "outputPath": None,
        "error": None
    }

    logger.info(f"Processing: {file_path.name}")

    parsed: ParseResult = parse_document(file_path)

    if not parsed.success:
        logger.error(f"Parse failed: {file_path.name} - {parsed.error_message}")
        result["error"] = parsed.error_message
        return result

    logger.info(f"Parsed {file_path.name}: {len(parsed.extracted_text)} chars")

    document_id = str(uuid.uuid4())
    parsed_data = parsed.to_dict()
    parsed_data["document_id"] = document_id
    parsed_data["module_name"] = ""
    parsed_data["document_type"] = ""

    saved_path = save_parsed_document(parsed_data, processed_dir / "parsed")
    if saved_path:
        result["outputPath"] = str(saved_path)
    else:
        logger.warning(f"Skipped saving (already exists): {file_path.name}")

    chunks: List[Chunk] = chunk_document(
        text=parsed.extracted_text,
        document_id=document_id,
        file_type=parsed.file_type,
        metadata=parsed.metadata
    )
    result["chunkCount"] = len(chunks)
    logger.info(f"Generated {len(chunks)} chunks from {file_path.name}")

    if chunks:
        chunk_path = save_chunks(chunks, processed_dir / "chunks", document_id)
        if chunk_path:
            logger.info(f"Saved chunks to: {chunk_path}")

    result["parseSuccess"] = True
    return result


def load_chunks_for_indexing(processed_dir: Path) -> List[Dict[str, Any]]:
    """Load all chunks from JSON files for indexing."""
    import json
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

    logger.info(f"Generating embeddings for {len(chunks)} chunks...")
    texts = [c["chunk_text"] for c in chunks]
    embeddings = embedder.embed_batch(texts)

    vector_store.add(
        ids=[c["chunk_id"] for c in chunks],
        embeddings=embeddings,
        documents=texts,
        metadatas=[
            {
                "document_id": c.get("document_id", ""),
                "source": c.get("source", ""),
                "chunk_index": str(c.get("chunk_index", "")),
            }
            for c in chunks
        ],
    )

    logger.info(f"Indexed {vector_store.count()} documents into ChromaDB")


def run_pipeline(config: Dict[str, Any]) -> None:
    """Run full ingestion + indexing pipeline."""
    logger.info("=" * 50)
    logger.info("AI Student Agent - Sprint 2 RAG Pipeline")
    logger.info("=" * 50)

    if not config["raw_path"].exists():
        logger.error(f"Raw folder does not exist: {config['raw_path']}")
        return

    logger.info("Scanning for supported files...")
    files = scan_intake()

    if not files:
        logger.info("No supported files found. Add PDF, PPTX, or TXT files to data/raw/")
        return

    logger.info(f"Found {len(files)} file(s) — processing...")
    results = [process_document(f, config["processed_path"]) for f in files]

    successful  = sum(1 for r in results if r["parseSuccess"])
    total_chunks = sum(r["chunkCount"] for r in results)

    logger.info(f"Processed: {len(results)} | OK: {successful} | Chunks: {total_chunks}")

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
