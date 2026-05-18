"""
Study Agent FastAPI backend.
"""
import asyncio
import json
import glob as _glob
import hashlib
import re
import shutil
import tempfile
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from anthropic import Anthropic
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
from app.rag.advanced_rag import ask_advanced
from app.utils.config import load_config
from app.vectorstore.chroma_db import ChromaVectorStore
from app.lecture import module_profile as mp
from app.lecture import roadmap as rm
from app.lecture import exam_analyzer as ea
from app.lecture.summarizer import summarize
from app.lecture import exam_generator as eg
from app.lecture import daily_tasks as dt


load_dotenv()

app = FastAPI(title="Study Agent")

client = OpenAI()
anthropic_client = Anthropic()
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

class ExamGenerateRequest(BaseModel):
    module_name: str
    num_tasks: int = 5
    total_points: int = 50

class DailyGenerateRequest(BaseModel):
    mode: str = "konkret"   # "konkret" | "grob"
    daily_hours: float = 2.0

class DailyTaskPatchRequest(BaseModel):
    topic_id: str
    task_index: int
    done: bool

def sanitize_module_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", name)
    if not name:
        raise ValueError("Ungueltiger Modulname.")
    return name


def module_dir(module_name: str) -> Path:
    return RAW_DIR / sanitize_module_name(module_name)


EXAM_FILE_PATTERN = re.compile(r"(probe|alt)?klausur|\bexam\b|\btest\b", re.IGNORECASE)


def _is_exam_file(rel_path: str, profile: Optional[dict]) -> bool:
    """Auto-detect exam files by filename, with manual override from the module profile."""
    profile = profile or {}
    manual_exam = set(profile.get("manual_exam_files") or [])
    manual_not_exam = set(profile.get("manual_not_exam_files") or [])
    if rel_path in manual_exam:
        return True
    if rel_path in manual_not_exam:
        return False
    return bool(EXAM_FILE_PATTERN.search(rel_path))


