"""
Study Agent — FastAPI Backend
Endpoints: GET / (serve UI), POST /ask
"""
import json
import re
import tempfile
from threading import Lock
from time import time
from typing import List, Optional, Literal
from urllib import response
import uuid
from fastapi import FastAPI, HTTPException, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from pathlib import Path
from openai import OpenAI
from app.main import load_chunks_for_indexing, process_document
from app.parsing.parsers import parse_document
from app.utils.config import load_config
from app.embeddings.embedder import Embedder
from app.vectorstore.chroma_db import ChromaVectorStore
from app.rag.query_service import create_query_service
from dotenv import load_dotenv
load_dotenv()

app = FastAPI(title="Study Agent")

INTAKE_DIR = Path("data/intake")
INTAKE_DIR.mkdir(parents=True, exist_ok=True)

client = OpenAI()

# -----------------------------
# In-memory temporary upload store
# -----------------------------
UPLOAD_CACHE = {}
UPLOAD_CACHE_LOCK = Lock()
UPLOAD_TTL_SECONDS = 15 * 60  # 15 Minuten


def cleanup_expired_uploads():
    now = time.time()
    expired_keys = []

    with UPLOAD_CACHE_LOCK:
        for key, item in UPLOAD_CACHE.items():
            if now - item["created_at"] > UPLOAD_TTL_SECONDS:
                expired_keys.append(key)

        for key in expired_keys:
            del UPLOAD_CACHE[key]


def get_existing_modules() -> List[str]:
    if not INTAKE_DIR.exists():
        return []
    return sorted([d.name for d in INTAKE_DIR.iterdir() if d.is_dir()])


def sanitize_module_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", name)  # Windows/Filesystem safe
    if not name:
        raise ValueError("Ungültiger Modulname.")
    return name


def normalize_module_name(name: str) -> str:
    # nur leichte Normalisierung, damit Deutsch/Englisch lesbar bleibt
    name = sanitize_module_name(name)
    return name


def file_already_exists(module_name: str, filename: str) -> bool:
    target = INTAKE_DIR / module_name / filename
    return target.exists()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Init RAG on startup
config = load_config()
embedder = Embedder(model_name=config["embedding_model"])
vector_store = ChromaVectorStore(
    persist_dir=config["chroma_path"],
    collection_name="study_chunks"
)
rag = create_query_service(vector_store=vector_store, embedder=embedder, top_k=config["top_k"])


class Question(BaseModel):
    question: str


@app.post("/ask")
def ask(body: Question):
    result = rag.ask(body.question)
    return result


@app.get("/", response_class=HTMLResponse)
def serve_ui():
    html_path = Path(__file__).parent / "static" / "index.html"
    return html_path.read_text(encoding="utf-8")

@app.post("/process")
async def process_lecture(file: UploadFile = File(...)):
    # 1. Datei lesen
    content = await file.read()

    # 2. Text extrahieren (dein bestehender Parser)
    with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    parsed = parse_document(tmp_path)  # dein bestehender Parser!
    text = parsed.extracted_text

    prompt = f"""
Analysiere diese Vorlesung und antworte NUR als valides JSON ohne Markdown:
{{
  "zusammenfassung": "...",
  "karteikarten": [{{"frage": "...", "antwort": "..."}}],
  "quiz": [{{"frage": "...", "optionen": ["A","B","C","D"], "richtig": 0}}]
}}

Vorlesung:
{text}"""

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    try:
        return json.loads(response.choices[0].message.content)
    except json.JSONDecodeError:
        return {
            "zusammenfassung": response.choices[0].message.content,
            "karteikarten": [],
            "quiz": []
        }


@app.post("/classify")
async def classify_lecture(file: UploadFile = File(...)):
    """Extract text and use GPT-4o-mini to suggest module classification."""
    content = await file.read()

    with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        parsed = parse_document(tmp_path)
        text = parsed.extracted_text[:15000]  # Truncate for API call

        prompt = f"""Analysiere diese Vorlesung und antworte NUR als valides JSON ohne Markdown:
{{"vorgeschlagenes_modul": "Name des Moduls", "begruendung": "Kurze Begründung"}}

Vorlesungsinhalt:
{text}"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3
        )

        try:
            result = json.loads(response.choices[0].message.content)
            return {
                "success": True,
                "suggested_module": result.get("vorgeschlagenes_modul", "Unbekannt"),
                "reason": result.get("begruendung", ""),
                "filename": file.filename
            }
        except json.JSONDecodeError:
            return {
                "success": True,
                "suggested_module": "Unbekannt",
                "reason": response.choices[0].message.content,
                "filename": file.filename
            }
    finally:
        tmp_path.unlink(missing_ok=True)


@app.get("/modules")
async def list_modules():
    """Return list of existing module names."""
    return get_existing_modules()


@app.post("/upload")
async def upload_to_module(
    file: UploadFile = File(...),
    module_name: str = Form(...)
):
    """Save uploaded file to specific module folder and trigger pipeline."""
    try:
        # Sanitize and validate module name
        clean_module = sanitize_module_name(module_name)

        # Create module directory if needed
        module_dir = INTAKE_DIR / clean_module
        module_dir.mkdir(parents=True, exist_ok=True)

        # Check for duplicates
        if file_already_exists(clean_module, file.filename):
            raise HTTPException(status_code=400, detail="Datei existiert bereits")

        # Save file
        target_path = module_dir / file.filename
        with open(target_path, "wb") as f:
            content = await file.read()
            f.write(content)

        # Trigger pipeline for this file
        result = process_document(target_path, Path("data/processed"))

        if result.get("parseSuccess"):
            # Index the new chunks
            index_chunks_for_file(target_path)
            return {
                "success": True,
                "message": f"Gespeichert unter {clean_module}/{file.filename}",
                "chunks": result.get("chunkCount", 0)
            }
        else:
            raise HTTPException(status_code=400, detail=result.get("error", "Parse failed"))

    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def index_chunks_for_file(file_path: Path):
    """Helper to index only new chunks from a specific file."""
    # Reuse existing index_chunks logic
    config = load_config()
    embedder = Embedder(model_name=config["embedding_model"])
    vector_store = ChromaVectorStore(
        persist_dir=config["chroma_path"],
        collection_name="study_chunks"
    )

    chunks = load_chunks_for_indexing(config["processed_path"])
    existing_ids = set(vector_store.collection.get()["ids"])
    new_chunks = [c for c in chunks if c["chunk_id"] not in existing_ids]

    if new_chunks:
        texts = [c["chunk_text"] for c in new_chunks]
        embeddings = embedder.embed_batch(texts)
        vector_store.add(
            ids=[c["chunk_id"] for c in new_chunks],
            embeddings=embeddings,
            documents=[c["chunk_text"] for c in new_chunks],
            metadatas=[
                {
                    "document_id": c.get("document_id", ""),
                    "source": c.get("metadata", {}).get("source", c.get("source", "")),
                    "chunk_index": str(c.get("chunk_index", "")),
                }
                for c in new_chunks
            ],
        )

