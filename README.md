# AI Student Agent

A personal study assistant that turns your lecture materials into an interactive knowledge base. Upload PDFs, PowerPoints, and notes — then ask questions, generate practice exams, build study roadmaps, and get your exercise sheets solved automatically.

## What it does

### Document Ingestion & Processing
- Scans `data/raw/` for supported files (PDF, PPTX, TXT, MD)
- Parses documents using `pypdf` and `python-pptx`; falls back to **GPT-4o Vision OCR** for image-based or scanned PDFs
- Fixes encoding issues automatically (`ftfy`)
- Splits text into overlapping character chunks (500 chars, 50-char overlap; 300 chars for slides)
- Generates embeddings with OpenAI `text-embedding-3-small` and stores them persistently in **ChromaDB**
- Skips already-processed files using deterministic content hashes — re-running the pipeline is safe

### RAG Question Answering
- Ask questions in natural language via `/ask` or `/ask/stream` (Server-Sent Events)
- Advanced pipeline: decomposes the question into sub-queries, routes between a simple and multi-step path, fuses results
- Hybrid router selects **Claude Haiku** for simple lookups and **Claude Sonnet** for complex reasoning
- Supports optional module filtering and multi-turn chat history
- Sources returned with every answer

### Module Management
- Files are organized into modules (one folder per course)
- Upload single files or entire folder trees through the UI; unsupported formats are silently skipped
- Preview raw files (PDF, PPTX, TXT, MD) directly in the browser
- Delete individual files or entire modules — raw files, chunk JSONL, ChromaDB embeddings, parsed JSON, summaries, and roadmaps are all cleaned up atomically
- Exam files are auto-detected by filename pattern (`klausur`, `exam`, `test`, …) and can be toggled manually per file

### Lecture Summaries
- Two-step AI summarization of any lecture file
- Extracts key concepts, generates a structured Markdown summary
- Summaries are saved to `data/processed/summaries/` and automatically indexed into ChromaDB so they become part of the knowledge base

### Study Roadmap
- Generate a personalized study roadmap per module with one click
- Takes into account: RAG-retrieved course content, exam style analysis from past exam files, exam date, and user-defined focus areas
- Output is a Mermaid flowchart (roadmap.sh-style) with phases: prerequisites → basics → core concepts → methods → practice → exam training → common mistakes → review
- Hours per topic are scaled dynamically to the time remaining before the exam date
- Topic status (`todo` / `doing` / `done`) can be updated in the UI; status is preserved when the roadmap is regenerated
- SSE streaming version (`/roadmap/generate/stream`) emits step-by-step progress events

### Practice Exam Generator
- Generates a full practice exam from your study material
- Analyzes the style of past exams (question types, difficulty distribution) and replicates it
- RAG context ensures questions actually cover the indexed content
- Configurable number of tasks and total points
- Generated exams are saved, listed, and retrievable; up to 100 exams per module
- SSE streaming version available
- Generated exams are indexed into ChromaDB so they feed future RAG queries

### Daily Task Planner
- Generates a concrete daily study plan based on the active roadmap
- Configurable daily study hours (0.5 – 12 h)
- Plan is structured into topics with individual checkboxes
- Checkboxes can be toggled via the API; progress is tracked in real time
- Completed tasks are archived in a history file for spaced-repetition review
- `/daily/{module}/review` returns randomly sampled past tasks for review
- Statistics endpoint shows total tasks done, per-topic counts, and per-day completion

### Exercise Sheet Solver
- Paste the text of an exercise sheet; the solver splits it into individual tasks automatically
- Each task is routed to the appropriate model (Haiku or Sonnet) based on semantic similarity and LLM classification
- All tasks are solved in parallel with `asyncio.gather` for fast turnaround
- Solutions follow a strict format: LaTeX for math, numbered steps, no filler text
- Solved sheets are saved as Markdown and indexed into ChromaDB

## Architecture

```
AI-Student-Agent/
├── app/
│   ├── api.py                  # FastAPI application (all endpoints)
│   ├── main.py                 # CLI runner (ingest, index, query)
│   ├── router.py               # Hybrid model router (Haiku / Sonnet)
│   ├── solver.py               # Exercise sheet solver
│   ├── ingestion/              # File scanning and intake
│   ├── parsing/                # Document parsers + GPT-4o OCR
│   ├── chunking/               # Text chunking pipeline
│   ├── embeddings/             # OpenAI embedding client
│   ├── vectorstore/            # ChromaDB wrapper
│   ├── storage/                # JSON/JSONL persistence layer
│   ├── rag/                    # Advanced RAG (multi-query, routing, reranking)
│   ├── lecture/                # Module profiles, summaries, roadmap, exam generator, daily tasks
│   ├── utils/                  # Config, logging
│   └── static/                 # Frontend HTML
├── data/
│   ├── raw/                    # Input documents (one subfolder per module)
│   ├── processed/              # Chunks, parsed JSON, summaries, roadmaps, exams, daily plans
│   ├── modules/                # Module profiles and exam analysis
│   ├── chroma/                 # ChromaDB vector index
│   └── settings.json           # App settings (favorite module, …)
├── notebooks/                  # Jupyter design and implementation notebooks
└── requirements.txt
```

