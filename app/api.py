"""
Study Agent FastAPI backend.
"""
import asyncio
import contextvars
import io
import json
import glob as _glob
import hashlib
import mimetypes
import os
import re
import shutil
import tempfile
import time
import traceback
import zipfile
from datetime import date as _date
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from anthropic import Anthropic
from openai import OpenAI
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

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
from app.vectorstore.pgvector_store import PgVectorStore
from app.storage.supabase_client import (
    get_client as _supa_client,
    get_user_id as _supa_uid,
    allow_fallback_user as _allow_fallback_user,
)
from app.lecture import module_profile as mp
from app.lecture import roadmap as rm
from app.lecture import exam_analyzer as ea
from app.lecture.summarizer import summarize
from app.lecture import exam_generator as eg
from app.lecture import daily_tasks as dt
from app.lecture import topic_pool as tp
from app.lecture import topic_quiz as tq
from app.lecture import topic_worksheet as tw
from app.chat import orchestrator as chat_orchestrator
from app.chat import tools as chat_tools


load_dotenv(override=True)

app = FastAPI(title="Study Agent")

# Ensure woff2 gets the right MIME type — otherwise StaticFiles serves it as
# text/plain, which the global "X-Content-Type-Options: nosniff" header makes the
# browser reject ("not a supported font type"). Must be set before the mount.
mimetypes.add_type("font/woff2", ".woff2")

# Self-hosted fonts (woff2 + fonts.css) — served locally so the browser never
# contacts Google's font servers (DSGVO: no user IP sent to the US on page load).
app.mount(
    "/fonts",
    StaticFiles(directory=Path(__file__).parent / "static" / "fonts"),
    name="fonts",
)

# ── Rate limiting (slowapi) ──────────────────────────────────────────────────
# Keyed per authenticated user (the Supabase user-id the auth middleware stores
# in a contextvar); falls back to the dev/env user-id when auth is disabled.
# In-memory storage by default — fine for single-process uvicorn. Point
# RATELIMIT_STORAGE_URI at redis://… before scaling to multiple workers.
RL_CHAT = os.getenv("RATELIMIT_CHAT", "15/minute")             # light: /ask, /ask/stream
RL_HEAVY = os.getenv("RATELIMIT_HEAVY", "5/minute")            # heavy: generation endpoints
RL_HEAVY_DAILY = os.getenv("RATELIMIT_HEAVY_DAILY", "50/day")  # shared per-user daily budget
RL_UPLOAD = os.getenv("RATELIMIT_UPLOAD", "10/minute")         # uploads: parsing + paid embeddings
RL_WORKSHEET = os.getenv("RATELIMIT_WORKSHEET", "10/minute")   # worksheet gen: unlimited/day, only throttled per-minute
_RL_ENABLED = os.getenv("RATELIMIT_ENABLED", "1").lower() not in ("0", "false", "no")

# Chat orchestrator model: Claude Haiku for reliable tool-calling routing.
CHAT_MODEL = os.getenv("CHAT_MODEL", "claude-haiku-4-5-20251001")

# ── Payload size limits ──────────────────────────────────────────────────────
# Guards against memory-exhaustion DoS (huge uploads) and runaway LLM cost
# (huge text bodies forwarded to paid Anthropic/OpenAI calls). All env-tunable.
MAX_REQUEST_BYTES = int(os.getenv("MAX_REQUEST_BYTES", str(500 * 1024 * 1024)))      # 500 MB total body
MAX_UPLOAD_FILE_BYTES = int(os.getenv("MAX_UPLOAD_FILE_BYTES", str(50 * 1024 * 1024)))  # 50 MB per file
MAX_TEXT_CHARS = int(os.getenv("MAX_TEXT_CHARS", "50000"))                            # free-text fields


def _rate_limit_key(request: Request) -> str:
    """Rate-limit bucket = the current authenticated user (per-request contextvar)."""
    return _supa_uid()


limiter = Limiter(
    key_func=_rate_limit_key,
    storage_uri=os.getenv("RATELIMIT_STORAGE_URI", "memory://"),
    enabled=_RL_ENABLED,
)
app.state.limiter = limiter


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """429 with a Retry-After header so the frontend can back off.

    A custom handler (not slowapi's default) is needed because emitting headers
    the default way requires headers_enabled=True, which forces every endpoint to
    return a starlette Response — ours return plain dicts and StreamingResponses.
    Setting Retry-After only on this error response leaves the success paths
    untouched. Retry-After = the limit's window length in seconds (worst-case wait).
    """
    try:
        retry_after = int(exc.limit.limit.get_expiry())
    except Exception:
        retry_after = 60
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate-Limit erreicht ({exc.detail}). Bitte kurz warten."},
        headers={"Retry-After": str(retry_after)},
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

# Shared daily budget: every heavy LLM endpoint draws from ONE per-user/day bucket.
_heavy_daily = limiter.shared_limit(RL_HEAVY_DAILY, scope="llm_heavy_daily")

# Per-user "only one at a time" guard for unlimited worksheet generation. In-process
# set of user-ids with a generation in flight — single-worker uvicorn, same assumption
# as the in-memory rate-limit storage above. A second concurrent request → 409.
_worksheet_inflight: set = set()

client = OpenAI()
anthropic_client = Anthropic()                 # real Claude: file generation, router, solver
config = load_config()
# RAW_DIR kept for legacy compatibility; uploads now go to Supabase Storage
RAW_DIR = config["raw_path"]
RAW_DIR.mkdir(parents=True, exist_ok=True)

# CORS origins are env-driven: set FRONTEND_ORIGIN to your deployed frontend URL
# (comma-separated for several) in production. Defaults to "*" so local dev keeps
# working out of the box. Credentials are intentionally NOT allowed — auth rides
# in the Authorization header (Bearer token), never in cookies, so a wildcard
# origin stays safe here.
_cors_origins = [o.strip() for o in os.getenv("FRONTEND_ORIGIN", "*").split(",") if o.strip()] or ["*"]
# Compress responses ≥1 KB when the client accepts gzip. The app shell
# (index.html, ~512 KB) shrinks to a fraction of that; starlette's gzip
# flushes per-chunk, so the SSE streaming endpoints keep working.
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Auth middleware ────────────────────────────────────────────────────────────
# Auth is enforced whenever Supabase is configured. Token verification is
# delegated to Supabase's own API (GET /auth/v1/user) so it works regardless of
# which JWT algorithm the project uses (HS256, ES256, …).
#
# Auth is only disabled when EXPLICITLY opted in via DEV_NO_AUTH *and* Supabase
# is genuinely absent. A deploy that simply forgot SUPABASE_* must fail closed
# (503) — never silently fall through to a shared, unauthenticated fallback user.
_SUPABASE_CONFIGURED = bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY"))
_DEV_NO_AUTH = os.getenv("DEV_NO_AUTH", "0").lower() in ("1", "true", "yes")
_AUTH_DISABLED = _DEV_NO_AUTH and not _SUPABASE_CONFIGURED

# In the explicit dev-no-auth mode there is no per-request JWT, so the data layer
# must be allowed to fall back to the env user + admin client. In every other
# mode the fallback stays OFF so a lost request context fails closed.
if _AUTH_DISABLED:
    _allow_fallback_user(True)


# Short-TTL token-verification cache: sha256(token) → (user_id, expires_at).
# Verifying via Supabase (GET /auth/v1/user) costs a full network round-trip on
# EVERY API call — with a UI that fires many requests per interaction this
# dominates latency. Supabase access tokens live ~1 h anyway, so re-verifying
# a token seen seconds ago adds no security; a revoked session is locked out
# at most AUTH_CACHE_TTL seconds later. Set AUTH_CACHE_TTL=0 to disable.
_AUTH_CACHE_TTL = float(os.getenv("AUTH_CACHE_TTL", "120"))
_AUTH_CACHE_MAX = 1000
_auth_cache: dict[str, tuple[str, float]] = {}


def _auth_cache_get(token: str) -> str | None:
    if _AUTH_CACHE_TTL <= 0:
        return None
    key = hashlib.sha256(token.encode()).hexdigest()
    entry = _auth_cache.get(key)
    if entry and entry[1] > time.monotonic():
        return entry[0]
    _auth_cache.pop(key, None)
    return None


def _auth_cache_put(token: str, user_id: str) -> None:
    if _AUTH_CACHE_TTL <= 0:
        return
    if len(_auth_cache) >= _AUTH_CACHE_MAX:
        now = time.monotonic()
        for k in [k for k, v in _auth_cache.items() if v[1] <= now]:
            _auth_cache.pop(k, None)
        if len(_auth_cache) >= _AUTH_CACHE_MAX:
            _auth_cache.clear()
    key = hashlib.sha256(token.encode()).hexdigest()
    _auth_cache[key] = (user_id, time.monotonic() + _AUTH_CACHE_TTL)


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    from app.storage.supabase_client import (
        set_request_user_id,
        set_request_token,
        get_admin_client,
        close_request_client,
    )

    # CORS preflight: browsers send OPTIONS without credentials, so let the CORS
    # middleware answer it. Otherwise a cross-origin deploy (FRONTEND_ORIGIN set
    # to a different host) would get 401s on every preflight and break all
    # non-simple requests. Preflights carry no token and perform no action.
    if request.method == "OPTIONS":
        return await call_next(request)

    # Landing page, app shell, upgrade page, legal pages and logo are always
    # public (serve without a token — legal pages MUST be reachable pre-login).
    # Self-hosted fonts under /fonts/ must load on the public/legal pages too.
    if request.url.path in (
        "/", "/app", "/upgrade", "/logo.png", "/logo_white.png",
        "/impressum", "/datenschutz", "/agb",
        "/hilfe", "/verschenken", "/mehr-erfahren",
    ) or request.url.path.startswith("/fonts/"):
        return await call_next(request)

    try:
        # Dev mode (explicit opt-in, Supabase absent): use the fixed fallback user.
        if _AUTH_DISABLED:
            set_request_user_id(os.getenv("SUPABASE_USER_ID", "00000000-0000-0000-0000-000000000001"))
            return await call_next(request)

        # Misconfiguration (Supabase env missing, dev-no-auth NOT requested):
        # fail closed instead of running unauthenticated.
        if not _SUPABASE_CONFIGURED:
            return JSONResponse(status_code=503, content={"detail": "Auth backend not configured"})

        # Credentials are only accepted in the Authorization header — never in the URL
        # query string, which would leak the token into access logs, browser history
        # and Referer headers. Browser-native loads (PDF iframe etc.) fetch the bytes
        # via the auth'd fetch() wrapper and render them from a blob: URL.
        auth = request.headers.get("Authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else ""
        if not token:
            return JSONResponse(status_code=401, content={"detail": "Not authenticated"})
        cached_uid = _auth_cache_get(token)
        if cached_uid:
            set_request_user_id(cached_uid)
            set_request_token(token)
        else:
            try:
                # Verify the token via Supabase using the service-role admin client
                # (GET /auth/v1/user). Works with any JWT algorithm.
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(None, lambda: get_admin_client().auth.get_user(token))
                if not (response and response.user):
                    return JSONResponse(status_code=401, content={"detail": "Invalid token"})
                # Scope every downstream query/storage call to this user. The token is
                # stashed so get_client() builds an RLS-enforcing, user-scoped client.
                set_request_user_id(response.user.id)
                set_request_token(token)
                _auth_cache_put(token, response.user.id)
            except Exception:
                return JSONResponse(status_code=401, content={"detail": "Invalid token"})

        return await call_next(request)
    finally:
        # Release the per-request user client (and clear its context).
        close_request_client()


# ── Body-size guard ──────────────────────────────────────────────────────────
# Added last → outermost middleware → runs first, so oversized requests are
# rejected before auth/processing. Covers the common (honest Content-Length)
# case for every endpoint; `_read_capped` is the backstop when the header is
# missing or lies (e.g. chunked uploads).
@app.middleware("http")
async def _limit_body_size(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_REQUEST_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"Anfrage zu groß (max {MAX_REQUEST_BYTES // (1024 * 1024)} MB)."},
                )
        except ValueError:
            return JSONResponse(status_code=400, content={"detail": "Ungültiger Content-Length-Header."})
    return await call_next(request)