def list_module_files(module_name: str) -> List[dict]:
    base = module_dir(module_name)
    if not base.exists():
        return []

    profile = mp.load(module_name) or {}
    files = []
    for file_path in sorted(base.rglob("*")):
        if file_path.is_file():
            rel = str(file_path.relative_to(base))
            files.append({
                "name": file_path.name,
                "relative_path": rel,
                "file_type": file_path.suffix.lower().lstrip("."),
                "size": file_path.stat().st_size,
                "is_exam": _is_exam_file(rel, profile),
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
    s = re.sub(r"[äöüß]", lambda m: {"ä":"ae","ö":"oe","ü":"ue","ß":"ss"}[m.group()], s)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


# ── Exam-style cache ──────────────────────────────────────────────────────────

_EXAM_CACHE_DIR = Path("data/modules")


def _exam_cache_hash(module_name: str, exam_files: list[dict]) -> str:
    """MD5 over sorted (name, mtime_ns, size) of the module's exam files."""
    base = module_dir(module_name)
    parts = []
    for f in sorted(exam_files, key=lambda x: x["name"]):
        path = base / f["relative_path"]
        try:
            st = path.stat()
            parts.append(f"{f['name']}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{f['name']}:0:0")
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def _load_exam_cache(module_name: str) -> dict | None:
    cache_path = _EXAM_CACHE_DIR / f"{_slug(module_name)}-exam-style-cache.json"
    if not cache_path.exists():
        return None
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_exam_cache(module_name: str, hash_val: str, style: str, profile_md: str) -> None:
    _EXAM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _EXAM_CACHE_DIR / f"{_slug(module_name)}-exam-style-cache.json"
    cache_path.write_text(
        json.dumps({"hash": hash_val, "style": style, "exam_profile_md": profile_md},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _exam_analyze_cached(module_name: str, all_files: list[dict]) -> tuple[str, str]:
    """Run exam analysis using cache when unchanged. Returns (exam_profile_md, exam_style)."""
    exam_files = [f for f in all_files if f.get("is_exam")]
    if not exam_files:
        return "", ""

    current_hash = _exam_cache_hash(module_name, exam_files)
    cache = _load_exam_cache(module_name)
    if cache and cache.get("hash") == current_hash:
        return cache.get("exam_profile_md", ""), cache.get("style", "")

    profile = mp.load(module_name)
    if not profile:
        profile = mp.create_from_onboarding({
            "name": module_name,
            "schwerpunkte": [],
            "stil": "mixed",
            "pruefungsrelevant": [],
        })

    exam_texts = _collect_exam_text(module_name)
    exam_profile_md = ""
    exam_style = ""

    if exam_texts:
        try:
            ea.analyze(module_name, exam_texts)
            profile = mp.load(module_name) or profile
        except Exception as exc:
            print(f"[exam_cache] ea.analyze failed: {exc}")
        exam_profile_md = mp.load_exam_profile(profile)
        try:
            exam_style = eg.analyze_exam_style(exam_texts, module_name)
        except Exception as exc:
            print(f"[exam_cache] style analysis failed: {exc}")

    _save_exam_cache(module_name, current_hash, exam_style, exam_profile_md)
    return exam_profile_md, exam_style


@app.get("/", response_class=HTMLResponse)
def serve_ui():
    html_path = Path(__file__).parent / "static" / "index.html"
    return html_path.read_text(encoding="utf-8")


@app.post("/ask")
async def ask(body: Question):
    answer_parts: List[str] = []
    sources: list = []
    path = "simple"
    async for raw in ask_advanced(body.question, body.module_name, rag):
        data = json.loads(raw)
        if data["type"] == "token":
            answer_parts.append(data["content"])
        elif data["type"] == "done":
            sources = data.get("sources", [])
            path = data.get("path", "simple")
    return {"answer": "".join(answer_parts), "sources": sources, "path": path}


@app.post("/ask/stream")
async def ask_stream(body: Question):
    async def event_stream():
        async for raw in ask_advanced(body.question, body.module_name, rag):
            yield f"data: {raw}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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


def _resolve_module_file(module_name: str, rel_path: str) -> Path:
    """Resolve a module-relative path safely (path-traversal guard)."""
    if not rel_path:
        raise HTTPException(status_code=400, detail="Pfad fehlt.")
    base = module_dir(module_name).resolve()
    target = (base / rel_path).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=403, detail="Ungueltiger Pfad.")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Datei nicht gefunden.")
    return target


_MEDIA_TYPES = {
    ".pdf":  "application/pdf",
    ".txt":  "text/plain; charset=utf-8",
    ".md":   "text/markdown; charset=utf-8",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@app.get("/modules/{module_name}/raw")
def get_module_raw(module_name: str, path: str):
    """Stream the raw file (PDF/PPTX/TXT/MD) for in-browser preview."""
    target = _resolve_module_file(module_name, path)
    media_type = _MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")
    return FileResponse(
        target,
        media_type=media_type,
        filename=target.name,
        headers={"Content-Disposition": f'inline; filename="{target.name}"'},
    )


@app.delete("/modules/{module_name}/file")
def delete_module_file(module_name: str, path: str):
    """
    Delete a single file from a module: removes the raw file, its chunks JSONL,
    its embeddings from Chroma, and its parsed JSON. Path-traversal guarded.
    """
    target = _resolve_module_file(module_name, path)
    target_norm = str(target).replace("\\", "/").lower()

    # 1) Find chunks JSONL files whose first chunk's metadata.source matches.
    matched_chunk_files = []
    chunks_dir = config["processed_path"] / "chunks"
    if chunks_dir.exists():
        for jsonl in chunks_dir.glob("*.jsonl"):
            try:
                first = jsonl.read_text(encoding="utf-8").splitlines()[0]
                chunk = json.loads(first)
                src = chunk.get("metadata", {}).get("source", "") or chunk.get("source", "")
                if src and src.replace("\\", "/").lower() == target_norm:
                    matched_chunk_files.append(jsonl)
            except Exception:
                pass

    # 2) Collect Chroma chunk-IDs whose source-metadata matches this file.
    chroma_ids_to_delete = []
    try:
        # Scope by module first to avoid scanning the whole index.
        scoped = vector_store.collection.get(where={"module_name": sanitize_module_name(module_name)})
        for cid, meta in zip(scoped.get("ids", []), scoped.get("metadatas", [])):
            src = (meta or {}).get("source", "")
            if src and src.replace("\\", "/").lower() == target_norm:
                chroma_ids_to_delete.append(cid)
        # Fallback for legacy chunks without module_name in metadata.
        if not chroma_ids_to_delete:
            unscoped = vector_store.collection.get()
            for cid, meta in zip(unscoped.get("ids", []), unscoped.get("metadatas", [])):
                src = (meta or {}).get("source", "")
                if src and src.replace("\\", "/").lower() == target_norm:
                    chroma_ids_to_delete.append(cid)
    except Exception:
        pass

    # 3) Now actually delete: file → chunks files → embeddings → parsed JSON.
    deleted_name = target.name
    try:
        target.unlink()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Datei konnte nicht gelöscht werden: {exc}")

    for jf in matched_chunk_files:
        try: jf.unlink()
        except Exception: pass

    if chroma_ids_to_delete:
        try: vector_store.delete(ids=chroma_ids_to_delete)
        except Exception: pass

    parsed_dir = config["processed_path"] / "parsed"
    if parsed_dir.exists():
        safe_stem = _glob.escape(target.stem.replace(" ", "_"))
        for jf in parsed_dir.glob(f"{safe_stem}_*.json"):
            try: jf.unlink()
            except Exception: pass

    return {
        "success": True,
        "deleted": deleted_name,
        "chunks_files_removed": len(matched_chunk_files),
        "embeddings_removed": len(chroma_ids_to_delete),
    }


@app.post("/modules/{module_name}/file/exam-flag")
def toggle_exam_flag(module_name: str, path: str):
    """Toggle the manual exam-flag for a single file (overrides auto-detection)."""
    target = _resolve_module_file(module_name, path)
    rel = str(target.relative_to(module_dir(module_name).resolve()))

    profile = mp.load(module_name)
    if not profile:
        profile = mp.create_from_onboarding({
            "name": module_name,
            "schwerpunkte": [],
            "stil": "mixed",
            "pruefungsrelevant": [],
        })

    auto_detected = bool(EXAM_FILE_PATTERN.search(rel))
    manual_exam = list(profile.get("manual_exam_files") or [])
    manual_not_exam = list(profile.get("manual_not_exam_files") or [])
    currently = (rel in manual_exam) or (auto_detected and rel not in manual_not_exam)

    desired = not currently
    manual_exam = [f for f in manual_exam if f != rel]
    manual_not_exam = [f for f in manual_not_exam if f != rel]
    if desired and not auto_detected:
        manual_exam.append(rel)
    elif (not desired) and auto_detected:
        manual_not_exam.append(rel)

    profile["manual_exam_files"] = manual_exam
    profile["manual_not_exam_files"] = manual_not_exam
    mp.save(profile)
    return {"success": True, "is_exam": desired}


# ─────────────────────── Roadmap endpoints ────────────────────────────────────

class SolveSheetRequest(BaseModel):
    sheet_text: str
    module_id: str


class RoadmapGenerateRequest(BaseModel):
    module_name: str
    exam_date: Optional[str] = None
    focus: Optional[str] = None


class RoadmapStatusRequest(BaseModel):
    status: str  # "todo" | "doing" | "done"


# In-memory pending-roadmap cache: {module_name: pending_md}
_PENDING_ROADMAPS: dict = {}


def _collect_exam_text(module_name: str) -> List[str]:
    """Parse all files marked as exam into plain text (for exam_analyzer)."""
    texts: List[str] = []
    for f in list_module_files(module_name):
        if not f.get("is_exam"):
            continue
        try:
            target = _resolve_module_file(module_name, f["relative_path"])
            parsed = parse_document(target)
            if parsed.success and parsed.extracted_text:
                texts.append(parsed.extracted_text)
        except Exception as exc:
            print(f"[roadmap] could not parse exam file {f['name']}: {exc}")
    return texts


@app.post("/roadmap/generate")
async def roadmap_generate(body: RoadmapGenerateRequest):
    """
    Generate a fresh roadmap. If an old one exists, smart-merge status across.
    Returns preview_md + diff WITHOUT writing — call /roadmap/{m}/accept to commit.
    """
    # 1) Course context via RAG — use higher top_k to cover more files.
    rag_result = await asyncio.to_thread(
        rag.ask,
        "Liste alle wichtigen Themen, Konzepte und Aufgaben aus den Materialien dieses Kurses auf.",
        module_name=body.module_name,
        top_k=20,
    )
    course_context = rag_result.get("answer", "") or "Keine Kursinhalte gefunden."

    # Collect all actual file names for the module so the LLM cannot invent names.
    all_module_files = list_module_files(body.module_name)
    module_files = [f["name"] for f in all_module_files]
    exam_file_names = [f["name"] for f in all_module_files if f.get("is_exam")]

    # 2) Run exam_analyzer on marked exam files (best-effort — never fatal).
    exam_profile_md = ""
    profile = mp.load(body.module_name)
    if not profile and exam_file_names:
        # Auto-create a minimal profile so exam analysis can run even without onboarding.
        profile = mp.create_from_onboarding({
            "name": body.module_name,
            "schwerpunkte": [],
            "stil": "mixed",
            "pruefungsrelevant": [],
        })
    if profile:
        exam_texts = _collect_exam_text(body.module_name)
        if exam_texts:
            try:
                ea.analyze(body.module_name, exam_texts)
                profile = mp.load(body.module_name) or profile
            except Exception as exc:
                print(f"[roadmap] exam_analyzer failed: {exc}")
        exam_profile_md = mp.load_exam_profile(profile)

    # 3) Generate fresh — no smart-merge, every generation is a clean slate.
    try:
        data = rm.generate(
            body.module_name,
            exam_date=body.exam_date or "",
            focus=body.focus or "",
            course_context=course_context,
            exam_profile=exam_profile_md,
            available_files=module_files,
            exam_files=exam_file_names,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Roadmap-Generation fehlgeschlagen: {exc}")

    # Scale topic hours to reflect the available study time before the exam.
    if body.exam_date:
        rm.scale_hours_to_exam_date(data, body.exam_date)

    new_md = rm.render_md(body.module_name, data)
    _PENDING_ROADMAPS[body.module_name] = new_md
    return {
        "success": True,
        "is_first_generation": True,
        "diff": None,
        "preview_md": new_md,
    }


@app.post("/roadmap/generate/stream")
async def roadmap_generate_stream(body: RoadmapGenerateRequest):
    """SSE version of /roadmap/generate — emits step events then a result event."""

    async def event_gen():
        module_name = body.module_name
        all_files = list_module_files(module_name)
        module_files = [f["name"] for f in all_files]
        exam_file_names = [f["name"] for f in all_files if f.get("is_exam")]

        yield f"data: {json.dumps({'type': 'step', 'key': 'rag', 'label': 'Kursinhalte laden…', 'done': False})}\n\n"
        yield f"data: {json.dumps({'type': 'step', 'key': 'analyze', 'label': 'Klausurstil analysieren…', 'done': False})}\n\n"

        try:
            rag_coro = asyncio.to_thread(
                rag.ask,
                "Liste alle wichtigen Themen, Konzepte und Aufgaben aus den Materialien dieses Kurses auf.",
                module_name=module_name,
                top_k=20,
            )
            analyze_coro = asyncio.to_thread(_exam_analyze_cached, module_name, all_files)

            rag_result, (exam_profile_md, _) = await asyncio.gather(rag_coro, analyze_coro)

            course_context = rag_result.get("answer", "") or "Keine Kursinhalte gefunden."

            yield f"data: {json.dumps({'type': 'step', 'key': 'rag', 'label': 'Kursinhalte geladen', 'done': True})}\n\n"
            yield f"data: {json.dumps({'type': 'step', 'key': 'analyze', 'label': 'Klausurstil analysiert', 'done': True})}\n\n"
            yield f"data: {json.dumps({'type': 'step', 'key': 'generate', 'label': 'Roadmap generieren…', 'done': False})}\n\n"

            data = await asyncio.to_thread(
                rm.generate,
                module_name,
                exam_date=body.exam_date or "",
                focus=body.focus or "",
                course_context=course_context,
                exam_profile=exam_profile_md,
                available_files=module_files,
                exam_files=exam_file_names,
            )

            if body.exam_date:
                rm.scale_hours_to_exam_date(data, body.exam_date)

            new_md = rm.render_md(module_name, data)
            _PENDING_ROADMAPS[module_name] = new_md

            payload = {
                "success": True,
                "is_first_generation": True,
                "diff": None,
                "preview_md": new_md,
            }
            yield f"data: {json.dumps({'type': 'result', 'data': payload})}\n\n"

        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/roadmap/{module_name}/accept")
def roadmap_accept(module_name: str):
    pending = _PENDING_ROADMAPS.pop(module_name, None)
    if not pending:
        raise HTTPException(status_code=404, detail="Keine ausstehende Generation gefunden.")
    rm.save_roadmap_md(module_name, pending)
    return {"success": True}


@app.post("/roadmap/{module_name}/reject")
def roadmap_reject(module_name: str):
    _PENDING_ROADMAPS.pop(module_name, None)
    return {"success": True}


@app.get("/roadmap/{module_name}")
def roadmap_get(module_name: str):
    md = rm.load_roadmap_md(module_name)
    if not md:
        return {"exists": False}
    parsed = rm.parse_md(md)
    if parsed.get("exam_date"):
        rm.scale_hours_to_exam_date(parsed, parsed["exam_date"])
    return {"exists": True, **parsed}


@app.patch("/roadmap/{module_name}/topic/{topic_id}")
def roadmap_update_status(module_name: str, topic_id: str, body: RoadmapStatusRequest):
    if body.status not in ("todo", "doing", "done"):
        raise HTTPException(status_code=400, detail="status muss todo|doing|done sein.")
    md = rm.load_roadmap_md(module_name)
    if not md:
        raise HTTPException(status_code=404, detail="Keine Roadmap gefunden.")
    md = rm.update_topic_status(md, topic_id, body.status)
    rm.save_roadmap_md(module_name, md)
    return {"success": True}


@app.delete("/roadmap/{module_name}")
def roadmap_delete(module_name: str):
    deleted = rm.delete_roadmap(module_name)
    return {"success": deleted}


@app.get("/modules/{module_name}/text")
def get_module_text(module_name: str, path: str):
    """Return extracted plain text for the file (re-parsed live; ~100 ms for PDFs)."""
    target = _resolve_module_file(module_name, path)
    parsed = parse_document(target)
    if not parsed.success:
        raise HTTPException(status_code=422, detail=f"Datei konnte nicht gelesen werden: {parsed.error_message}")
    return {
        "file_name": target.name,
        "file_type": parsed.file_type,
        "text": parsed.extracted_text,
    }


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

    with ThreadPoolExecutor(max_workers=min(len(saved_paths), 4)) as pool:
        futures = {pool.submit(process_document, path, config["processed_path"], True): path for path in saved_paths}
        results = [f.result() for f in as_completed(futures)]
    index_chunks(config)

    processed = sum(1 for result in results if result.get("parseSuccess"))
    skipped = sum(1 for result in results if result.get("skipped"))
    failed = [result for result in results if not result.get("parseSuccess")]

    needs_onboarding = not bool(mp.load(clean_module))

    return {
        "success": True,
        "module_name": clean_module,
        "files": list_module_files(clean_module),
        "saved_count": len(saved_paths),
        "processed_count": processed,
        "skipped_count": skipped,
        "unsupported_files": skipped_files,
        "needs_onboarding": needs_onboarding,
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

    try:
        parsed = parse_document(tmp_path)
        text = parsed.extracted_text
    finally:
        tmp_path.unlink(missing_ok=True)

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
    _summaries_root = (Path(__file__).parent.parent / "data/processed/summaries").resolve()
    try:
        summary_path.resolve().relative_to(_summaries_root)
    except ValueError:
        raise HTTPException(status_code=403, detail="Ungültiger Pfad.")

    if not summary_path.exists():
        raise HTTPException(status_code=404, detail="Zusammenfassung nicht gefunden.")

    return {"content": summary_path.read_text(encoding="utf-8")}


@app.post("/solve-sheet")
async def solve_sheet(body: SolveSheetRequest):
    from app.router import HybridRouter
    from app.solver import ExerciseSheetSolver

    router = HybridRouter(vector_store=vector_store, embedder=embedder, client=anthropic_client)
    solver = ExerciseSheetSolver(router=router, client=anthropic_client)

    results = await solver.solve(body.sheet_text, body.module_id)

    total_tokens = sum(r.tokens_used for r in results)
    models_used: dict = {}
    for r in results:
        models_used[r.model_used] = models_used.get(r.model_used, 0) + 1

    return {
        "results": [
            {
                "aufgabe_nr": r.aufgabe_nr,
                "aufgabe_text": r.aufgabe_text,
                "loesung": r.loesung,
                "model_used": r.model_used,
                "route": r.route,
                "tokens_used": r.tokens_used,
            }
            for r in results
        ],
        "total_tokens": total_tokens,
        "models_used": models_used,
    }


@app.delete("/modules/{module_name}")
def delete_module(module_name: str):
    """Löscht einen Kurs inkl. Dateien, Chunks, Embeddings und Profil."""
    clean_name = sanitize_module_name(module_name)
    slug = _slug(clean_name)
    clean_lower = clean_name.lower()

    # 1. OCR-Cache-Dateien löschen (vor raw, solange Dateinamen noch bekannt sind)
    ocr_cache_dir = Path("data/processed/ocr")
    raw = module_dir(clean_name)
    if raw.exists() and ocr_cache_dir.exists():
        for f in raw.rglob("*"):
            if f.is_file():
                cache_file = ocr_cache_dir / f"{f.stem}.ocr.json"
                if cache_file.exists():
                    try:
                        cache_file.unlink()
                    except Exception:
                        pass

    # 2. Raw-Dateien löschen
    if raw.exists():
        shutil.rmtree(raw)

    # 3. Intake-Dateien löschen
    intake_dir = config["raw_path"].parent / "intake" / clean_name
    if intake_dir.exists():
        shutil.rmtree(intake_dir)

    # 4. ChromaDB-Embeddings löschen
    try:
        results = vector_store.collection.get(where={"module_name": clean_name})
        if results and results.get("ids"):
            vector_store.delete(ids=results["ids"])
    except Exception:
        pass

    # 5. JSONL-Chunk-Dateien löschen (case-insensitiv)
    chunks_dir = config["processed_path"] / "chunks"
    if chunks_dir.exists():
        for jsonl in list(chunks_dir.glob("*.jsonl")):
            try:
                first_line = jsonl.read_text(encoding="utf-8").splitlines()[0]
                chunk = json.loads(first_line)
                meta = chunk.get("metadata", {})
                src = meta.get("source", chunk.get("source", ""))
                module_name_meta = meta.get("module_name", "")
                if (module_name_meta.lower() == clean_lower or
                        any(p.lower() == clean_lower for p in Path(src).parts)):
                    jsonl.unlink()
            except Exception:
                pass

    # 6. Geparste Dokumente löschen (case-insensitiv)
    parsed_dir = config["processed_path"] / "parsed"
    if parsed_dir.exists():
        for jf in list(parsed_dir.glob("*.json")):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                if data.get("module_name", "").lower() == clean_lower:
                    jf.unlink()
            except Exception:
                pass

    # 7. Zusammenfassungen löschen
    summaries_dir = Path("data/processed/summaries") / slug
    if summaries_dir.exists():
        shutil.rmtree(summaries_dir)

    # 8. Daily-Tasks löschen
    daily_tasks_dir = Path("data/processed/daily_tasks") / slug
    if daily_tasks_dir.exists():
        shutil.rmtree(daily_tasks_dir)

    # 9. Modul-Profil löschen
    modules_dir = Path("data/modules")
    for fname in [f"{slug}.json", f"{slug}-exam-profile.md", f"{slug}-history.md"]:
        f = modules_dir / fname
        if f.exists():
            f.unlink()

    # 10. Roadmap löschen
    roadmap_dir = Path("data/processed/roadmaps") / slug
    if roadmap_dir.exists():
        shutil.rmtree(roadmap_dir)

    return {"success": True, "deleted": clean_name}


# ─────────────────────── Probeklausur endpoints ───────────────────────────────

@app.post("/exam/generate")
async def exam_generate(body: ExamGenerateRequest):
    clean_name = sanitize_module_name(body.module_name)

    existing = eg.list_exams(clean_name)
    if len(existing) >= 100:
        raise HTTPException(
            status_code=400,
            detail="Limit von 100 Klausuren erreicht. Bitte alte Klausuren löschen.",
        )

    # Schritt 1 (optional): Klausurstil analysieren
    exam_style = ""
    exam_texts = _collect_exam_text(clean_name)
    if exam_texts:
        try:
            exam_style = eg.analyze_exam_style(exam_texts, clean_name)
        except Exception as exc:
            print(f"[exam_generate] Stilanalyse fehlgeschlagen (nicht fatal): {exc}")

    # RAG-Kontext
    rag_result = rag.ask(
        "Fasse alle wichtigen Konzepte, Definitionen, Methoden und prüfungsrelevanten Inhalte zusammen.",
        module_name=clean_name,
        top_k=20,
    )
    rag_context = rag_result.get("answer", "") or ""
    if not rag_context:
        raise HTTPException(
            status_code=422,
            detail="Keine Inhalte für dieses Modul gefunden. Bitte zuerst Materialien hochladen und verarbeiten.",
        )

    try:
        md_content = eg.generate(
            module_name=clean_name,
            exam_style=exam_style,
            rag_context=rag_context,
            num_tasks=body.num_tasks,
            total_points=body.total_points,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Generierung fehlgeschlagen: {exc}")

    n = eg.save_exam(clean_name, md_content)
    return {"success": True, "n": n, "module_name": clean_name}


@app.get("/exam/{module_name}")
def exam_list(module_name: str):
    clean_name = sanitize_module_name(module_name)
    return {"module_name": clean_name, "exams": eg.list_exams(clean_name)}


@app.get("/exam/{module_name}/{n}")
def exam_get(module_name: str, n: int):
    clean_name = sanitize_module_name(module_name)
    content = eg.load_exam(clean_name, n)
    if content is None:
        raise HTTPException(status_code=404, detail=f"Klausur {n} nicht gefunden.")
    return {"module_name": clean_name, "n": n, "content": content}


@app.delete("/exam/{module_name}/{n}")
def exam_delete(module_name: str, n: int):
    clean_name = sanitize_module_name(module_name)
    if not eg.delete_exam(clean_name, n):
        raise HTTPException(status_code=404, detail=f"Klausur {n} nicht gefunden.")
    return {"success": True}


# ─────────────────────── Daily tasks endpoints ────────────────────────────────

@app.get("/daily/{module_name}")
def daily_get(module_name: str):
    """Load active daily plan. Returns {exists: false} if none."""
    clean = sanitize_module_name(module_name)
    md = dt.load_plan(clean)
    if not md:
        return {"exists": False}
    parsed = dt.parse_plan(md)
    return {"exists": True, **parsed}


@app.post("/daily/{module_name}/generate")
async def daily_generate(module_name: str, body: DailyGenerateRequest):
    """Generate a new daily plan. Archives old plan and applies carryover."""
    try:
        clean = sanitize_module_name(module_name)
        if body.mode not in ("konkret", "grob"):
            raise HTTPException(status_code=400, detail="mode muss 'konkret' oder 'grob' sein.")
        if body.daily_hours < 0.5 or body.daily_hours > 12:
            raise HTTPException(status_code=400, detail="daily_hours muss zwischen 0.5 und 12 liegen.")

        roadmap_md = rm.load_roadmap_md(clean)
        if not roadmap_md:
            raise HTTPException(status_code=404, detail="Keine Roadmap gefunden. Erst Roadmap generieren.")
        roadmap_data = rm.parse_md(roadmap_md)

        def rag_fn(question: str, _module: str = clean, top_k: int = 8) -> str:
            return ""

        new_md = dt.generate(
            clean,
            mode=body.mode,
            daily_hours=body.daily_hours,
            roadmap_data=roadmap_data,
            rag_fn=rag_fn,
        )
        parsed = dt.parse_plan(new_md)
        return {"success": True, **parsed}
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Plan-Generierung fehlgeschlagen: {exc}")


@app.patch("/daily/{module_name}/task")
def daily_task_patch(module_name: str, body: DailyTaskPatchRequest):
    """Toggle a single task checkbox (done or undone)."""
    clean = sanitize_module_name(module_name)
    try:
        updated_md = dt.toggle_task(clean, body.topic_id, body.task_index, body.done)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    parsed = dt.parse_plan(updated_md)
    return {"success": True, "progress": parsed["progress"]}


@app.get("/daily/{module_name}/stats")
def daily_stats(module_name: str):
    """Return completion stats: total tasks done, per-topic counts, per-day counts."""
    clean = sanitize_module_name(module_name)
    return dt.get_stats(clean)


@app.get("/daily/{module_name}/review")
def daily_review(module_name: str, count: int = 2):
    """Return `count` randomly-sampled completed tasks for spaced-repetition display."""
    clean = sanitize_module_name(module_name)
    return {"tasks": dt.get_review_tasks(clean, count)}


@app.delete("/daily/{module_name}/history")
def daily_history_delete(module_name: str):
    """Delete the completed-task history for a module."""
    clean = sanitize_module_name(module_name)
    p = dt.task_history_path(clean)
    if p.exists():
        p.unlink()
    return {"success": True}
