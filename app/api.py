"""
Study Agent FastAPI backend.
"""
import json
import re
import re as _re
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
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
from app.lecture import roadmap as rm
from app.lecture import exam_analyzer as ea
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
        safe_stem = target.stem.replace(" ", "_")
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
    rag_result = rag.ask(
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

    new_md = rm.render_md(body.module_name, data)
    _PENDING_ROADMAPS[body.module_name] = new_md
    return {
        "success": True,
        "is_first_generation": True,
        "diff": None,
        "preview_md": new_md,
    }


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
    return {"exists": True, **rm.parse_md(md)}


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

    results = [process_document(path, config["processed_path"], skip_lecture_processing=True) for path in saved_paths]
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


@app.delete("/modules/{module_name}")
def delete_module(module_name: str):
    """Löscht einen Kurs inkl. Dateien, Chunks, Embeddings und Profil."""
    clean_name = sanitize_module_name(module_name)
    slug = _slug(clean_name)
    clean_lower = clean_name.lower()

    # 1. Raw-Dateien löschen
    raw = module_dir(clean_name)
    if raw.exists():
        shutil.rmtree(raw)

    # 2. Intake-Dateien löschen
    intake_dir = config["raw_path"].parent / "intake" / clean_name
    if intake_dir.exists():
        shutil.rmtree(intake_dir)

    # 3. ChromaDB-Embeddings löschen
    try:
        results = vector_store.collection.get(where={"module_name": clean_name})
        if results and results.get("ids"):
            vector_store.delete(ids=results["ids"])
    except Exception:
        pass

    # 4. JSONL-Chunk-Dateien löschen (case-insensitiv)
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

    # 5. Geparste Dokumente löschen (case-insensitiv)
    parsed_dir = config["processed_path"] / "parsed"
    if parsed_dir.exists():
        for jf in list(parsed_dir.glob("*.json")):
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
                if data.get("module_name", "").lower() == clean_lower:
                    jf.unlink()
            except Exception:
                pass

    # 6. Zusammenfassungen löschen
    summaries_dir = Path("data/processed/summaries") / slug
    if summaries_dir.exists():
        shutil.rmtree(summaries_dir)

    # 7. Modul-Profil löschen
    modules_dir = Path("data/modules")
    for fname in [f"{slug}.json", f"{slug}-exam-profile.md", f"{slug}-history.md"]:
        f = modules_dir / fname
        if f.exists():
            f.unlink()

    # 8. Roadmap löschen
    roadmap_dir = Path("data/processed/roadmaps") / slug
    if roadmap_dir.exists():
        shutil.rmtree(roadmap_dir)

    return {"success": True, "deleted": clean_name}