# ── Security headers ───────────────────────────────────────────────────────────
# Defence-in-depth response headers on every response. Toggle off via
# SECURITY_HEADERS_ENABLED=0; override the CSP wholesale via CONTENT_SECURITY_POLICY.
_SECURITY_HEADERS_ENABLED = os.getenv("SECURITY_HEADERS_ENABLED", "1").lower() not in ("0", "false", "no")


def _build_csp() -> str:
    """Content-Security-Policy tuned to what the frontend actually loads:
    self-hosted fonts, the jsDelivr-hosted Supabase JS SDK and KaTeX
    (script + stylesheet + math webfonts), and the project's own Supabase
    origin for client-side auth/realtime. 'unsafe-inline' is required
    because the UI relies on many inline <script>/<style> blocks and onclick
    handlers; the policy still restricts *external* origins."""
    supa = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    supa_connect = ""
    if supa.startswith("https://"):
        host = supa[len("https://"):]
        supa_connect = f" https://{host} wss://{host}"
    return (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "img-src 'self' data: blob:; "
        f"connect-src 'self'{supa_connect}; "
        "frame-src 'self' blob:; "
        "frame-ancestors 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )


_CSP = os.getenv("CONTENT_SECURITY_POLICY", "").strip() or _build_csp()


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    response = await call_next(request)
    if not _SECURITY_HEADERS_ENABLED:
        return response
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "SAMEORIGIN")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(self), browsing-topics=()")
    h.setdefault("Content-Security-Policy", _CSP)
    # Long-lived browser cache for immutable assets (fonts, logos) — without
    # this every page load re-downloads ~600 KB of woff2 files.
    path = request.url.path
    if path.endswith(".woff2") or path in ("/logo.png", "/logo_white.png"):
        h.setdefault("Cache-Control", "public, max-age=31536000, immutable")
    elif path == "/fonts/fonts.css":
        # fonts.css may change (font additions/swaps) — cache, but revalidate daily.
        h.setdefault("Cache-Control", "public, max-age=86400")
    # HSTS only over HTTPS (Railway terminates TLS and forwards X-Forwarded-Proto).
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    if proto == "https":
        h.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
    return response