## Models used

| Task | Model |
|------|-------|
| Embeddings | `text-embedding-3-small` (OpenAI) |
| OCR (image PDFs) | `gpt-4o-mini` (OpenAI Vision) |
| Simple Q&A, daily tasks | `claude-haiku-4-5` (Anthropic) |
| Complex Q&A, roadmap, exam gen | `claude-sonnet-4-6` (Anthropic) |
| Exercise routing classifier | `claude-haiku-4-5` + similarity score |

## Quick Start

### 1. Setup

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux / Mac
pip install -r requirements.txt
```

### 2. Configure

Copy `.env.example` to `.env` and set your API keys:

```env
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-ant-...

# Optional — defaults shown
DATA_RAW_PATH=data/raw
DATA_PROCESSED_PATH=data/processed
DATA_EXPORTS_PATH=data/exports
CHROMA_PATH=data/chroma
EMBEDDING_MODEL=text-embedding-3-small
OPENAI_MODEL=gpt-4o-mini
RAG_TOP_K=5
LOG_LEVEL=INFO

# Optional Obsidian vault integration
OBSIDIAN_VAULT=C:/path/to/your/vault
```

### 3. Run the API server

```bash
uvicorn app.api:app --reload
```

Open `http://localhost:8000` to use the UI.

### 4. CLI usage (optional)

```bash
# Full pipeline: ingest → parse → chunk → embed → index
python -m app.main

# Only (re-)index existing chunks
python -m app.main --index-only

# Ask a question directly from the terminal
python -m app.main --query "Was ist ein Binärbaum?"
```

## Supported file types

| Format | Extension | Notes |
|--------|-----------|-------|
| PDF | `.pdf` | pypdf; GPT-4o OCR fallback for image-based pages |
| PowerPoint | `.pptx` | python-pptx; smaller chunks (300 chars) |
| Plain text | `.txt` | built-in |
| Markdown | `.md` | built-in |

## API overview

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Frontend UI |
| `POST` | `/ask` | Ask a question (JSON response) |
| `POST` | `/ask/stream` | Ask a question (SSE streaming) |
| `GET` | `/modules` | List all modules |
| `POST` | `/modules/upload` | Upload files to a module |
| `GET` | `/modules/{m}/files` | List files in a module |
| `GET` | `/modules/{m}/raw?path=` | Stream raw file for browser preview |
| `DELETE` | `/modules/{m}/file?path=` | Delete a file and all its indexed data |
| `POST` | `/modules/{m}/file/exam-flag?path=` | Toggle exam flag for a file |
| `DELETE` | `/modules/{m}` | Delete entire module |
| `POST` | `/lecture/summarize` | Generate lecture summary |
| `GET` | `/lecture/summaries/{m}` | List summaries for a module |
| `GET` | `/lecture/summary?path=` | Load a specific summary |
| `POST` | `/lecture/onboarding` | Save module profile |
| `POST` | `/roadmap/generate` | Generate study roadmap |
| `POST` | `/roadmap/generate/stream` | Generate roadmap (SSE) |
| `GET` | `/roadmap/{m}` | Load roadmap |
| `PATCH` | `/roadmap/{m}/topic/{id}` | Update topic status |
| `DELETE` | `/roadmap/{m}` | Delete roadmap |
| `POST` | `/exam/generate` | Generate practice exam |
| `POST` | `/exam/generate/stream` | Generate practice exam (SSE) |
| `GET` | `/exam/{m}` | List exams for a module |
| `GET` | `/exam/{m}/{n}` | Load exam n |
| `DELETE` | `/exam/{m}/{n}` | Delete exam n |
| `GET` | `/daily/{m}` | Load today's plan |
| `POST` | `/daily/{m}/generate` | Generate new daily plan |
| `PATCH` | `/daily/{m}/task` | Toggle task checkbox |
| `GET` | `/daily/{m}/stats` | Completion statistics |
| `GET` | `/daily/{m}/review` | Spaced-repetition review tasks |
| `POST` | `/solve-sheet` | Solve an exercise sheet |

## License

MIT
