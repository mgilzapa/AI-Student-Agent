"""
pgvector-backed vector store — drop-in replacement for ChromaVectorStore.

Stores embeddings in Supabase PostgreSQL (chunks table) and uses the
match_chunks RPC function for similarity search.
"""
import logging
from typing import Any, Dict, List, Optional

from app.storage.supabase_client import get_client, get_user_id

logger = logging.getLogger(__name__)

# In-memory cache: "{user_id}:{module_name_or_slug}" -> uuid
_module_id_cache: Dict[str, str] = {}


def _cache_key(uid: str, name: str) -> str:
    return f"{uid}:{name}"


def _resolve_module_id(module_name: str) -> Optional[str]:
    """Look up module UUID by name or slug. Auto-creates a minimal entry if missing."""
    if not module_name:
        return None

    uid = get_user_id()
    ck = _cache_key(uid, module_name)

    if ck in _module_id_cache:
        return _module_id_cache[ck]

    # Fetch all modules for this user and cache them
    try:
        rows = (
            get_client()
            .table("modules")
            .select("id, slug, name")
            .eq("user_id", uid)
            .execute()
        ).data or []
        for row in rows:
            _module_id_cache[_cache_key(uid, row["slug"])]       = row["id"]
            _module_id_cache[_cache_key(uid, row["name"])]       = row["id"]
            _module_id_cache[_cache_key(uid, row["name"].lower())] = row["id"]
    except Exception as exc:
        logger.warning("Failed to fetch modules: %s", exc)
        return None

    if ck in _module_id_cache:
        return _module_id_cache[ck]

    # Case-insensitive fallback
    lower_ck = _cache_key(uid, module_name.lower())
    if lower_ck in _module_id_cache:
        mid = _module_id_cache[lower_ck]
        _module_id_cache[ck] = mid
        return mid

    # Module not in DB — auto-create a minimal entry so chunks are not lost
    import re as _re
    slug = _re.sub(r"[äöüß]", lambda m: {"ä":"ae","ö":"oe","ü":"ue","ß":"ss"}[m.group()], module_name.lower())
    slug = _re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    try:
        result = get_client().table("modules").upsert({
            "user_id":    uid,
            "name":       module_name,
            "slug":       slug,
        }, on_conflict="user_id,slug").execute()
        new_rows = result.data or []
        if new_rows:
            mid = new_rows[0]["id"]
            _module_id_cache[ck]                                    = mid
            _module_id_cache[_cache_key(uid, slug)]                 = mid
            _module_id_cache[_cache_key(uid, module_name.lower())]  = mid
            logger.info("Auto-created module %r in Supabase (id=%s)", module_name, mid)
            return mid
    except Exception as exc:
        logger.warning("Auto-create module %r failed: %s", module_name, exc)

    return None


def _vec_str(embedding: List[float]) -> str:
    return "[" + ",".join(str(v) for v in embedding) + "]"


