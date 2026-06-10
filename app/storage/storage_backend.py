"""
Supabase Storage backend for processed content (roadmaps, summaries, daily plans, exams).

All paths are automatically namespaced under {user_id}/ inside the 'processed' bucket.
"""
import logging
from typing import Optional, List

from app.storage.supabase_client import get_client, get_user_id

logger = logging.getLogger(__name__)

BUCKET = "processed"


def _full_path(path: str) -> str:
    uid = get_user_id()
    return f"{uid}/{path.lstrip('/')}"


def read_text(path: str) -> Optional[str]:
    """Download a text file from Supabase Storage. Returns None if not found."""
    try:
        data = get_client().storage.from_(BUCKET).download(_full_path(path))
        return data.decode("utf-8") if data else None
    except Exception as exc:
        if "not found" in str(exc).lower() or "404" in str(exc):
            return None
        logger.warning("storage read_text failed for %s: %s", path, exc)
        return None


def write_text(path: str, content: str) -> None:
    """Upload a text file to Supabase Storage (overwriting any existing object).

    Under the user-scoped (RLS-enforcing) client there is no storage UPDATE
    policy — only insert/select/delete — so overwriting an existing object is
    done as delete-then-insert, which stays within the existing policies.
    """
    full = _full_path(path)
    data = content.encode("utf-8")
    store = get_client().storage.from_(BUCKET)
    try:
        store.upload(full, data, {"content-type": "text/plain; charset=utf-8", "x-upsert": "true"})
        return
    except Exception:
        pass
    # Overwrite path: drop the existing object (delete is allowed) then insert.
    try:
        store.remove([full])
    except Exception:
        pass
    try:
        store.upload(full, data, {"content-type": "text/plain; charset=utf-8"})
    except Exception as exc:
        logger.error("storage write_text failed for %s: %s", path, exc)
        raise


def delete(path: str) -> bool:
    """Delete a file from Supabase Storage. Returns True on success."""
    try:
        get_client().storage.from_(BUCKET).remove([_full_path(path)])
        return True
    except Exception as exc:
        logger.warning("storage delete failed for %s: %s", path, exc)
        return False


def list_prefix(prefix: str) -> List[str]:
    """List files under a prefix. Returns relative paths (without user_id/)."""
    uid = get_user_id()
    full_prefix = f"{uid}/{prefix.lstrip('/')}"
    # Supabase list() takes the folder path and returns file objects
    parts = full_prefix.rstrip("/").rsplit("/", 1)
    folder = parts[0] if len(parts) > 1 else ""
    try:
        items = get_client().storage.from_(BUCKET).list(folder)
        # items is a list of dicts with 'name' key
        results = []
        for item in (items or []):
            name = item.get("name", "")
            if name:
                full = f"{folder}/{name}" if folder else name
                # Strip user_id prefix
                rel = full[len(uid) + 1:] if full.startswith(uid + "/") else full
                results.append(rel)
        return results
    except Exception as exc:
        logger.warning("storage list_prefix failed for %s: %s", prefix, exc)
        return []


def exists(path: str) -> bool:
    return read_text(path) is not None
