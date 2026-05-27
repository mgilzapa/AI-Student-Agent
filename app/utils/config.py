import os
from dotenv import load_dotenv
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent.parent

def load_config():
    load_dotenv(BASE_DIR / ".env")
    return {
        "raw_path":       BASE_DIR / os.getenv("DATA_RAW_PATH", "data/raw"),
        "processed_path": BASE_DIR / os.getenv("DATA_PROCESSED_PATH", "data/processed"),
        "exports_path":   BASE_DIR / os.getenv("DATA_EXPORTS_PATH", "data/exports"),
        "log_level":      os.getenv("LOG_LEVEL", "INFO"),
        "log_file":       os.getenv("LOG_FILE", None),
        "openai_model":   os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        "embedding_model": os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        "top_k":          int(os.getenv("RAG_TOP_K", "5")),
        "vault_path":     Path(os.getenv("OBSIDIAN_VAULT")) if os.getenv("OBSIDIAN_VAULT") else None,
        # Supabase
        "supabase_url":         os.getenv("SUPABASE_URL", ""),
        "supabase_anon_key":    os.getenv("SUPABASE_ANON_KEY", ""),
        "supabase_service_key": os.getenv("SUPABASE_SERVICE_KEY", ""),
        "supabase_jwt_secret":  os.getenv("SUPABASE_JWT_SECRET", ""),
        "supabase_user_id":     os.getenv("SUPABASE_USER_ID", "00000000-0000-0000-0000-000000000001"),
    }
