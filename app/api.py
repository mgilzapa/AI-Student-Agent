"""
Study Agent FastAPI backend.
"""
import json
import re 
import re as _re
import tempfile
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from openai import OpenAI
from pydantic import BaseModel

from app.embeddings.embedder import Embedder
from app.ingestion.file_scanner import is_supported
from app.main import (
    index_chunks,
    process_document,
)
from app.parsing.parsers import parse_document
from app.rag.query_service import create_query_service
from app.utils.config import load_config
from app.vectorstore.chroma_db import ChromaVectorStore
from app.lecture import module_profile as mp
from app.lecture.summarizer import summarize


load_dotenv()

app = FastAPI(title="Study Agent")

client = OpenAI()
config = load_config()
RAW_DIR = config["raw_path"]
RAW_DIR.mkdir(parents=True, exist_ok=True)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Init RAG on startup
embedder = Embedder(model_name=config["embedding_model"])
vector_store = ChromaVectorStore(
    persist_dir=config["chroma_path"],
    collection_name="study_chunks"
)
rag = create_query_service(vector_store=vector_store, embedder=embedder, top_k=config["top_k"])


class Question(BaseModel):
    question: str
    module_name: Optional[str] = None

class LectureSummarizeRequest(BaseModel):
    filename: str
    module_name: str

class LectureOnboardingRequest(BaseModel):
    module_name: str
    schwerpunkte: List[str] = []
    stil: str = "mixed"
    pruefungsrelevant: List[str] = []

def sanitize_module_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", name)
    if not name:
        raise ValueError("Ungueltiger Modulname.")
    return name


def module_dir(module_name: str) -> Path:
    return RAW_DIR / sanitize_module_name(module_name)


def list_module_files(module_name: str) -> List[dict]:
    base = module_dir(module_name)
    if not base.exists():
        return []

    files = []
    for file_path in sorted(base.rglob("*")):
        if file_path.is_file():
            files.append({
                "name": file_path.name,
                "relative_path": str(file_path.relative_to(base)),
                "file_type": file_path.suffix.lower().lstrip("."),
                "size": file_path.stat().st_size,
            })
    return files

def _find_file(module_name: str, filename: str) -> Path:
    """Sucht Datei im Modul-Verzeichnis."""
    base = RAW_DIR / sanitize_module_name(module_name)
    target = base / filename
    if target.exists():
        return target
    # Fallback: rekursiv suchen
    matches = list(base.rglob(filename))
    if matches:
        return matches[0]
    raise HTTPException(status_code=404, detail=f"Datei nicht gefunden: {filename}")


def _slug(name: str) -> str:
    s = name.lower().strip()
    s = _re.sub(r"[äöüß]", lambda m: {"ä":"ae","ö":"oe","ü":"ue","ß":"ss"}[m.group()], s)
    return _re.sub(r"[^a-z0-9]+", "-", s).strip("-")

@app.get("/", response_class=HTMLResponse)
def serve_ui():
    html_path = Path(__file__).parent / "static" / "index.html"
    return html_path.read_text(encoding="utf-8")


@app.post("/ask")
def ask(body: Question):
    result = rag.ask(body.question, module_name=body.module_name)
    return result


@app.get("/modules")
def get_modules():
    modules = []
    if RAW_DIR.exists():
        modules = sorted(d.name for d in RAW_DIR.iterdir() if d.is_dir())
    return {"modules": modules}


@app.get("/modules/{module_name}/files")
def get_module_files(module_name: str):
    files = list_module_files(module_name)
    if not files:
        raise HTTPException(status_code=404, detail="Modul nicht gefunden oder leer.")
    return {"module_name": sanitize_module_name(module_name), "files": files}


