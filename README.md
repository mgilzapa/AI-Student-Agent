# AI Student Agent — Phase 1, Sprint 1

Personal AI Study Agent for processing study documents into a retrievable format.

## Sprint 1 Status: COMPLETE

This sprint establishes the foundation for document ingestion, parsing, chunking, and local persistence.

## Quick Start

### 1. Setup Environment

```bash
# Create virtual environment (if not exists)
python -m venv .venv

# Activate
.venv\Scripts\activate  # Windows
source .venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Paths

```bash
# Copy example environment file
cp .env.example .env

# Edit .env to customize paths (optional)
```

### 3. Add Study Documents

Place your study materials in `data/raw/`:
- PDF files (`.pdf`)
- PowerPoint presentations (`.pptx`)
- Text files (`.txt`)
- Markdown files (`.md`)

### 4. Run the Pipeline

```bash
python -m app.main
```

### 5. View Results

Processed outputs are saved to:
- `data/processed/parsed/` — Parsed documents with metadata (JSON)
- `data/processed/chunks/` — Text chunks for retrieval (JSONL)

## Project Structure

```
study-agent/
├── app/
│   ├── ingestion/      # File intake and scanning
│   ├── parsing/        # Document parsers (PDF, PPTX, TXT)
│   ├── chunking/       # Text chunking pipeline
│   ├── storage/        # Local persistence layer
│   ├── utils/          # Config and logging
│   └── main.py         # Entry point
├── data/
│   ├── raw/            # Input documents
│   ├── processed/      # Output files
│   └── exports/        # Exports and logs
├── notebooks/          # Jupyter notebooks
├── tests/              # Unit tests
├── requirements.txt    # Python dependencies
├── .env.example        # Environment template
└── README.md
```

## Supported File Types (Sprint 1)

| Format | Extension | Parser |
|--------|-----------|--------|
| PDF | `.pdf` | pypdf |
| PowerPoint | `.pptx` | python-pptx |
| Plain Text | `.txt` | built-in |
| Markdown | `.md` | built-in |

## Configuration

Edit `.env` to customize:

```env
DATA_RAW_PATH=data/raw
DATA_PROCESSED_PATH=data/processed
DATA_EXPORTS_PATH=data/exports
LOG_LEVEL=INFO
```

## Chunking Strategy

- **Default chunk size:** 500 characters
- **Overlap:** 50 characters
- **Break points:** Sentence boundaries (`.`, `\n`)
- **PPTX adjustment:** 300 characters (slide content is shorter)

## Sprint 1 Deliverables

- [x] Repository structure
- [x] Local Python environment
- [x] Configuration handling
- [x] Supported file types defined
- [x] Raw file intake workflow
- [x] Document parsers (PDF, PPTX, TXT)
- [x] Standardized parsed output schema
- [x] Metadata model
- [x] Chunking pipeline
- [x] Local persistence (JSON/JSONL)
- [x] Processing runner script
- [x] Logging system
- [x] Test dataset
- [x] End-to-end validation

## Next Steps (Sprint 2)

- Embedding generation
- Vector storage (ChromaDB)
- Retrieval implementation
- Basic RAG pipeline

## License

MIT
