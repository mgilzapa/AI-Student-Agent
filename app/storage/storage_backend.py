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


# ── Account-wide helpers (DSGVO export & deletion) ─────────────────────────────
# Both buckets namespace every object under ``{user_id}/`` so a full per-user
# walk/purge only has to recurse beneath that single prefix.

ALL_BUCKETS = ("raw-files", "processed")


def _walk_bucket(store, prefix: str) -> List[str]:
    """Recursively list every object path under ``prefix`` in a storage bucket.

    Supabase's ``list()`` is non-recursive and returns both files and folder
    placeholders. File objects carry an ``id``/``metadata``; folder placeholders
    don't — that's how we tell them apart and recurse only into folders.
    """
    found: List[str] = []
    try:
        items = store.list(prefix)
    except Exception as exc:
        logger.warning("storage list failed for %s: %s", prefix, exc)
        return found
    for item in (items or []):
        name = item.get("name")
        if not name:
            continue
        full = f"{prefix}/{name}" if prefix else name
        is_file = bool(item.get("id")) or bool(item.get("metadata"))
        if is_file:
            found.append(full)
        else:
            found.extend(_walk_bucket(store, full))
    return found


def list_all_user_objects() -> dict:
    """Map each bucket to the list of the current user's object paths (full paths,
    i.e. including the ``{uid}/`` prefix). Used by the data export."""
    client = get_client()
    uid = get_user_id()
    out: dict = {}
    for bucket in ALL_BUCKETS:
        out[bucket] = _walk_bucket(client.storage.from_(bucket), uid)
    return out


def download_object(bucket: str, full_path: str) -> Optional[bytes]:
    """Download a single object's bytes from a bucket (full, uid-prefixed path)."""
    try:
        return get_client().storage.from_(bucket).download(full_path)
    except Exception as exc:
        logger.warning("storage download failed for %s/%s: %s", bucket, full_path, exc)
        return None


def purge_user_storage() -> int:
    """Delete EVERY storage object owned by the current user across all buckets.

    Returns the number of objects removed. Runs under the user-scoped (RLS)
    client, so it can only ever touch the caller's own ``{uid}/`` tree. Used by
    account deletion (DSGVO Art. 17).
    """
    client = get_client()
    removed = 0
    for bucket in ALL_BUCKETS:
        store = client.storage.from_(bucket)
        paths = _walk_bucket(store, get_user_id())
        for i in range(0, len(paths), 100):  # Supabase caps remove() batch size
            batch = paths[i:i + 100]
            try:
                store.remove(batch)
                removed += len(batch)
            except Exception as exc:
                logger.warning("purge_user_storage: remove failed in %s: %s", bucket, exc)
    return removed
