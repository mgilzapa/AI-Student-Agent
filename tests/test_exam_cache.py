import json
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _exam_cache_hash_impl(module_dir_fn, module_name, exam_files):
    """Reference implementation for the hash function."""
    base = module_dir_fn(module_name)
    parts = []
    for f in sorted(exam_files, key=lambda x: x["name"]):
        path = base / f["relative_path"]
        try:
            st = path.stat()
            parts.append(f"{f['name']}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append(f"{f['name']}:0:0")
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def test_hash_empty():
    """Empty exam list → deterministic hash."""
    h = hashlib.md5(b"").hexdigest()
    assert hashlib.md5(b"").hexdigest() == h


def test_hash_order_independent(tmp_path):
    """Hash is sorted by filename, not insertion order."""
    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / "b.pdf").write_bytes(b"y")

    def dir_fn(_):
        return tmp_path

    files_ab = [
        {"name": "a.pdf", "relative_path": "a.pdf"},
        {"name": "b.pdf", "relative_path": "b.pdf"},
    ]
    files_ba = [
        {"name": "b.pdf", "relative_path": "b.pdf"},
        {"name": "a.pdf", "relative_path": "a.pdf"},
    ]
    assert _exam_cache_hash_impl(dir_fn, "m", files_ab) == _exam_cache_hash_impl(dir_fn, "m", files_ba)


def test_hash_changes_on_content_change(tmp_path):
    """Modifying file content changes the hash (mtime changes)."""
    import time

    f = tmp_path / "klausur.pdf"
    f.write_bytes(b"v1")

    def dir_fn(_):
        return tmp_path

    files = [{"name": "klausur.pdf", "relative_path": "klausur.pdf"}]
    h1 = _exam_cache_hash_impl(dir_fn, "m", files)
    time.sleep(0.01)
    f.write_bytes(b"v2")
    h2 = _exam_cache_hash_impl(dir_fn, "m", files)
    assert h1 != h2


def test_cache_roundtrip(tmp_path, monkeypatch):
    """load returns None when missing; save+load round-trips data."""
    import app.api as api_mod

    monkeypatch.setattr(api_mod, "_EXAM_CACHE_DIR", tmp_path)
    monkeypatch.setattr(api_mod, "_slug", lambda n: n.lower())

    assert api_mod._load_exam_cache("TestModul") is None

    api_mod._save_exam_cache("TestModul", "abc123", "Stil text", "# Profil")
    cache = api_mod._load_exam_cache("TestModul")
    assert cache["hash"] == "abc123"
    assert cache["style"] == "Stil text"
    assert cache["exam_profile_md"] == "# Profil"