@app.post("/modules/upload")
async def upload_module(
    module_name: str = Form(...),
    files: List[UploadFile] = File(...)
):
    clean_module = sanitize_module_name(module_name)
    target_dir = module_dir(clean_module)
    target_dir.mkdir(parents=True, exist_ok=True)

    if not files:
        raise HTTPException(status_code=400, detail="Keine Dateien uebergeben.")

    saved_paths: List[Path] = []
    skipped_files: List[str] = []

    for uploaded in files:
        if not uploaded.filename:
            continue

        target_path = target_dir / Path(uploaded.filename).name
        if not is_supported(target_path):
            skipped_files.append(uploaded.filename)
            continue

        content = await uploaded.read()
        with open(target_path, "wb") as handle:
            handle.write(content)
        saved_paths.append(target_path)

    if not saved_paths:
        raise HTTPException(status_code=400, detail="Keine unterstuetzten Dateien hochgeladen.")

    results = [process_document(path, config["processed_path"]) for path in saved_paths]
    index_chunks(config)

    processed = sum(1 for result in results if result.get("parseSuccess"))
    skipped = sum(1 for result in results if result.get("skipped"))
    failed = [result for result in results if not result.get("parseSuccess")]

    return {
        "success": True,
        "module_name": clean_module,
        "files": list_module_files(clean_module),
        "saved_count": len(saved_paths),
        "processed_count": processed,
        "skipped_count": skipped,
        "unsupported_files": skipped_files,
        "failed_files": [
            {"file": result.get("file", ""), "error": result.get("error", "Unbekannter Fehler")}
            for result in failed
        ],
    }


@app.post("/process")
async def process_lecture(file: UploadFile = File(...)):
    content = await file.read()

    with tempfile.NamedTemporaryFile(suffix=Path(file.filename).suffix, delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    parsed = parse_document(tmp_path)
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

@app.post("/lecture/summarize")
async def lecture_summarize(body: LectureSummarizeRequest):
    """
    Erstellt eine Vorlesungszusammenfassung (Zwei-Stufen-Generierung).
    Prüft zuerst ob Modul-Profil vorhanden – wenn nicht, needs_onboarding=True.
    """
    from app.lecture import module_profile as mp
    from app.lecture.summarizer import summarize

    # Modul-Profil prüfen
    profile = mp.load(body.module_name)
    if not profile:
        return {
            "needs_onboarding": True,
            "modul_slug": _slug(body.module_name),
            "konzepte": [],
            "summary": "",
            "saved_to": "",
        }

    # Datei finden und parsen
    file_path = _find_file(body.module_name, body.filename)
    parsed = parse_document(file_path)
    if not parsed.success:
        raise HTTPException(status_code=422, detail=f"Datei konnte nicht gelesen werden: {parsed.error_message}")

    # Zwei-Stufen-Zusammenfassung
    result = summarize(body.module_name, parsed.extracted_text)

    return {
        "needs_onboarding": False,
        "modul_slug": profile["slug"],
        "konzepte": result["konzepte"],
        "summary": result["summary"],
        "saved_to": result["saved_to"],
    }


@app.post("/lecture/onboarding")
def lecture_onboarding(body: LectureOnboardingRequest):
    """Speichert Modul-Profil nach Onboarding-Flow in der UI."""
    from app.lecture import module_profile as mp

    profile = mp.create_from_onboarding({
        "name": body.module_name,
        "schwerpunkte": body.schwerpunkte,
        "stil": body.stil,
        "pruefungsrelevant": body.pruefungsrelevant,
    })
    return {"success": True, "slug": profile["slug"]}


@app.get("/lecture/summaries/{module_name}")
def get_lecture_summaries(module_name: str):
    """Listet alle gespeicherten Zusammenfassungen eines Moduls."""
    slug = _slug(module_name)
    summaries_dir = Path("data/processed/summaries") / slug

    if not summaries_dir.exists():
        return {"summaries": []}

    summaries = []
    for md_file in sorted(summaries_dir.glob("*.md"), reverse=True):
        content = md_file.read_text(encoding="utf-8")
        # Titel aus erster H1-Zeile
        titel = md_file.stem.replace("-", " ").title()
        for line in content.splitlines():
            if line.startswith("# "):
                titel = line[2:].strip()
                break
        summaries.append({
            "titel": titel,
            "date": md_file.stem[:10] if len(md_file.stem) >= 10 else "",
            "path": str(md_file),
            "preview": content[:200].replace("\n", " "),
        })

    return {"summaries": summaries}


@app.get("/lecture/summary")
def get_lecture_summary(path: str):
    """Gibt Inhalt einer gespeicherten Zusammenfassung zurück."""
    summary_path = Path(path)
    # Sicherheits-Check: nur Dateien innerhalb data/processed/summaries
    try:
        summary_path.resolve().relative_to(Path("data/processed/summaries").resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Ungültiger Pfad.")

    if not summary_path.exists():
        raise HTTPException(status_code=404, detail="Zusammenfassung nicht gefunden.")

    return {"content": summary_path.read_text(encoding="utf-8")}