class PgVectorStore:
    """
    ChromaDB-compatible vector store backed by Supabase pgvector.

    Public interface mirrors ChromaVectorStore so it can be swapped without
    changing call sites.  `collection` property returns self so that code
    like `vector_store.collection.get()` continues to work.
    """

    def __init__(self) -> None:
        pass

    # ── collection shim (makes vector_store.collection.X work) ───────────────

    @property
    def collection(self):
        return self

    # ── Write ─────────────────────────────────────────────────────────────────

    def add(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        documents: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        if not ids:
            return
        if metadatas is None:
            metadatas = [{} for _ in ids]

        uid = get_user_id()
        rows = []
        for chunk_id, emb, doc, meta in zip(ids, embeddings, documents, metadatas):
            module_name = meta.get("module_name", "")
            module_id = _resolve_module_id(module_name)
            if not module_id:
                logger.warning("Skipping chunk %s — no module_id for %r", chunk_id, module_name)
                continue
            rows.append({
                "user_id":     uid,
                "module_id":   module_id,
                "chunk_text":  doc,
                "chunk_index": int(meta.get("chunk_index", 0)),
                "chunk_size":  len(doc),
                "embedding":   _vec_str(emb),
                "metadata":    {
                    "chunk_id":    chunk_id,
                    "document_id": meta.get("document_id", ""),
                    "source":      meta.get("source", ""),
                    "module_name": module_name,
                    "chunk_index": meta.get("chunk_index", ""),
                },
            })

        if not rows:
            return

        BATCH = 100
        for i in range(0, len(rows), BATCH):
            try:
                get_client().table("chunks").insert(rows[i:i + BATCH]).execute()
            except Exception as exc:
                logger.error("pgvector add batch failed: %s", exc)

        logger.info("Added %d chunks to pgvector", len(rows))

    # ── Search ────────────────────────────────────────────────────────────────

    def search(
        self,
        query_embedding: List[float],
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Returns {"ids", "distances", "documents", "metadatas"}.
        Distances are scaled to [0,2] to match ChromaDB cosine distance range.
        """
        if not query_embedding:
            return {"ids": [], "distances": [], "documents": [], "metadatas": []}

        uid = get_user_id()
        params: Dict[str, Any] = {
            "query_embedding": _vec_str(query_embedding),
            "match_threshold": 0.0,
            "match_count":     n_results,
            "filter_user_id":  uid,
        }

        if where:
            module_name = where.get("module_name")
            if module_name:
                mid = _resolve_module_id(module_name)
                if mid:
                    params["filter_module_id"] = mid

        try:
            rows = get_client().rpc("match_chunks", params).execute().data or []
        except Exception as exc:
            logger.error("match_chunks RPC failed: %s", exc)
            return {"ids": [], "distances": [], "documents": [], "metadatas": []}

        ids, distances, documents, metadatas = [], [], [], []
        for row in rows:
            meta = row.get("metadata") or {}
            ids.append(meta.get("chunk_id", str(row.get("id", ""))))
            sim = float(row.get("similarity", 0.0))
            distances.append(2.0 * (1.0 - sim))  # convert to ChromaDB distance range
            documents.append(row.get("chunk_text", ""))
            metadatas.append(meta)

        return {"ids": ids, "distances": distances, "documents": documents, "metadatas": metadatas}

    # ── Read (collection.get shim) ─────────────────────────────────────────────

    def get(self, where: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Return {"ids": [...], "metadatas": [...]} for the given filter."""
        uid = get_user_id()
        try:
            q = get_client().table("chunks").select("id, metadata").eq("user_id", uid)
            if where:
                module_name = where.get("module_name")
                if module_name:
                    mid = _resolve_module_id(module_name)
                    if mid:
                        q = q.eq("module_id", mid)
            rows = q.execute().data or []
        except Exception as exc:
            logger.error("pgvector get failed: %s", exc)
            return {"ids": [], "metadatas": []}

        ids, metadatas = [], []
        for row in rows:
            meta = row.get("metadata") or {}
            ids.append(meta.get("chunk_id", str(row["id"])))
            metadatas.append(meta)
        return {"ids": ids, "metadatas": metadatas}

    # ── Count ─────────────────────────────────────────────────────────────────

    def count(self) -> int:
        uid = get_user_id()
        try:
            result = (
                get_client()
                .table("chunks")
                .select("id", count="exact")
                .eq("user_id", uid)
                .execute()
            )
            return result.count or 0
        except Exception as exc:
            logger.error("pgvector count failed: %s", exc)
            return 0

    # ── Delete ────────────────────────────────────────────────────────────────

    def delete(
        self,
        ids: Optional[List[str]] = None,
        where: Optional[Dict[str, Any]] = None,
    ) -> None:
        uid = get_user_id()
        try:
            if ids:
                # ids are stored inside metadata->>'chunk_id'
                # Delete rows whose metadata->>'chunk_id' is in ids
                for chunk_id in ids:
                    (
                        get_client()
                        .table("chunks")
                        .delete()
                        .eq("user_id", uid)
                        .eq("metadata->>chunk_id", chunk_id)
                        .execute()
                    )
            elif where:
                module_name = where.get("module_name")
                if module_name:
                    mid = _resolve_module_id(module_name)
                    if mid:
                        (
                            get_client()
                            .table("chunks")
                            .delete()
                            .eq("user_id", uid)
                            .eq("module_id", mid)
                            .execute()
                        )
        except Exception as exc:
            logger.error("pgvector delete failed: %s", exc)

    def clear(self) -> None:
        uid = get_user_id()
        try:
            get_client().table("chunks").delete().eq("user_id", uid).execute()
        except Exception as exc:
            logger.error("pgvector clear failed: %s", exc)


_default_store: Optional[PgVectorStore] = None


def get_pgvector_store() -> PgVectorStore:
    global _default_store
    if _default_store is None:
        _default_store = PgVectorStore()
    return _default_store