async def _read_capped(upload: UploadFile, max_bytes: int = MAX_UPLOAD_FILE_BYTES) -> bytes:
    """Read an UploadFile in chunks, aborting with HTTP 413 once max_bytes is
    exceeded. Bounds memory/disk use even when Content-Length is absent or lies,
    instead of `await upload.read()` pulling an unbounded file in one shot."""
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)  # 1 MB
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Datei zu groß (max {max_bytes // (1024 * 1024)} MB).",
            )
        chunks.append(chunk)
    return b"".join(chunks)


# Init RAG on startup (pgvector replaces ChromaDB)
embedder = Embedder(model_name=config["embedding_model"])
vector_store = PgVectorStore()
rag = create_query_service(vector_store=vector_store, embedder=embedder, top_k=config["top_k"])


class Question(BaseModel):
    question: str = Field(..., max_length=MAX_TEXT_CHARS)
    module_name: Optional[str] = Field(default=None, max_length=200)
    chat_history: List[dict] = []

class ChatRequest(BaseModel):
    message: str = Field(..., max_length=MAX_TEXT_CHARS)
    module_name: Optional[str] = Field(default=None, max_length=200)
    chat_history: List[dict] = []
    pending_proposal: Optional[dict] = None

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
    daily_hours: float = 2.0

class DailyTaskPatchRequest(BaseModel):
    topic_id: str
    task_index: int
    done: bool

class FileTypeRequest(BaseModel):
    path: str
    file_type: str  # "klausur" | "übungsblatt" | "vorlesung" | "sonstiges"

class FileRenameRequest(BaseModel):
    path: str
    new_name: str

def sanitize_module_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", name)
    if not name:
        raise ValueError("Ungueltiger Modulname.")
    return name


def module_dir(module_name: str) -> Path:
    # Namespace local files per authenticated user so the local-filesystem tier
    # (used as a processing scratch area and as a read fallback) cannot serve one
    # tenant's uploads to another that happens to pick the same module name.
    return RAW_DIR / _supa_uid() / sanitize_module_name(module_name)


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
    try:
        uid = _supa_uid()
        profile = mp.load(module_name) or {}
        if not profile.get("id"):
            return _list_module_files_local(module_name)
        rows = (
            _supa_client()
            .table("files")
            .select("file_name, relative_path, file_type, file_size, is_exam, file_category, storage_path")
            .eq("module_id", profile["id"])
            .order("file_name")
            .execute()
        ).data or []
        if rows:
            return [
                {
                    "name":          r["file_name"],
                    "relative_path": r.get("relative_path") or r["file_name"],
                    "file_type":     r.get("file_type", ""),
                    "size":          r.get("file_size", 0),
                    "is_exam":       r.get("is_exam", False),
                    "file_category": r.get("file_category"),
                    "storage_path":  r.get("storage_path", ""),
                }
                for r in rows
            ]
        # files table empty for this module — fall back to local disk
        return _list_module_files_local(module_name)
    except Exception:
        return _list_module_files_local(module_name)


def _list_module_files_local(module_name: str) -> List[dict]:
    base = module_dir(module_name)
    if not base.exists():
        return []
    profile = mp.load(module_name) or {}
    file_types = profile.get("file_types") or {}
    files = []
    for file_path in sorted(base.rglob("*")):
        if file_path.is_file():
            rel = str(file_path.relative_to(base)).replace("\\", "/")
            files.append({
                "name":          file_path.name,
                "relative_path": rel,
                "file_type":     file_path.suffix.lower().lstrip("."),
                "size":          file_path.stat().st_size,
                "is_exam":       _is_exam_file(rel, profile),
                "file_category": file_types.get(rel),
            })
    return files


def _safe_rel_path(rel: str) -> Path:
    """Sanitize a relative path preventing directory traversal attacks."""
    parts = Path(rel.replace("\\", "/")).parts
    safe = [p for p in parts if p not in ("..", ".", "") and "/" not in p and "\\" not in p]
    return Path(*safe) if safe else Path(Path(rel).name)

def _find_file(module_name: str, filename: str) -> Path:
    """Sucht Datei im Modul-Verzeichnis (pfad-traversal-sicher).

    `filename` ist client-kontrolliert; es wird über `_safe_rel_path` entschärft
    und das aufgelöste Ziel muss innerhalb des Modul-Verzeichnisses liegen, damit
    `../`-/Absolut-Pfade nicht beliebige Dateien des Servers lesen können.
    """
    base = module_dir(module_name).resolve()
    rel = _safe_rel_path(filename)
    target = (base / rel).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Datei nicht gefunden: {rel.name}")
    if target.exists() and target.is_file():
        return target
    # Fallback: rekursiv per Basename suchen (innerhalb von base, kein Traversal)
    matches = list(base.rglob(rel.name)) if rel.name else []
    if matches:
        return matches[0]
    raise HTTPException(status_code=404, detail=f"Datei nicht gefunden: {rel.name}")


def _slug(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[äöüß]", lambda m: {"ä":"ae","ö":"oe","ü":"ue","ß":"ss"}[m.group()], s)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _index_generated_content(text: str, source_path: str, module_name: str) -> int:
    """Chunk, embed und indiziere generierten Inhalt in pgvector. Gibt Chunk-Anzahl zurück."""
    from app.chunking.chunker import chunk_document

    doc_id = hashlib.md5(source_path.encode()).hexdigest()
    chunks = chunk_document(
        text=text,
        document_id=doc_id,
        file_type="md",
        metadata={"source": source_path, "module_name": module_name},
    )
    if not chunks:
        return 0

    existing_ids = set(vector_store.get()["ids"])
    new_chunks = [c for c in chunks if c.chunk_id not in existing_ids]
    if not new_chunks:
        return 0

    embeddings = embedder.embed_batch([c.chunk_text for c in new_chunks])
    vector_store.add(
        ids=[c.chunk_id for c in new_chunks],
        embeddings=embeddings,
        documents=[c.chunk_text for c in new_chunks],
        metadatas=[
            {
                "document_id": doc_id,
                "source": source_path,
                "module_name": module_name,
                "chunk_index": str(c.chunk_index),
            }
            for c in new_chunks
        ],
    )
    return len(new_chunks)


# ── Exam-style cache (stored in Supabase Storage) ─────────────────────────────

def _exam_cache_hash(module_name: str, exam_files: list[dict]) -> str:
    """MD5 over sorted (name, size) of the module's exam files."""
    parts = []
    for f in sorted(exam_files, key=lambda x: x["name"]):
        parts.append(f"{f['name']}:0:{f.get('size', 0)}")
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def _load_exam_cache(module_name: str) -> dict | None:
    from app.storage import storage_backend as sb
    raw = sb.read_text(f"{_slug(module_name)}/exam-style-cache.json")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _save_exam_cache(module_name: str, hash_val: str, style: str, profile_md: str) -> None:
    from app.storage import storage_backend as sb
    sb.write_text(
        f"{_slug(module_name)}/exam-style-cache.json",
        json.dumps({"hash": hash_val, "style": style, "exam_profile_md": profile_md},
                   ensure_ascii=False, indent=2),
    )


def _exam_analyze_cached(module_name: str, all_files: list[dict]) -> tuple[str, str]:
    """Run exam analysis using cache when unchanged. Returns (exam_profile_md, exam_style)."""
    exam_files = [f for f in all_files if f.get("is_exam")]
    if not exam_files:
        return "", ""

    current_hash = _exam_cache_hash(module_name, exam_files)
    cache = _load_exam_cache(module_name)
    # Only trust a cache hit that actually carries analysis — an earlier run whose
    # exam download failed may have poisoned the cache with an empty style.
    if cache and cache.get("hash") == current_hash and (cache.get("style") or cache.get("exam_profile_md")):
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

    # Don't cache an empty result — that would pin the failure for this file set
    # until the files change. Leave it uncached so the next run retries.
    if exam_style or exam_profile_md:
        _save_exam_cache(module_name, current_hash, exam_style, exam_profile_md)
    return exam_profile_md, exam_style


def _inject_supabase(html: str) -> str:
    inject = (
        f'<script>window.__SUPABASE_URL__="{os.getenv("SUPABASE_URL","")}";</script>\n'
        f'<script>window.__SUPABASE_ANON_KEY__="{os.getenv("SUPABASE_ANON_KEY","")}";</script>\n'
        '<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>\n'
    )
    return html.replace("<!-- SUPABASE_INJECT -->", inject, 1)


# Injected HTML cached by (file, mtime): re-reading + string-replacing the
# 512 KB app shell on every page hit is pure waste, but keying on mtime keeps
# dev edits to the .html files visible without a restart.
_html_cache: dict = {}


def _serve_static_html(name: str) -> HTMLResponse:
    html_path = Path(__file__).parent / "static" / name
    mtime = html_path.stat().st_mtime
    cached = _html_cache.get(name)
    if cached and cached[0] == mtime:
        return HTMLResponse(cached[1])
    html = _inject_supabase(html_path.read_text(encoding="utf-8"))
    _html_cache[name] = (mtime, html)
    return HTMLResponse(html)


@app.get("/", response_class=HTMLResponse)
def serve_landing():
    return _serve_static_html("landing.html")


@app.get("/app", response_class=HTMLResponse)
def serve_ui():
    return _serve_static_html("index.html")


@app.get("/upgrade", response_class=HTMLResponse)
def serve_upgrade():
    return _serve_static_html("upgrade.html")


@app.get("/impressum", response_class=HTMLResponse)
def serve_impressum():
    """Impressum (§ 5 DDG) — public, no auth required."""
    return _serve_static_html("impressum.html")


@app.get("/datenschutz", response_class=HTMLResponse)
def serve_datenschutz():
    """Datenschutzerklärung (DSGVO Art. 13/14) — public, no auth required."""
    return _serve_static_html("datenschutz.html")


@app.get("/agb", response_class=HTMLResponse)
def serve_agb():
    """AGB / Nutzungsbedingungen — public, no auth required."""
    return _serve_static_html("agb.html")


@app.get("/hilfe", response_class=HTMLResponse)
def serve_hilfe():
    """Hilfe & Support — public, no auth required."""
    return _serve_static_html("hilfe.html")


@app.get("/verschenken", response_class=HTMLResponse)
def serve_verschenken():
    """Veexa verschenken — public, no auth required."""
    return _serve_static_html("verschenken.html")


@app.get("/mehr-erfahren", response_class=HTMLResponse)
def serve_mehr_erfahren():
    """Funktionsüberblick — public, no auth required."""
    return _serve_static_html("mehr-erfahren.html")


@app.get("/logo.png")
def serve_logo():
    return FileResponse(Path(__file__).parent / "static" / "logo.png", media_type="image/png")


@app.get("/logo_white.png")
def serve_logo_white():
    return FileResponse(Path(__file__).parent / "static" / "logo_white.png", media_type="image/png")


@app.post("/ask")
@limiter.limit(RL_CHAT)
async def ask(request: Request, body: Question):
    answer_parts: List[str] = []
    sources: list = []
    path = "simple"
    async for raw in ask_advanced(body.question, body.module_name, rag, body.chat_history):
        data = json.loads(raw)
        if data["type"] == "token":
            answer_parts.append(data["content"])
        elif data["type"] == "done":
            sources = data.get("sources", [])
            path = data.get("path", "simple")
    return {"answer": "".join(answer_parts), "sources": sources, "path": path}


@app.post("/ask/stream")
@limiter.limit(RL_CHAT)
async def ask_stream(request: Request, body: Question):
    async def event_stream():
        try:
            async for raw in ask_advanced(body.question, body.module_name, rag, body.chat_history):
                yield f"data: {raw}\n\n"
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("ask/stream failed")
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ── Chat agent (tool-use orchestrator) ─────────────────────────────────────────

def _safe_module(name: Optional[str]) -> str:
    """Sanitize a module name without raising on empty input."""
    return chat_tools.sanitize_module_name(name or "")


def _module_status(module_name: str) -> dict:
    """Light module status for the chat system prompt (roadmap?, #exams, files list)."""
    if not module_name:
        return {}
    try:
        has_roadmap = bool(rm.load_roadmap_md(module_name))
    except Exception:
        has_roadmap = False
    try:
        n_exams = len(eg.list_exams(module_name))
    except Exception:
        n_exams = 0
    file_names: list[str] = []
    try:
        file_names = [f.get("name", "") for f in list_module_files(module_name) if f.get("name")]
    except Exception:
        pass
    return {"roadmap": has_roadmap, "klausuren": n_exams, "dateien": file_names}


def _all_module_names() -> List[str]:
    try:
        return get_modules().get("modules", [])
    except Exception:
        return []


def _build_chat_system_prompt(active: str, all_modules: List[str], status: dict, today: str) -> str:
    modul_list = ", ".join(all_modules) if all_modules else "(keine)"
    if status:
        rm_txt = "ja" if status.get("roadmap") else "nein"
        file_names: list = status.get("dateien") or []
        n_files = len(file_names)
        files_txt = ", ".join(file_names) if file_names else "(keine)"
        status_txt = (f"Roadmap vorhanden: {rm_txt}; Klausuren: {status.get('klausuren', 0)}; "
                      f"Dateien ({n_files}): {files_txt}")
    else:
        status_txt = "(kein aktives Modul)"
    return (
        "Du bist die Steuerzentrale eines Lern-Assistenten. Du hilfst Studierenden, "
        "Lerninhalte zu erstellen und ihren Arbeitsbereich anzusehen.\n\n"
        f"Heutiges Datum: {today}\n"
        f"Aktives Modul: {active or '(keines)'}\n"
        f"Alle Module des Nutzers: {modul_list}\n"
        f"Status des aktiven Moduls: {status_txt}\n\n"
        "REGELN:\n"
        "- Zum ERSTELLEN von Inhalten rufst du IMMER das passende Tool auf (das erzeugt "
        "  einen Vorschlag). Behaupte NIEMALS, etwas sei erstellt, bevor der Nutzer bestätigt hat.\n"
        "- Für einen Tagesplan ist eine vorhandene Roadmap nötig. Fehlt sie (siehe Status), "
        "  schlage zuerst eine Roadmap vor (erstelle_roadmap).\n"
        "- Datei-Auflösung: Wenn der Nutzer eine Datei mit einer Beschreibung nennt (z.B. "
        "  'Blatt 9', 'zweite Übung', 'Aufgabe 3'), wähle den besten passenden Dateinamen aus "
        "  der obigen Dateiliste und verwende DIESEN exakten Namen als Parameter. "
        "  Frage NIEMALS nach dem genauen Dateinamen — löse ihn selbst auf.\n"
        "- Für eine Zusammenfassung ohne genannte Datei: frage, welche Datei zusammengefasst "
        "  werden soll (zeige dabei die Dateiliste aus dem Status).\n"
        "- Wenn du ein Lese-Tool benutzt, leite das Ergebnis mit EINEM kurzen Satz ein. "
        "  Die Liste wird separat angezeigt — zähle die Einträge nicht selbst auf.\n"
        "- `zeige_dateien` nur aufrufen, wenn der Nutzer EXPLIZIT alle Dateien des Moduls sehen "
        "  will ('zeig mir alle Dateien', 'welche Dokumente gibt es'). Fragen wie 'wo finde ich "
        "  etwas zu Thema X' oder 'in welcher Datei steht Y' sind Wissensfragen → KEIN Tool.\n"
        "- Für normale Wissensfragen rufst du KEIN Tool auf; diese werden separat beantwortet.\n"
        "- Antworte immer auf Deutsch.\n"
        "- Antworte standardmäßig kurz und präzise (2-4 Sätze). Nur wenn der Nutzer "
        "explizit nach Details, einer ausführlichen Erklärung oder einem Überblick fragt, "
        "antworte ausführlich.\n"
        "- Strukturiere längere Antworten mit Markdown: ## Überschriften, "
        "**Fettschrift** für Schlüsselbegriffe, Listen für Aufzählungen. "
        "Mathematische Formeln inline mit $...$ oder abgesetzt mit $$...$$."
    )


def _chat_read_executor(tool_name: str, raw: dict, active_module: str) -> dict:
    """Execute a read tool and return {kind, items, result_text}. Items are
    structured (never raw document text) to keep the prompt-injection surface small."""
    module = _safe_module((raw or {}).get("modul") or active_module)

    if tool_name == "zeige_klausuren":
        exams = eg.list_exams(module)
        items = [{"n": e.get("n"), "generated": e.get("generated", ""),
                  "num_tasks": e.get("num_tasks", 0), "total_points": e.get("total_points", 0)}
                 for e in exams]
        txt = (f"{len(items)} Probeklausur(en): " + ", ".join(f"#{i['n']}" for i in items)) if items \
            else "Keine Probeklausuren vorhanden."
        return {"kind": "klausuren", "items": items, "result_text": txt}

    if tool_name == "zeige_zusammenfassungen":
        items = get_lecture_summaries(module).get("summaries", [])
        txt = (f"{len(items)} Zusammenfassung(en): " + ", ".join(i.get("titel", "") for i in items)) if items \
            else "Keine Zusammenfassungen vorhanden."
        return {"kind": "zusammenfassungen", "items": items, "result_text": txt}

    if tool_name == "zeige_dateien":
        files = list_module_files(module)
        items = [{"name": f.get("name"), "relative_path": f.get("relative_path"),
                  "file_type": f.get("file_type", ""), "is_exam": f.get("is_exam", False)}
                 for f in files]
        txt = (f"{len(items)} Datei(en) im Modul.") if items else "Keine Dateien vorhanden."
        return {"kind": "dateien", "items": items, "result_text": txt}

    if tool_name == "zeige_lernfortschritt":
        stats = dt.get_stats(module)
        txt = f"{stats.get('total_completed', 0)} erledigte Aufgaben."
        return {"kind": "lernfortschritt", "items": [stats], "result_text": txt}

    return {"kind": "", "items": [], "result_text": ""}


async def _chat_rag_streamer(message: str, module_name: str, chat_history: List[dict]):
    """Adapt the existing RAG pipeline (yields JSON strings) into event dicts."""
    async for raw in ask_advanced(message, module_name or None, rag, chat_history):
        try:
            yield json.loads(raw)
        except (TypeError, ValueError):
            continue


@app.post("/chat/stream")
@limiter.limit(RL_CHAT)
async def chat_stream(request: Request, body: ChatRequest):
    """Chat control center: routes a message to a proposal / read-nav action /
    RAG fallback via a Claude tool-use loop. Mutating actions are never executed
    here — the orchestrator only emits proposals; execution happens on an explicit
    click via the existing rate-limited generator endpoints."""
    active = _safe_module(body.module_name)
    all_modules = _all_module_names()
    status = _module_status(active)
    system_prompt = _build_chat_system_prompt(active, all_modules, status, str(_date.today()))

    async def event_stream():
        async for ev in chat_orchestrator.run_chat(
            message=body.message,
            module_name=active,
            chat_history=body.chat_history,
            pending_proposal=body.pending_proposal,
            client=anthropic_client,
            model=CHAT_MODEL,
            system_prompt=system_prompt,
            read_executor=_chat_read_executor,
            rag_streamer=_chat_rag_streamer,
        ):
            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/modules")
def get_modules():
    uid = _supa_uid()
    rows = (
        _supa_client()
        .table("modules")
        .select("name")
        .eq("user_id", uid)
        .order("name")
        .execute()
    ).data or []
    return {"modules": [r["name"] for r in rows]}


def _load_settings() -> dict:
    try:
        uid = _supa_uid()
        rows = (
            _supa_client()
            .table("settings")
            .select("preferences, favorite_module")
            .eq("user_id", uid)
            .execute()
        ).data or []
        if rows:
            row = rows[0]
            prefs = row.get("preferences") or {}
            if row.get("favorite_module"):
                prefs["favorite_module_id"] = row["favorite_module"]
            return prefs
    except Exception:
        pass
    return {}


def _save_settings(data: dict) -> None:
    try:
        uid = _supa_uid()
        _supa_client().table("settings").upsert(
            {"user_id": uid, "preferences": data},
            on_conflict="user_id",
        ).execute()
    except Exception:
        pass


@app.get("/settings/favorite-module")
def get_favorite_module():
    try:
        uid = _supa_uid()
        rows = (
            _supa_client()
            .table("settings")
            .select("preferences")
            .eq("user_id", uid)
            .execute()
        ).data or []
        if rows:
            prefs = rows[0].get("preferences") or {}
            return {"module": prefs.get("favorite_module")}
    except Exception:
        pass
    return {"module": None}


class FavoriteModuleRequest(BaseModel):
    module: str

@app.post("/settings/favorite-module")
def set_favorite_module(body: FavoriteModuleRequest):
    clean = sanitize_module_name(body.module)
    try:
        uid = _supa_uid()
        rows = (
            _supa_client()
            .table("settings")
            .select("preferences")
            .eq("user_id", uid)
            .execute()
        ).data or []
        prefs = (rows[0].get("preferences") or {}) if rows else {}
        prefs["favorite_module"] = clean
        _supa_client().table("settings").upsert(
            {"user_id": uid, "preferences": prefs},
            on_conflict="user_id",
        ).execute()
    except Exception:
        pass
    return {"ok": True}


# ── Sidebar folder layout (persisted in settings.preferences jsonb) ────────────
# The browser keeps a localStorage cache, but the server is the source of truth so
# folders survive a cache clear and follow the user across browsers/devices.
def _read_preferences() -> dict:
    try:
        uid = _supa_uid()
        rows = (
            _supa_client()
            .table("settings")
            .select("preferences")
            .eq("user_id", uid)
            .execute()
        ).data or []
        if rows:
            return rows[0].get("preferences") or {}
    except Exception:
        pass
    return {}


def _write_preferences(prefs: dict) -> None:
    try:
        uid = _supa_uid()
        _supa_client().table("settings").upsert(
            {"user_id": uid, "preferences": prefs},
            on_conflict="user_id",
        ).execute()
    except Exception:
        pass


def _clear_folder_config(module_name: str) -> None:
    """Remove a module's saved sidebar folder layout from settings.preferences."""
    prefs = _read_preferences()
    configs = prefs.get("folder_configs") or {}
    if module_name in configs:
        configs.pop(module_name, None)
        prefs["folder_configs"] = configs
        _write_preferences(prefs)


@app.get("/modules/{module_name}/folder-config")
def get_module_folder_config(module_name: str):
    """Return the saved sidebar folder layout for a module (empty if none)."""
    clean = sanitize_module_name(module_name)
    prefs = _read_preferences()
    configs = prefs.get("folder_configs") or {}
    return {"config": configs.get(clean) or {}}


class FolderConfigRequest(BaseModel):
    config: dict = {}

@app.put("/modules/{module_name}/folder-config")
def set_module_folder_config(module_name: str, body: FolderConfigRequest):
    """Persist the sidebar folder layout for a module in settings.preferences."""
    clean = sanitize_module_name(module_name)
    prefs = _read_preferences()
    configs = dict(prefs.get("folder_configs") or {})
    if body.config:
        configs[clean] = body.config
    else:
        configs.pop(clean, None)
    prefs["folder_configs"] = configs
    _write_preferences(prefs)
    return {"ok": True}


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
async def get_module_raw(module_name: str, path: str):
    """Stream file content from Supabase Storage or local filesystem.

    Always served through our own backend (no redirect to external domains) so
    the browser treats it as same-origin — required for inline PDF rendering in
    iframes and to prevent unwanted download prompts.
    """
    import httpx

    uid = _supa_uid()
    slug = _slug(sanitize_module_name(module_name))
    # `path` is client-controlled — sanitize it the same way uploads do so a
    # `../`/absolute path can never be concatenated into the storage key. RLS
    # already pins the first key segment to {uid}; this is defence-in-depth and
    # keeps the storage branch consistent with the local-fs guard below.
    rel = str(_safe_rel_path(path)).replace("\\", "/")
    storage_path = f"{uid}/{slug}/{rel}"
    suffix = Path(rel).suffix.lower()
    media_type = _MEDIA_TYPES.get(suffix, "application/octet-stream")
    filename = Path(rel).name
    # PDFs must be inline so the browser renders them; everything else is a download.
    disposition = "inline" if suffix == ".pdf" else f'attachment; filename="{filename}"'

    # ── 1. Try Supabase Storage (stream through our server, never redirect) ──────
    try:
        result = _supa_client().storage.from_("raw-files").create_signed_url(storage_path, 300)
        signed_url = (result or {}).get("signedURL") or (result or {}).get("signedUrl")
        if signed_url:
            async def _stream_supabase():
                async with httpx.AsyncClient(timeout=60) as client:
                    async with client.stream("GET", signed_url) as resp:
                        async for chunk in resp.aiter_bytes(65536):
                            yield chunk

            return StreamingResponse(
                _stream_supabase(),
                media_type=media_type,
                headers={"Content-Disposition": disposition, "Cache-Control": "private, max-age=300"},
            )
    except Exception:
        pass

    # ── 2. Fallback: local filesystem ────────────────────────────────────────────
    try:
        target = _resolve_module_file(module_name, path)
        return FileResponse(
            target,
            media_type=media_type,
            content_disposition_type="inline",
            filename=filename,
        )
    except HTTPException:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden.")


@app.delete("/modules/{module_name}/file")
def delete_module_file(module_name: str, path: str):
    """Delete a single file from a module (Supabase Storage + DB + pgvector)."""
    uid = _supa_uid()
    supa = _supa_client()
    slug = _slug(sanitize_module_name(module_name))
    profile = mp.load(module_name) or {}

    # Find the file record in DB
    file_record = None
    if profile.get("id"):
        try:
            rows = (
                supa.table("files")
                .select("id, file_name, storage_path, relative_path")
                .eq("module_id", profile["id"])
                .eq("relative_path", path.replace("\\", "/"))
                .execute()
            ).data or []
            file_record = rows[0] if rows else None
        except Exception:
            pass

    deleted_name = Path(path).name
    embeddings_removed = 0

    # 1) Delete embeddings in pgvector by source metadata
    try:
        scoped = vector_store.get(where={"module_name": sanitize_module_name(module_name)})
        ids_to_del = [
            cid for cid, meta in zip(scoped.get("ids", []), scoped.get("metadatas", []))
            if (meta or {}).get("source", "").replace("\\", "/").endswith(path.replace("\\", "/"))
        ]
        if ids_to_del:
            vector_store.delete(ids=ids_to_del)
            embeddings_removed = len(ids_to_del)
    except Exception:
        pass

    # 2) Delete file record from DB
    if file_record:
        try:
            supa.table("files").delete().eq("id", file_record["id"]).execute()
        except Exception:
            pass

    # 3) Delete from Supabase Storage
    storage_path = (file_record or {}).get("storage_path") or f"{uid}/{slug}/{path.replace(chr(92), '/')}"
    try:
        supa.storage.from_("raw-files").remove([storage_path])
    except Exception:
        pass

    # 4) Local filesystem cleanup (best-effort)
    try:
        local = _resolve_module_file(module_name, path)
        local.unlink(missing_ok=True)
    except Exception:
        pass

    return {
        "success": True,
        "deleted": deleted_name,
        "chunks_files_removed": 0,
        "embeddings_removed": embeddings_removed,
    }


@app.post("/modules/{module_name}/file/exam-flag")
def toggle_exam_flag(module_name: str, path: str):
    """Toggle the manual exam-flag for a single file."""
    rel = path.replace("\\", "/")
    profile = mp.load(module_name)
    if not profile:
        profile = mp.create_from_onboarding({
            "name": module_name, "schwerpunkte": [], "stil": "mixed", "pruefungsrelevant": [],
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

    # Also update is_exam in files table
    if profile.get("id"):
        try:
            _supa_client().table("files").update({"is_exam": desired}).eq(
                "module_id", profile["id"]
            ).eq("relative_path", rel).execute()
        except Exception:
            pass

    return {"success": True, "is_exam": desired}


@app.patch("/modules/{module_name}/file/type")
def set_file_type(module_name: str, body: FileTypeRequest):
    """Persist the semantic category of a file (klausur/übungsblatt/vorlesung/sonstiges)."""
    rel = body.path.replace("\\", "/")

    profile = mp.load(module_name)
    if not profile:
        profile = mp.create_from_onboarding({
            "name": module_name, "schwerpunkte": [], "stil": "mixed", "pruefungsrelevant": [],
        })

    file_types = dict(profile.get("file_types") or {})
    if body.file_type == "sonstiges":
        file_types.pop(rel, None)
    else:
        file_types[rel] = body.file_type
    profile["file_types"] = file_types

    manual_exam = list(profile.get("manual_exam_files") or [])
    manual_not_exam = list(profile.get("manual_not_exam_files") or [])
    auto_detected = bool(EXAM_FILE_PATTERN.search(rel))

    if body.file_type == "klausur":
        manual_not_exam = [f for f in manual_not_exam if f != rel]
        if not auto_detected and rel not in manual_exam:
            manual_exam.append(rel)
    else:
        manual_exam = [f for f in manual_exam if f != rel]
        if auto_detected and rel not in manual_not_exam:
            manual_not_exam.append(rel)

    profile["manual_exam_files"] = manual_exam
    profile["manual_not_exam_files"] = manual_not_exam
    mp.save(profile)

    # Update file_category and is_exam in files table
    if profile.get("id"):
        try:
            _supa_client().table("files").update({
                "file_category": body.file_type if body.file_type != "sonstiges" else None,
                "is_exam": body.file_type == "klausur",
            }).eq("module_id", profile["id"]).eq("relative_path", rel).execute()
        except Exception:
            pass

    return {"success": True, "file_type": body.file_type}


@app.post("/modules/{module_name}/file/rename")
def rename_module_file(module_name: str, body: FileRenameRequest):
    """Rename a file's display name. For Supabase-backed modules only
    ``files.file_name`` changes — storage_path/relative_path (and with them the
    search index) stay untouched. Legacy disk modules rename on disk."""
    new_name = body.new_name.strip()
    if not new_name or "/" in new_name or "\\" in new_name or ".." in new_name:
        raise HTTPException(status_code=400, detail="Ungültiger Dateiname.")

    rel = body.path.replace("\\", "/")
    profile = mp.load(module_name) or {}
    if profile.get("id"):
        try:
            supa = _supa_client()
            res = (
                supa.table("files")
                .update({"file_name": new_name})
                .eq("module_id", profile["id"])
                .eq("relative_path", rel)
                .execute()
            )
            if not res.data:
                # Legacy rows without relative_path are identified by file_name.
                res = (
                    supa.table("files")
                    .update({"file_name": new_name})
                    .eq("module_id", profile["id"])
                    .eq("file_name", rel)
                    .execute()
                )
            if res.data:
                return {"success": True, "old_path": rel, "new_path": rel, "new_name": new_name}
            raise HTTPException(status_code=404, detail="Datei nicht gefunden.")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=500, detail="Umbenennen fehlgeschlagen.")

    # ── Legacy local-disk fallback ──
    target = _resolve_module_file(module_name, body.path)
    new_path = target.parent / new_name
    if new_path.exists():
        raise HTTPException(status_code=409, detail="Eine Datei mit diesem Namen existiert bereits.")

    base = module_dir(module_name).resolve()
    rel_old = str(target.relative_to(base)).replace("\\", "/")
    target.rename(new_path)
    rel_new = str(new_path.relative_to(base)).replace("\\", "/")

    profile = mp.load(module_name) or {}

    def swap(lst: list) -> list:
        return [rel_new if x == rel_old else x for x in lst]

    def swap_dict(d: dict) -> dict:
        return {(rel_new if k == rel_old else k): v for k, v in d.items()}

    if "manual_exam_files" in profile:
        profile["manual_exam_files"] = swap(profile["manual_exam_files"])
    if "manual_not_exam_files" in profile:
        profile["manual_not_exam_files"] = swap(profile["manual_not_exam_files"])
    if "file_types" in profile:
        profile["file_types"] = swap_dict(profile["file_types"])
    mp.save(profile)

    return {"success": True, "old_path": rel_old, "new_path": rel_new, "new_name": new_name}


# ─────────────────────── Roadmap endpoints ────────────────────────────────────

class SolveSheetRequest(BaseModel):
    sheet_text: str = Field(..., max_length=MAX_TEXT_CHARS)
    module_id: str = Field(..., max_length=200)
    sheet_name: str = Field(default="", max_length=500)
    sheet_path: str = Field(default="", max_length=1000)


class SolutionRenameRequest(BaseModel):
    path: str
    new_name: str


class RoadmapGenerateRequest(BaseModel):
    module_name: str = Field(..., max_length=200)
    exam_date: Optional[str] = Field(default=None, max_length=50)
    focus: Optional[str] = Field(default=None, max_length=2000)


class RoadmapStatusRequest(BaseModel):
    status: str  # "todo" | "doing" | "done"


class ExtendPoolsRequest(BaseModel):
    files: List[str] = Field(default_factory=list)


# In-memory pending-roadmap cache: {module_name: pending_md}
_PENDING_ROADMAPS: dict = {}


def _is_generated_source(src: str) -> bool:
    """True for our own generated artifacts (practice exams, solved sheets) — these
    must never feed back into generation context, or new exams just clone old ones."""
    s = (src or "").replace("\\", "/").lower()
    return ("/exams/exam_" in s or s.startswith("exams/exam_")
            or "/solved_sheets/" in s or s.startswith("solved_sheets/"))


def _exam_texts_from_index(file_names: set) -> dict:
    """Reconstruct each exam file's text from its indexed pgvector chunks.

    Used as a last resort when the raw upload is no longer in Storage (the chunk
    text persists in pgvector even after the original PDF is gone). Chunk order
    isn't guaranteed, but that's fine for style analysis (format / point
    distribution / phrasing), which doesn't depend on exact sequence.
    """
    if not file_names:
        return {}
    try:
        store = vector_store.get()
    except Exception:
        return {}
    docs = store.get("documents") or []
    metas = store.get("metadatas") or []
    buckets: dict = {}
    for doc, meta in zip(docs, metas):
        if not doc or not meta:
            continue
        name = Path((meta.get("source") or "").replace("\\", "/")).name
        if name in file_names:
            buckets.setdefault(name, []).append(doc)
    return {k: "\n".join(v) for k, v in buckets.items()}


def _collect_exam_text(module_name: str) -> List[str]:
    """Plain text of every file marked as exam, for style analysis.

    Resolution order per file: canonical Storage path (``{uid}/{slug}/{rel}``) →
    DB ``storage_path`` (older uploads can carry a stale path) → local-disk scratch
    copy → reconstruction from the pgvector index (covers files whose raw upload is
    no longer in Storage but whose extracted text was indexed).
    """
    import tempfile as _tf
    uid = _supa_uid()
    slug = _slug(sanitize_module_name(module_name))
    texts: List[str] = []
    missing: set = set()
    for f in list_module_files(module_name):
        if not f.get("is_exam"):
            continue
        rel = (f.get("relative_path") or f.get("name") or "").replace("\\", "/")
        name = f.get("name") or rel

        candidates: List[str] = []
        if rel:
            candidates.append(f"{uid}/{slug}/{rel}")
        sp = f.get("storage_path")
        if sp and sp not in candidates:
            candidates.append(sp)

        raw = None
        for path in candidates:
            try:
                raw = _supa_client().storage.from_("raw-files").download(path)
                if raw:
                    break
            except Exception:
                raw = None

        recovered = False
        try:
            if raw:
                with _tf.NamedTemporaryFile(suffix=Path(name).suffix, delete=False) as tmp:
                    tmp.write(raw)
                    tmp_path = Path(tmp.name)
                try:
                    parsed = parse_document(tmp_path)
                finally:
                    tmp_path.unlink(missing_ok=True)
            else:
                # Local-disk fallback (processing scratch area / same-session uploads).
                parsed = parse_document(_resolve_module_file(module_name, rel or name))
            if parsed.success and parsed.extracted_text:
                texts.append(parsed.extracted_text)
                recovered = True
        except Exception:
            pass
        if not recovered:
            missing.add(name)

    if missing:
        backfill = _exam_texts_from_index(missing)
        for name in missing:
            if backfill.get(name):
                texts.append(backfill[name])
            else:
                print(f"[collect_exam_text] no source for {name} (not in storage or index)")
    return texts


def _course_context_excluding_generated(module_name: str, query: str, top_k: int = 20) -> str:
    """Build a generation context from retrieved chunks, dropping our own generated
    artifacts (practice exams / solved sheets) so new exams are built from the real
    course material instead of cloning a previously generated exam."""
    hits = rag.retrieve(query, top_k=top_k * 2, module_name=module_name)
    real = [h for h in hits if not _is_generated_source(h.get("source", ""))][:top_k]
    if not real:
        return ""
    return "\n\n".join(
        f"[{Path(h['source']).name}]\n{(h.get('text') or '').strip()}"
        for h in real
    )


@app.post("/roadmap/generate")
@limiter.limit(RL_HEAVY)
@_heavy_daily
async def roadmap_generate(request: Request, body: RoadmapGenerateRequest):
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

    new_md = rm.render_md(body.module_name, data)
    _PENDING_ROADMAPS[body.module_name] = new_md
    return {
        "success": True,
        "is_first_generation": True,
        "diff": None,
        "preview_md": new_md,
    }


@app.post("/roadmap/generate/stream")
@limiter.limit(RL_HEAVY)
@_heavy_daily
async def roadmap_generate_stream(request: Request, body: RoadmapGenerateRequest):
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


def _pool_rag_fn(clean_module: str) -> Callable[[str, str, int], str]:
    """Build the RAG retrieval closure used during pool generation.

    Identical shape to the one in ``daily_generate`` — returns formatted course
    chunks for a query, or "" when nothing is retrieved.
    """
    from pathlib import Path as _Path

    def rag_fn(question: str, module: str = clean_module, top_k: int = 6) -> str:
        hits = rag.retrieve(question, top_k=top_k, module_name=module)
        if not hits:
            return ""
        return "\n\n".join(
            f"[{_Path(h['source']).name}]\n{h['text'].strip()}" for h in hits
        )

    return rag_fn


def _save_accepted_roadmap(module_name: str, pending: str):
    """Persist an accepted roadmap and reset the derived task state.

    Returns the parsed roadmap data so the caller can (re)generate pools.
    """
    rm.save_roadmap_md(module_name, pending)
    dt.delete_plan_and_history(module_name)
    tp.delete_all_pools(module_name)
    return rm.parse_md(pending)


@app.post("/roadmap/{module_name}/accept")
def roadmap_accept(module_name: str):
    pending = _PENDING_ROADMAPS.pop(module_name, None)
    if not pending:
        raise HTTPException(status_code=404, detail="Keine ausstehende Generation gefunden.")
    roadmap_data = _save_accepted_roadmap(module_name, pending)
    # Pre-generate all task pools synchronously (no progress reporting on this path).
    try:
        dt.generate_all_pools(module_name, roadmap_data, rag_fn=_pool_rag_fn(module_name))
    except Exception as exc:
        traceback.print_exc()
        print(f"[roadmap] pool pre-generation failed for {module_name}: {exc}")
    return {"success": True}


@app.post("/roadmap/{module_name}/accept/stream")
def roadmap_accept_stream(module_name: str):
    """SSE version of /accept — saves the roadmap then pre-generates all task pools
    in a background thread, emitting per-topic progress events."""
    pending = _PENDING_ROADMAPS.pop(module_name, None)
    if not pending:
        raise HTTPException(status_code=404, detail="Keine ausstehende Generation gefunden.")

    roadmap_data = _save_accepted_roadmap(module_name, pending)
    rag_fn = _pool_rag_fn(module_name)

    import contextvars as _contextvars
    import queue as _queue
    import threading as _threading

    events: "_queue.Queue" = _queue.Queue()

    def _progress(done: int, total: int, topic: str):
        events.put({"type": "progress", "done": done, "total": total, "topic": topic})

    def _run():
        try:
            dt.generate_all_pools(module_name, roadmap_data, rag_fn=rag_fn, progress_cb=_progress)
            events.put({"type": "done"})
        except Exception as exc:
            traceback.print_exc()
            events.put({"type": "error", "detail": str(exc)})

    # Snapshot the request context (Supabase user JWT / user_id live in ContextVars)
    # so the background thread — and the pool workers it spawns — stay scoped to
    # this user and storage RLS still applies.
    _ctx = _contextvars.copy_context()

    def event_gen():
        yield f"data: {json.dumps({'type': 'step', 'key': 'save', 'label': 'Roadmap gespeichert', 'done': True})}\n\n"
        worker = _threading.Thread(target=_ctx.run, args=(_run,), daemon=True)
        worker.start()
        while True:
            evt = events.get()
            yield f"data: {json.dumps(evt)}\n\n"
            if evt.get("type") in ("done", "error"):
                break

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.post("/modules/{module_name}/extend-pools")
def extend_pools_stream(module_name: str, body: ExtendPoolsRequest):
    """SSE: extend existing topic pools using newly uploaded files.

    Emits ``progress`` events per affected topic and a final ``done`` event with
    ``topics_extended``/``tasks_added``. No-op (immediate ``done`` with zeros) when
    no roadmap exists yet or no new files were given."""
    clean_module = sanitize_module_name(module_name)
    new_files = [f for f in (body.files or []) if f]

    md = rm.load_roadmap_md(clean_module)
    if not md or not new_files:
        def _noop_gen():
            yield f"data: {json.dumps({'type': 'done', 'topics_extended': 0, 'tasks_added': 0})}\n\n"
        return StreamingResponse(_noop_gen(), media_type="text/event-stream")

    roadmap_data = rm.parse_md(md)
    rag_fn = _pool_rag_fn(clean_module)

    import contextvars as _contextvars
    import queue as _queue
    import threading as _threading

    events: "_queue.Queue" = _queue.Queue()

    def _progress(done: int, total: int, topic: str, added: int):
        events.put({"type": "progress", "done": done, "total": total, "topic": topic, "added": added})

    def _run():
        try:
            results = dt.extend_pools_for_new_files(
                clean_module, roadmap_data, new_files, rag_fn=rag_fn, progress_cb=_progress
            )
            events.put({
                "type": "done",
                "topics_extended": sum(1 for v in results.values() if v > 0),
                "tasks_added": sum(results.values()),
            })
        except Exception as exc:
            traceback.print_exc()
            events.put({"type": "error", "detail": str(exc)})

    # Snapshot the request context so the worker (and its pool workers) stay
    # scoped to this Supabase user and storage RLS still applies.
    _ctx = _contextvars.copy_context()

    def event_gen():
        worker = _threading.Thread(target=_ctx.run, args=(_run,), daemon=True)
        worker.start()
        while True:
            evt = events.get()
            yield f"data: {json.dumps(evt)}\n\n"
            if evt.get("type") in ("done", "error"):
                break

    return StreamingResponse(event_gen(), media_type="text/event-stream")


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
    """Return extracted plain text (downloads from Supabase Storage, parses in temp file)."""
    uid = _supa_uid()
    slug = _slug(sanitize_module_name(module_name))
    # Sanitize the client-controlled path (defence-in-depth behind storage RLS),
    # mirroring the upload/raw paths so `../`/absolute paths can't be concatenated
    # into the storage key.
    rel = str(_safe_rel_path(path)).replace("\\", "/")
    storage_path = f"{uid}/{slug}/{rel}"
    file_name = Path(rel).name

    try:
        raw = _supa_client().storage.from_("raw-files").download(storage_path)
        suffix = Path(file_name).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)
        try:
            parsed = parse_document(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        if not parsed.success:
            raise HTTPException(status_code=422, detail=f"Datei konnte nicht gelesen werden: {parsed.error_message}")
        return {"file_name": file_name, "file_type": parsed.file_type, "text": parsed.extracted_text}
    except HTTPException:
        raise
    except Exception:
        # Fallback: local filesystem
        target = _resolve_module_file(module_name, path)
        parsed = parse_document(target)
        if not parsed.success:
            raise HTTPException(status_code=422, detail=f"Datei konnte nicht gelesen werden: {parsed.error_message}")
        return {"file_name": target.name, "file_type": parsed.file_type, "text": parsed.extracted_text}


@app.post("/modules/upload")
@limiter.limit(RL_UPLOAD)
async def upload_module(
    request: Request,
    module_name: str = Form(...),
    files: List[UploadFile] = File(...),
    paths: Optional[str] = Form(None),
):
    clean_module = sanitize_module_name(module_name)

    if not files:
        raise HTTPException(status_code=400, detail="Keine Dateien uebergeben.")

    rel_paths: list = []
    if paths:
        try:
            rel_paths = json.loads(paths)
        except Exception:
            rel_paths = []

    # Ensure module exists in DB (create minimal profile if needed)
    profile = mp.load(clean_module)
    if not profile:
        profile = mp.create_from_onboarding({
            "name": clean_module, "schwerpunkte": [], "stil": "mixed", "pruefungsrelevant": [],
        })
    module_id = profile.get("id") if profile else None

    uid = _supa_uid()
    slug = _slug(clean_module)
    supa = _supa_client()

    saved_paths: List[Path] = []
    skipped_files: List[str] = []

    for i, uploaded in enumerate(files):
        if not uploaded.filename:
            continue

        if i < len(rel_paths) and rel_paths[i]:
            save_rel = _safe_rel_path(rel_paths[i])
        else:
            save_rel = Path(Path(uploaded.filename).name)

        if not is_supported(save_rel):
            skipped_files.append(uploaded.filename)
            continue

        try:
            content = await _read_capped(uploaded)
        except HTTPException:
            # Oversized file: skip and report instead of failing the whole batch.
            skipped_files.append(uploaded.filename)
            continue
        rel_str = str(save_rel).replace("\\", "/")
        storage_path = f"{uid}/{slug}/{rel_str}"
        file_type = save_rel.suffix.lower().lstrip(".")

        # Upload to Supabase Storage raw-files bucket. Under the user-scoped
        # (RLS-enforcing) client there is no storage UPDATE policy — only
        # insert/select/delete — so overwriting an existing object is done as
        # delete-then-insert, which stays within the existing policies.
        try:
            supa.storage.from_("raw-files").upload(
                storage_path, content,
                {"content-type": "application/octet-stream", "x-upsert": "true"},
            )
        except Exception:
            try:
                try:
                    supa.storage.from_("raw-files").remove([storage_path])
                except Exception:
                    pass
                supa.storage.from_("raw-files").upload(
                    storage_path, content, {"content-type": "application/octet-stream"}
                )
            except Exception as exc:
                skipped_files.append(uploaded.filename)
                continue

        # Record in files table
        content_hash = hashlib.sha256(content).hexdigest()
        if module_id:
            try:
                supa.table("files").upsert({
                    "user_id":      uid,
                    "module_id":    module_id,
                    "file_name":    save_rel.name,
                    "relative_path": rel_str,
                    "storage_path": storage_path,
                    "file_type":    file_type,
                    "file_size":    len(content),
                    "content_hash": content_hash,
                    "is_exam":      bool(EXAM_FILE_PATTERN.search(rel_str)),
                }, on_conflict="module_id,relative_path").execute()
            except Exception as exc:
                print(f"[upload] files table upsert failed for {rel_str}: {exc}")

        # Also save to local disk for processing pipeline
        target_dir = module_dir(clean_module)
        target_path = target_dir / save_rel
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(content)
        del content  # free RAM immediately; file is now in Supabase Storage + local disk
        saved_paths.append(target_path)

    if not saved_paths:
        raise HTTPException(status_code=400, detail="Keine unterstuetzten Dateien hochgeladen.")

    # Each worker runs in its own copy of the current request context so any
    # Supabase access inside process_document is scoped to this user (the
    # ContextVars holding the user id / JWT are otherwise not visible to threads).
    with ThreadPoolExecutor(max_workers=min(len(saved_paths), 2)) as pool:
        futures = {}
        for path in saved_paths:
            ctx = contextvars.copy_context()
            futures[pool.submit(ctx.run, process_document, path, config["processed_path"], True, clean_module)] = path
        results = [f.result() for f in as_completed(futures)]
    index_chunks(config, module_name=clean_module)

    processed = sum(1 for result in results if result.get("parseSuccess"))
    skipped = sum(1 for result in results if result.get("skipped"))
    failed = [result for result in results if not result.get("parseSuccess")]

    needs_onboarding = not bool(mp.load(clean_module))

    return {
        "success": True,
        "module_name": clean_module,
        "files": list_module_files(clean_module),
        "saved_files": [p.name for p in saved_paths],
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
@limiter.limit(RL_HEAVY)
@_heavy_daily
async def process_lecture(request: Request, file: UploadFile = File(...)):
    content = await _read_capped(file)

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
        model="claude-haiku-4-5",
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
@limiter.limit(RL_HEAVY)
@_heavy_daily
async def lecture_summarize(request: Request, body: LectureSummarizeRequest):
    """
    Erstellt eine Vorlesungszusammenfassung (Zwei-Stufen-Generierung).
    Prüft zuerst ob Modul-Profil vorhanden – wenn nicht, needs_onboarding=True.
    """
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

    # Datei parsen — versuche zuerst Supabase Storage, dann lokal
    uid = _supa_uid()
    slug = _slug(sanitize_module_name(body.module_name))
    parsed = None
    try:
        storage_path = f"{uid}/{slug}/{body.filename}"
        raw = _supa_client().storage.from_("raw-files").download(storage_path)
        suffix = Path(body.filename).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(raw)
            tmp_path = Path(tmp.name)
        try:
            parsed = parse_document(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception:
        try:
            file_path = _find_file(body.module_name, body.filename)
            parsed = parse_document(file_path)
        except Exception:
            pass

    if not parsed or not parsed.success:
        raise HTTPException(status_code=422, detail=f"Datei konnte nicht gelesen werden.")

    # Zwei-Stufen-Zusammenfassung
    result = summarize(body.module_name, parsed.extracted_text)

    try:
        _index_generated_content(result["summary"], result["saved_to"], body.module_name)
    except Exception as exc:
        print(f"[summarize] RAG-Indizierung fehlgeschlagen (nicht fatal): {exc}")

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
    try:
        profile = mp.load(module_name)
        if profile and profile.get("id"):
            rows = (
                _supa_client()
                .table("summaries")
                .select("title, storage_path, created_at")
                .eq("module_id", profile["id"])
                .order("created_at", desc=True)
                .execute()
            ).data or []
            summaries = []
            for r in rows:
                summaries.append({
                    "titel":   r.get("title") or r.get("storage_path", "").split("/")[-1],
                    "date":    str(r.get("created_at", ""))[:10],
                    "path":    r.get("storage_path", ""),
                    "preview": "",
                })
            return {"summaries": summaries}
    except Exception:
        pass
    return {"summaries": []}


@app.get("/lecture/summary")
def get_lecture_summary(path: str):
    """Gibt Inhalt einer gespeicherten Zusammenfassung zurück (Supabase Storage)."""
    from app.storage import storage_backend as sb
    # path is the storage_path returned by summaries listing
    content = sb.read_text(path)
    if content is None:
        raise HTTPException(status_code=404, detail="Zusammenfassung nicht gefunden.")
    return {"content": content}


class SummaryRenameRequest(BaseModel):
    path: str   # storage_path of the summary
    title: str


@app.patch("/lecture/summaries/{module_name}")
def rename_lecture_summary(module_name: str, body: SummaryRenameRequest):
    """Benennt eine Zusammenfassung um. Ändert nur den DB-`title`; der Storage-Pfad
    bleibt unverändert (der angezeigte Name kommt aus `title`)."""
    new_title = body.title.strip()
    if not new_title:
        raise HTTPException(status_code=400, detail="Titel darf nicht leer sein.")
    profile = mp.load(module_name)
    if not profile or not profile.get("id"):
        raise HTTPException(status_code=404, detail="Modul nicht gefunden.")
    try:
        _supa_client().table("summaries").update({"title": new_title}) \
            .eq("module_id", profile["id"]).eq("storage_path", body.path).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Umbenennen fehlgeschlagen: {exc}")
    return {"success": True, "title": new_title}


@app.delete("/lecture/summaries/{module_name}")
def delete_lecture_summary(module_name: str, path: str):
    """Löscht eine Zusammenfassung: Storage-Objekt + DB-Row. Der `module_id`-Filter
    ist sicherheitsrelevant, da der Service-Key RLS umgeht."""
    from app.storage import storage_backend as sb
    profile = mp.load(module_name)
    if not profile or not profile.get("id"):
        raise HTTPException(status_code=404, detail="Modul nicht gefunden.")
    sb.delete(path)
    try:
        _supa_client().table("summaries").delete() \
            .eq("module_id", profile["id"]).eq("storage_path", path).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Löschen fehlgeschlagen: {exc}")
    return {"success": True}


# ─────────────────────── Solutions endpoints ──────────────────────────────────

def _sanitize_solve_data(data):
    """Entfernt interne Metadaten (verwendetes Modell, Token-Verbrauch, Routing)
    aus solve_data, bevor es an den Client ausgeliefert wird. Nutzer sollen diese
    Informationen nicht sehen können."""
    if not isinstance(data, dict):
        return data
    clean = {k: v for k, v in data.items() if k not in ("total_tokens", "models_used")}
    results = clean.get("results")
    if isinstance(results, list):
        clean["results"] = [
            {k: v for k, v in r.items() if k not in ("model_used", "tokens_used", "route")}
            if isinstance(r, dict) else r
            for r in results
        ]
    return clean


@app.get("/lecture/solutions/{module_name}")
def get_lecture_solutions(module_name: str):
    """Listet alle gespeicherten Lösungen eines Moduls."""
    try:
        profile = mp.load(module_name)
        if profile and profile.get("id"):
            rows = (
                _supa_client()
                .table("solutions")
                .select("name, sheet_path, storage_path, solve_data, created_at")
                .eq("module_id", profile["id"])
                .order("created_at", desc=True)
                .execute()
            ).data or []
            solutions = [
                {
                    "name":         r.get("name") or "Lösung",
                    "sheet_path":   r.get("sheet_path", ""),
                    "storage_path": r.get("storage_path", ""),
                    "solve_data":   _sanitize_solve_data(r.get("solve_data")),
                    "date":         str(r.get("created_at", ""))[:10],
                }
                for r in rows
            ]
            return {"solutions": solutions}
    except Exception:
        pass
    return {"solutions": []}


@app.patch("/lecture/solutions/{module_name}")
def rename_lecture_solution(module_name: str, body: SolutionRenameRequest):
    """Benennt eine Lösung um (nur DB-`name`; Storage-Pfad bleibt unverändert)."""
    new_name = body.new_name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name darf nicht leer sein.")
    profile = mp.load(module_name)
    if not profile or not profile.get("id"):
        raise HTTPException(status_code=404, detail="Modul nicht gefunden.")
    try:
        _supa_client().table("solutions").update({"name": new_name}) \
            .eq("module_id", profile["id"]).eq("storage_path", body.path).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Umbenennen fehlgeschlagen: {exc}")
    return {"success": True, "name": new_name}


@app.delete("/lecture/solutions/{module_name}")
def delete_lecture_solution(module_name: str, path: str):
    """Löscht eine Lösung: Storage-Objekt + DB-Row."""
    from app.storage import storage_backend as sb
    profile = mp.load(module_name)
    if not profile or not profile.get("id"):
        raise HTTPException(status_code=404, detail="Modul nicht gefunden.")
    sb.delete(path)
    try:
        _supa_client().table("solutions").delete() \
            .eq("module_id", profile["id"]).eq("storage_path", path).execute()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Löschen fehlgeschlagen: {exc}")
    return {"success": True}


@app.post("/solve-sheet")
@limiter.limit(RL_HEAVY)
@_heavy_daily
async def solve_sheet(request: Request, body: SolveSheetRequest):
    from app.router import HybridRouter
    from app.solver import ExerciseSheetSolver

    router = HybridRouter(vector_store=vector_store, embedder=embedder, client=anthropic_client)
    solver = ExerciseSheetSolver(router=router, client=anthropic_client)

    results = await solver.solve(body.sheet_text, body.module_id)

    total_tokens = sum(r.tokens_used for r in results)
    models_used: dict = {}
    for r in results:
        models_used[r.model_used] = models_used.get(r.model_used, 0) + 1

    # Gelöstes Blatt speichern und in RAG indizieren
    storage_path = ""
    results_dicts = [
        {
            "aufgabe_nr":   r.aufgabe_nr,
            "aufgabe_text": r.aufgabe_text,
            "loesung":      r.loesung,
            "model_used":   r.model_used,
            "route":        r.route,
            "tokens_used":  r.tokens_used,
        }
        for r in results
    ]
    try:
        md_lines = [f"# Gelöstes Übungsblatt — {body.module_id}", f"**Datum:** {_date.today()}", ""]
        for r in results:
            md_lines += [
                f"---", f"## Aufgabe {r.aufgabe_nr}", "",
                "**Aufgabentext:**", r.aufgabe_text.strip(), "",
                "**Lösung:**", r.loesung.strip(), "",
            ]
        md_content = "\n".join(md_lines)

        from app.storage import storage_backend as sb
        from app.storage.supabase_client import get_client as _get_supa, get_user_id as _get_uid
        sheet_hash = hashlib.md5(body.sheet_text.encode()).hexdigest()[:8]
        storage_path = f"{_slug(body.module_id)}/solved_sheets/{_date.today()}_{sheet_hash}.md"
        sb.write_text(storage_path, md_content)

        # DB-Eintrag (wie summaries)
        try:
            profile = mp.load(body.module_id)
            if profile and profile.get("id"):
                _get_supa().table("solutions").insert({
                    "user_id":      _get_uid(),
                    "module_id":    profile["id"],
                    "sheet_path":   body.sheet_path,
                    "name":         body.sheet_name or f"Lösung {_date.today()}",
                    "storage_path": storage_path,
                    "solve_data":   {"results": results_dicts, "total_tokens": total_tokens, "models_used": models_used},
                }).execute()
        except Exception as db_exc:
            print(f"[solve_sheet] DB-Eintrag fehlgeschlagen (nicht fatal): {db_exc}")

        await asyncio.to_thread(_index_generated_content, md_content, storage_path, body.module_id)
    except Exception as exc:
        print(f"[solve_sheet] Speichern/Indizieren fehlgeschlagen (nicht fatal): {exc}")

    response = _sanitize_solve_data({
        "results": results_dicts,
        "total_tokens": total_tokens,
        "models_used": models_used,
    })
    response["storage_path"] = storage_path
    return response


def _list_storage_prefix_recursive(supa, bucket: str, prefix: str, _depth: int = 0) -> list:
    """Return full object paths under a storage prefix, recursing into subfolders.

    Supabase ``list()`` is not recursive and returns subfolders as placeholder
    entries (no ``id``/``metadata``). We descend into those so nested files
    (e.g. ``processed/<slug>/summaries/*`` or nested raw-file folders) are caught.
    """
    out: list = []
    if _depth > 6:
        return out
    try:
        items = supa.storage.from_(bucket).list(prefix)
    except Exception:
        return out
    for item in (items or []):
        name = item.get("name")
        if not name:
            continue
        full = f"{prefix}/{name}"
        is_folder = item.get("id") is None and item.get("metadata") is None
        if is_folder:
            out.extend(_list_storage_prefix_recursive(supa, bucket, full, _depth + 1))
        else:
            out.append(full)
    return out


@app.delete("/modules/{module_name}")
def delete_module(module_name: str):
    """Löscht einen Kurs vollständig: DB-Zeilen, Chunks/Embeddings, Storage
    (raw-files + processed) und lokale Reste.

    Reihenfolge bewusst gewählt: Chunks und Storage werden gelöscht, *solange das
    Modul noch existiert*, und die ``modules``-Zeile erst ganz zuletzt. So kann
    keine spätere Namensauflösung das Modul versehentlich neu anlegen.
    """
    clean_name = sanitize_module_name(module_name)
    slug = _slug(clean_name)
    uid = _supa_uid()
    supa = _supa_client()

    profile = mp.load(clean_name)
    module_id = (profile or {}).get("id")

    # 1. Delete pgvector chunks first, directly by module_id (no name resolution,
    #    so the auto-create path can never fire). Falls back to the name-based
    #    delete only when no profile/id was found.
    try:
        if module_id:
            supa.table("chunks").delete().eq("user_id", uid).eq("module_id", module_id).execute()
        else:
            vector_store.delete(where={"module_name": clean_name})
    except Exception as exc:
        print(f"[delete_module] chunks delete failed (non-fatal): {exc}")

    # 2. Delete every raw file for this module. Prefer the exact storage_paths
    #    recorded in the files table (handles nested folders), then sweep the
    #    storage prefix as a backstop for anything not in the table.
    try:
        raw_paths: list = []
        if module_id:
            rows = (
                supa.table("files")
                .select("storage_path")
                .eq("module_id", module_id)
                .execute()
            ).data or []
            raw_paths = [r["storage_path"] for r in rows if r.get("storage_path")]
        raw_paths += _list_storage_prefix_recursive(supa, "raw-files", f"{uid}/{slug}")
        raw_paths = list(dict.fromkeys(raw_paths))  # dedupe, preserve order
        if raw_paths:
            supa.storage.from_("raw-files").remove(raw_paths)
    except Exception as exc:
        print(f"[delete_module] raw-files delete failed (non-fatal): {exc}")

    # 3. Delete every processed artifact (roadmap, daily plan, history,
    #    summaries/*, exams/*) under processed/<uid>/<slug>/.
    try:
        proc_paths = _list_storage_prefix_recursive(supa, "processed", f"{uid}/{slug}")
        if proc_paths:
            supa.storage.from_("processed").remove(proc_paths)
    except Exception as exc:
        print(f"[delete_module] processed delete failed (non-fatal): {exc}")

    # 4. Delete DB rows last. Remove children explicitly (works with or without
    #    ON DELETE CASCADE), then the module itself.
    if module_id:
        for child_table in ("chunks", "summaries", "exams", "documents", "files"):
            try:
                supa.table(child_table).delete().eq("module_id", module_id).execute()
            except Exception as exc:
                print(f"[delete_module] {child_table} delete failed (non-fatal): {exc}")
        try:
            supa.table("modules").delete().eq("id", module_id).execute()
        except Exception as exc:
            print(f"[delete_module] modules delete failed: {exc}")
    else:
        # Fallback: delete by user_id + slug (no profile found but may still be in DB)
        try:
            supa.table("modules").delete().eq("user_id", uid).eq("slug", slug).execute()
        except Exception as exc:
            print(f"[delete_module] modules delete by slug failed: {exc}")

    # 5. Drop the in-memory module-id cache so the name can't resolve to a stale
    #    (now-deleted) id on the next operation.
    try:
        from app.vectorstore.pgvector_store import purge_module_cache
        purge_module_cache(clean_name, slug)
    except Exception:
        pass

    # 6. Local filesystem cleanup (best-effort)
    raw = module_dir(clean_name)
    if raw.exists():
        shutil.rmtree(raw, ignore_errors=True)

    chunks_dir = config["processed_path"] / "chunks"
    clean_lower = clean_name.lower()
    if chunks_dir.exists():
        for jsonl in list(chunks_dir.glob("*.jsonl")):
            try:
                first_line = jsonl.read_text(encoding="utf-8").splitlines()[0]
                chunk = json.loads(first_line)
                meta = chunk.get("metadata", {})
                module_name_meta = meta.get("module_name", "")
                if module_name_meta.lower() == clean_lower:
                    jsonl.unlink()
            except Exception:
                pass

    # Drop the saved sidebar folder layout so a module recreated under the same
    # name doesn't resurrect old folders (mirrors the localStorage cleanup).
    _clear_folder_config(clean_name)

    return {"success": True, "deleted": clean_name}


# ─────────────────────── Probeklausur endpoints ───────────────────────────────

@app.post("/exam/generate")
@limiter.limit(RL_HEAVY)
@_heavy_daily
async def exam_generate(request: Request, body: ExamGenerateRequest):
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

    # RAG context from real course material only (never previously generated exams).
    rag_context = _course_context_excluding_generated(
        clean_name,
        "Wichtige Konzepte, Definitionen, Methoden und prüfungsrelevante Inhalte.",
        top_k=20,
    )
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

    # Note: generated exams are deliberately NOT indexed into pgvector — doing so
    # would feed them back into the generation context and clone old exams.
    n = eg.save_exam(clean_name, md_content)
    return {"success": True, "n": n, "module_name": clean_name}


@app.post("/exam/generate/stream")
@limiter.limit(RL_HEAVY)
@_heavy_daily
async def exam_generate_stream(request: Request, body: ExamGenerateRequest):
    """SSE version of /exam/generate — emits step events then a result event."""

    async def event_gen():
        clean_name = sanitize_module_name(body.module_name)

        existing = eg.list_exams(clean_name)
        if len(existing) >= 100:
            yield f"data: {json.dumps({'type': 'error', 'detail': 'Limit von 100 Klausuren erreicht. Bitte alte löschen.'})}\n\n"
            return

        all_files = list_module_files(clean_name)

        yield f"data: {json.dumps({'type': 'step', 'key': 'analyze_style', 'label': 'Klausurstil analysieren…', 'done': False})}\n\n"
        yield f"data: {json.dumps({'type': 'step', 'key': 'rag', 'label': 'Inhalte laden…', 'done': False})}\n\n"

        try:
            analyze_coro = asyncio.to_thread(_exam_analyze_cached, clean_name, all_files)
            rag_coro = asyncio.to_thread(
                _course_context_excluding_generated,
                clean_name,
                "Wichtige Konzepte, Definitionen, Methoden und prüfungsrelevante Inhalte.",
                20,
            )

            (_, exam_style), rag_context = await asyncio.gather(analyze_coro, rag_coro)

            if not rag_context:
                yield f"data: {json.dumps({'type': 'error', 'detail': 'Keine Inhalte gefunden. Bitte zuerst Materialien hochladen.'})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'step', 'key': 'analyze_style', 'label': 'Klausurstil analysiert', 'done': True})}\n\n"
            yield f"data: {json.dumps({'type': 'step', 'key': 'rag', 'label': 'Inhalte geladen', 'done': True})}\n\n"
            yield f"data: {json.dumps({'type': 'step', 'key': 'generate', 'label': 'Probeklausur generieren…', 'done': False})}\n\n"

            md_content = await asyncio.to_thread(
                eg.generate,
                module_name=clean_name,
                exam_style=exam_style,
                rag_context=rag_context,
                num_tasks=body.num_tasks,
                total_points=body.total_points,
            )

            # Generated exams are deliberately NOT indexed into pgvector (avoids the
            # feedback loop where a new exam clones a previously generated one).
            n = eg.save_exam(clean_name, md_content)
            yield f"data: {json.dumps({'type': 'result', 'data': {'success': True, 'n': n, 'module_name': clean_name}})}\n\n"

        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream")


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

@app.get("/daily/dashboard")
async def daily_dashboard():
    """Aggregated dashboard: active plans per module + last 4 completed tasks across all modules."""
    modules = _all_module_names()

    # Pre-build the per-request user client (and its sub-clients) so the parallel
    # workers share one connection pool instead of each racing to build its own.
    # Best-effort: a pre-warm failure must not break the endpoint.
    if modules:
        try:
            _client = _supa_client()
            _ = (_client.postgrest, _client.storage)
        except Exception:
            pass

    # Resolve every module's slug in ONE query up front, instead of each
    # per-module bundle calling mp.load (a full modules-table fetch) again.
    slug_map = await asyncio.to_thread(mp.all_slugs) if modules else {}

    # Fan the per-module storage reads out concurrently. supabase-py is sync, so
    # each module's bundle runs in a worker thread (the request context — user id
    # + JWT — is copied into each thread by asyncio.to_thread). This turns the old
    # N sequential round-trips into ~1 wall-clock round-trip.
    bundles = (
        await asyncio.gather(*[
            asyncio.to_thread(dt.load_dashboard_bundle, m, slug_map.get(m) or slug_map.get(m.lower()))
            for m in modules
        ])
        if modules else []
    )

    today_plans = []
    all_completed = []
    total_hours = 0.0

    for module_name, (parsed, history) in zip(modules, bundles):
        if parsed:
            today_plans.append({
                "module": module_name,
                "daily_hours": parsed.get("daily_hours", 0.0),
                "progress": parsed.get("progress", {"done": 0, "total": 0}),
                "topics": parsed.get("topics", []),
                "has_plan": True,
            })
            total_hours += parsed.get("daily_hours", 0.0)
        else:
            today_plans.append({
                "module": module_name,
                "daily_hours": 0.0,
                "progress": {"done": 0, "total": 0},
                "topics": [],
                "has_plan": False,
            })
        for entry in history:
            all_completed.append({
                "module": module_name,
                "task_text": entry.get("task_text", ""),
                "topic_name": entry.get("topic_name", ""),
                "completed_date": entry.get("completed_date", ""),
            })

    remaining_minutes = sum(
        task.get("minutes", 45)
        for plan in today_plans
        if plan["has_plan"]
        for topic in plan["topics"]
        if not topic["id"].endswith("_review")
        for task in topic["tasks"]
        if not task["done"]
    )

    from datetime import date as _date, timedelta as _td
    _today = _date.today()
    _recent = {_today.isoformat(), (_today - _td(days=1)).isoformat()}
    completed_today = sum(1 for e in all_completed if e.get("completed_date") in _recent)

    all_completed.sort(key=lambda x: x.get("completed_date", ""), reverse=True)
    return {
        "today_plans": today_plans,
        "remaining_minutes": remaining_minutes,
        "completed_today": completed_today,
        "recent_completed": all_completed[:4],
    }


@app.get("/daily/{module_name}")
def daily_get(module_name: str):
    """Load active daily plan. Returns {exists: false} if none."""
    clean = sanitize_module_name(module_name)
    md = dt.load_plan(clean)
    if not md:
        return {"exists": False}
    parsed = dt.parse_plan(md)
    return {"exists": True, **parsed}


@app.get("/daily/{module_name}/pool/{topic_id}")
def daily_pool_get(module_name: str, topic_id: str):
    """Read-only view of a topic's task pool (used by the roadmap detail card).

    Returns ``{exists: false}`` when no pool exists, otherwise the full task list
    plus a progress summary. Purely reading — never generates.
    """
    clean = sanitize_module_name(module_name)
    pool = tp.load_pool(clean, topic_id)
    if not pool:
        return {"exists": False}
    tasks = [
        {
            "text": str(t.get("text", "")),
            "done": bool(t.get("done")),
            "minutes": t.get("minutes", 45),
        }
        for t in (pool.get("tasks") or [])
    ]
    done = sum(1 for t in tasks if t["done"])
    return {"exists": True, "tasks": tasks, "progress": {"done": done, "total": len(tasks)}}


@app.post("/daily/{module_name}/generate")
@limiter.limit(RL_HEAVY)
@_heavy_daily
async def daily_generate(request: Request, module_name: str, body: DailyGenerateRequest):
    """Generate a new daily plan. Archives old plan and applies carryover."""
    try:
        clean = sanitize_module_name(module_name)
        if body.daily_hours < 0.5 or body.daily_hours > 12:
            raise HTTPException(status_code=400, detail="daily_hours muss zwischen 0.5 und 12 liegen.")

        roadmap_md = rm.load_roadmap_md(clean)
        if not roadmap_md:
            raise HTTPException(status_code=404, detail="Keine Roadmap gefunden. Erst Roadmap generieren.")
        roadmap_data = rm.parse_md(roadmap_md)

        new_md = dt.generate(
            clean,
            daily_hours=body.daily_hours,
            roadmap_data=roadmap_data,
            rag_fn=_pool_rag_fn(clean),
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
        result = dt.toggle_task(clean, body.topic_id, body.task_index, body.done)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    parsed = dt.parse_plan(result["md"])
    return {
        "success": True,
        "progress": parsed["progress"],
        "card_completed": result["card_completed"],
        "topic_id": result["topic_id"],
        "topic_name": result["topic_name"],
    }


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
    from app.storage import storage_backend as sb
    sb.delete(dt.task_history_path(clean))
    return {"success": True}


# ─────────────────────── Topic completion quiz endpoints ──────────────────────

@app.get("/quiz/topic/{module_name}")
def topic_quiz_list(module_name: str):
    """List metadata of all saved completion quizzes for a module."""
    clean = sanitize_module_name(module_name)
    return {"quizzes": tq.list_quizzes(clean)}


@app.get("/quiz/topic/{module_name}/{topic_id}")
def topic_quiz_get(module_name: str, topic_id: str):
    """Load a saved quiz without regenerating it."""
    clean = sanitize_module_name(module_name)
    quiz = tq.load_quiz(clean, topic_id)
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz nicht gefunden.")
    return {"success": True, **quiz}


@app.delete("/quiz/topic/{module_name}/{topic_id}")
def topic_quiz_delete(module_name: str, topic_id: str):
    """Delete a saved quiz."""
    clean = sanitize_module_name(module_name)
    deleted = tq.delete_quiz(clean, topic_id)
    return {"success": deleted}


def _find_roadmap_topic(roadmap_data: dict, topic_id: str) -> Optional[dict]:
    for phase in roadmap_data.get("phases", []):
        for topic in phase.get("topics", []):
            if topic.get("id") == topic_id:
                return topic
    return None


@app.post("/quiz/topic/{module_name}/{topic_id}")
@limiter.limit(RL_HEAVY)
@_heavy_daily
async def topic_quiz_generate(request: Request, module_name: str, topic_id: str):
    """Generate (and persist) a completion quiz for a roadmap topic."""
    clean = sanitize_module_name(module_name)
    roadmap_md = rm.load_roadmap_md(clean)
    if not roadmap_md:
        raise HTTPException(status_code=404, detail="Keine Roadmap gefunden.")
    roadmap_data = rm.parse_md(roadmap_md)
    topic = _find_roadmap_topic(roadmap_data, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail=f"Thema {topic_id} nicht gefunden.")

    name = topic.get("name", "")
    subtopics = topic.get("subtopics") or []
    rag_query = name + ((" — " + ", ".join(subtopics[:4])) if subtopics else "")
    rag_context = await asyncio.to_thread(
        _course_context_excluding_generated,
        clean,
        rag_query or "Wichtige Konzepte und Definitionen.",
        10,
    )

    try:
        quiz = await asyncio.to_thread(tq.generate, clean, topic, rag_context)
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Quiz-Generierung fehlgeschlagen: {exc}")
    return {"success": True, **quiz}


@app.post("/quiz/topic/{module_name}/{topic_id}/complete")
def topic_quiz_complete(module_name: str, topic_id: str):
    """Mark the roadmap card as done after the user finishes its completion quiz."""
    clean = sanitize_module_name(module_name)
    md = rm.load_roadmap_md(clean)
    if not md:
        raise HTTPException(status_code=404, detail="Keine Roadmap gefunden.")
    try:
        md = rm.update_topic_status(md, topic_id, "done")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    rm.save_roadmap_md(clean, md)
    # Stamp the quiz as completed so the practice-worksheet feature can unlock
    # (requires both: card done AND quiz completed).
    tq.mark_completed(clean, topic_id)
    return {"success": True}


# ─────────────────────── Topic practice worksheet endpoints ───────────────────
# A worksheet ("Übungsblatt") is a topic-scoped sheet of fresh practice exercises in
# the style of the course materials. Unlike the quiz it does NOT gate completion and
# is unlimited per day — only throttled per-minute and serialized per user.

@app.get("/worksheet/topic/{module_name}")
def topic_worksheet_list(module_name: str):
    """List metadata of all saved practice worksheets for a module."""
    clean = sanitize_module_name(module_name)
    return {"worksheets": tw.list_worksheets(clean)}


@app.get("/worksheet/topic/{module_name}/{worksheet_id}")
def topic_worksheet_get(module_name: str, worksheet_id: str):
    """Load a saved worksheet without regenerating it."""
    clean = sanitize_module_name(module_name)
    worksheet = tw.load_worksheet(clean, worksheet_id)
    if not worksheet:
        raise HTTPException(status_code=404, detail="Übungsblatt nicht gefunden.")
    return {"success": True, **worksheet}


@app.delete("/worksheet/topic/{module_name}/{worksheet_id}")
def topic_worksheet_delete(module_name: str, worksheet_id: str):
    """Delete a saved worksheet."""
    clean = sanitize_module_name(module_name)
    deleted = tw.delete_worksheet(clean, worksheet_id)
    return {"success": deleted}


@app.post("/worksheet/topic/{module_name}/{topic_id}")
@limiter.limit(RL_WORKSHEET)
async def topic_worksheet_generate(request: Request, module_name: str, topic_id: str):
    """Generate (and persist) a new practice worksheet for a roadmap topic.

    Unlimited per day; throttled per-minute by RL_WORKSHEET and serialized per user
    via an in-flight guard so only one generation runs at a time.
    """
    uid = _supa_uid()
    if uid in _worksheet_inflight:
        raise HTTPException(
            status_code=409,
            detail="Es läuft bereits eine Generierung. Bitte warte, bis sie fertig ist.",
        )

    clean = sanitize_module_name(module_name)
    roadmap_md = rm.load_roadmap_md(clean)
    if not roadmap_md:
        raise HTTPException(status_code=404, detail="Keine Roadmap gefunden.")
    roadmap_data = rm.parse_md(roadmap_md)
    topic = _find_roadmap_topic(roadmap_data, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail=f"Thema {topic_id} nicht gefunden.")

    # Unlock gate (defense in depth — the frontend already hides the button): the
    # card must be done AND its quiz completed.
    if topic.get("status") != "done":
        raise HTTPException(status_code=403, detail="Schließe zuerst dieses Thema ab.")
    topic_quiz = tq.load_quiz(clean, topic_id)
    if not topic_quiz or not topic_quiz.get("completed_at"):
        raise HTTPException(status_code=403, detail="Schließe zuerst das Quiz dieses Themas ab.")

    name = topic.get("name", "")
    subtopics = topic.get("subtopics") or []
    rag_query = name + ((" — " + ", ".join(subtopics[:4])) if subtopics else "")

    _worksheet_inflight.add(uid)
    try:
        rag_context = await asyncio.to_thread(
            _course_context_excluding_generated,
            clean,
            rag_query or "Wichtige Konzepte, Aufgaben und Methoden.",
            10,
        )
        worksheet = await asyncio.to_thread(tw.generate, clean, topic, rag_context)
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Übungsblatt-Generierung fehlgeschlagen: {exc}")
    finally:
        _worksheet_inflight.discard(uid)
    return {"success": True, **worksheet}


# ─────────────────────── Account: Datenexport & -löschung (DSGVO) ─────────────
# Self-service for the data-subject rights every EU user has: Art. 15/20 (access
# & portability → export) and Art. 17 (erasure → deletion). Both run strictly
# scoped to the authenticated user — never an admin-wide operation.

# Every per-user table carries a user_id column except `settings`, whose PK *is*
# the user_id. The export filters all of them by user_id; deletion relies on the
# ON DELETE CASCADE from auth.users (see SUPABASE.md) plus a manual storage purge.
_EXPORT_TABLES = ["modules", "files", "documents", "chunks", "summaries", "exams", "settings"]


@app.get("/account/export")
def account_export(request: Request):
    """DSGVO Art. 15 & 20: download all of the user's data as one ZIP.

    Contains ``data.json`` (every DB row belonging to the user, across all
    tables) plus ``files/<bucket>/…`` (the raw uploads and generated artifacts
    from both storage buckets). Embedding vectors are stripped from ``chunks`` —
    they're large and not human-meaningful, the chunk_text is included instead.
    """
    from app.storage import storage_backend as sb

    uid = _supa_uid()
    supa = _supa_client()

    data: dict = {
        "user_id": uid,
        "exported_at": _date.today().isoformat(),
        "tables": {},
    }
    for table in _EXPORT_TABLES:
        try:
            rows = supa.table(table).select("*").eq("user_id", uid).execute().data or []
        except Exception as exc:
            rows = [{"_error": str(exc)}]
        if table == "chunks":
            for r in rows:
                r.pop("embedding", None)  # drop 1536-dim vectors from the export
        data["tables"][table] = rows

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.json", json.dumps(data, ensure_ascii=False, indent=2, default=str))
        zf.writestr(
            "README.txt",
            "Veexa Datenexport (DSGVO Art. 15/20)\n\n"
            "data.json  — alle zu deinem Konto gespeicherten Datenbank-Eintraege.\n"
            "files/      — deine hochgeladenen Originaldateien und generierten Inhalte.\n",
        )
        try:
            objects = sb.list_all_user_objects()
        except Exception:
            objects = {}
        for bucket, paths in objects.items():
            for full in paths:
                content = sb.download_object(bucket, full)
                if content is None:
                    continue
                rel = full[len(uid) + 1:] if full.startswith(uid + "/") else full
                zf.writestr(f"files/{bucket}/{rel}", content)

    zip_bytes = buf.getvalue()
    fname = f"veexa-export-{_date.today().isoformat()}.zip"
    return StreamingResponse(
        iter([zip_bytes]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.delete("/account")
def account_delete(request: Request):
    """DSGVO Art. 17: permanently delete the account and ALL associated data.

    Order matters: purge storage *first* (user-scoped client, so it can only
    touch the caller's own ``{uid}/`` tree), then delete the Supabase auth user
    via the admin client — its ON DELETE CASCADE on every table
    (modules/files/documents/chunks/summaries/exams/settings) removes all
    remaining DB rows. Irreversible; the frontend gates it behind a typed
    confirmation and logs the user out afterwards.
    """
    from app.storage import storage_backend as sb
    from app.storage.supabase_client import get_admin_client

    uid = _supa_uid()

    # 1) Storage: empty both buckets under {uid}/ (RLS-scoped to this user).
    storage_removed = 0
    try:
        storage_removed = sb.purge_user_storage()
    except Exception as exc:
        print(f"[account_delete] storage purge failed: {exc}")

    # 2) pgvector/chunks: explicit user-scoped clear (belt-and-suspenders — the
    #    cascade below also covers it, but this guarantees vectors are gone even
    #    if the auth delete later fails).
    try:
        vector_store.clear()
    except Exception as exc:
        print(f"[account_delete] pgvector clear failed: {exc}")

    # 3) Auth user → cascade wipes every remaining DB row. Admin client required.
    auth_deleted = False
    try:
        get_admin_client().auth.admin.delete_user(uid)
        auth_deleted = True
    except Exception as exc:
        print(f"[account_delete] auth user delete failed: {exc}")
        # Fallback: at least delete the DB rows explicitly so no data lingers,
        # even though the login record could not be removed.
        for table in ["chunks", "documents", "summaries", "exams", "files", "settings", "modules"]:
            try:
                _supa_client().table(table).delete().eq("user_id", uid).execute()
            except Exception as inner:
                print(f"[account_delete] fallback delete {table} failed: {inner}")

    return {
        "success": True,
        "auth_user_deleted": auth_deleted,
        "storage_objects_removed": storage_removed,
    }
