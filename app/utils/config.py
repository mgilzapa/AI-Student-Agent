import os
from dotenv import load_dotenv
from pathlib import Path

# Basisverzeichnis des Projekts (wo .env liegt)
BASE_DIR = Path(__file__).parent.parent.parent

def load_config():
    load_dotenv(BASE_DIR / ".env")
    chroma_path_val = os.getenv("CHROMA_PATH")
    return {
        "raw_path": BASE_DIR / os.getenv("DATA_RAW_PATH", "data/raw"),
        "processed_path": BASE_DIR / os.getenv("DATA_PROCESSED_PATH", "data/processed"),
        "exports_path": BASE_DIR / os.getenv("DATA_EXPORTS_PATH", "data/exports"),
        "chroma_path": BASE_DIR / chroma_path_val if chroma_path_val else BASE_DIR / "data/chroma",
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "log_file": os.getenv("LOG_FILE", None),
        "openai_model": os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "embedding_model": os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        "top_k": int(os.getenv("RAG_TOP_K", "5")),
        "vault_path": Path(os.getenv("OBSIDIAN_VAULT")) if os.getenv("OBSIDIAN_VAULT") else None,
    }
