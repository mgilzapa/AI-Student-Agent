"""Tests for pool extension on new uploads
(design: 2026-06-22-pool-erweiterung-uploads)."""
import contextvars
import json
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.lecture import daily_tasks as dt
from app.lecture import roadmap as rm
from app.lecture import topic_pool as tp


def _fake_client(content):
    msg = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(message=msg)
    resp = types.SimpleNamespace(choices=[choice])
    create = lambda **kw: resp
    completions = types.SimpleNamespace(create=create)
    chat = types.SimpleNamespace(completions=completions)
    return types.SimpleNamespace(chat=chat)


# ─────────────────────────────── extend_pool ────────────────────────────────

def test_extend_pool_appends_and_dedups(monkeypatch):
    existing = {
        "tasks": [
            {"text": "Alte Aufgabe 1", "done": True, "minutes": 30},
            {"text": "Alte Aufgabe 2", "done": False, "minutes": 45},
        ],
        "pool_size": 2,
        "generated_at": "2026-06-01",
    }
    saved = {}
    monkeypatch.setattr(tp, "load_pool", lambda m, tid, slug=None: existing)
    monkeypatch.setattr(tp, "save_pool", lambda m, tid, pool, slug=None: saved.update(pool))
    monkeypatch.setattr(dt.mp, "load", lambda m: None)
    # one genuinely new task + one exact duplicate of an existing (done) task
    content = (
        '[{"text":"Neue Aufgabe aus Blatt5","minutes":40},'
        '{"text":"Alte Aufgabe 1","minutes":30}]'
    )
    monkeypatch.setattr(dt, "_get_client", lambda: _fake_client(content))

    added = dt.extend_pool(
        {"id": "t1", "name": "Topic"}, "Mod", ["Blatt5.pdf"], rag_fn=None, rag_content=""
    )

    assert added == 1
    assert len(saved["tasks"]) == 3
    # existing tasks preserved unchanged (incl. done status & order)
    assert saved["tasks"][0] == {"text": "Alte Aufgabe 1", "done": True, "minutes": 30}
    assert saved["tasks"][1]["done"] is False
    # new task appended open
    assert saved["tasks"][-1]["text"] == "Neue Aufgabe aus Blatt5"
    assert saved["tasks"][-1]["done"] is False
    assert saved["pool_size"] == 3
    assert saved["extended_at"]
    assert saved["generated_at"] == "2026-06-01"  # untouched


def test_extend_pool_respects_max_new(monkeypatch):
    saved = {}
    monkeypatch.setattr(tp, "load_pool", lambda m, tid, slug=None: {"tasks": []})
    monkeypatch.setattr(tp, "save_pool", lambda m, tid, pool, slug=None: saved.update(pool))
    monkeypatch.setattr(dt.mp, "load", lambda m: None)
    texts = [
        "Bearbeite Blatt drei komplett", "Loese die Integrationsaufgabe",
        "Wiederhole Grenzwerte sorgfaeltig", "Erstelle Mindmap Topologie",
        "Beweise den Satz von Stokes", "Rechne Beispiel sieben durch",
        "Fasse Kapitel neun zusammen", "Analysiere Fallstudie Markt",
        "Implementiere Quicksort sauber", "Vergleiche Normalformen genau",
        "Skizziere ER Diagramm Shop", "Pruefe Aussagenlogik Tautologie",
    ]
    content = json.dumps([{"text": t, "minutes": 20} for t in texts])
    monkeypatch.setattr(dt, "_get_client", lambda: _fake_client(content))

    added = dt.extend_pool(
        {"id": "t1", "name": "T"}, "Mod", ["x.pdf"], rag_fn=None, max_new=8, rag_content=""
    )

    assert added == 8
    assert len(saved["tasks"]) == 8


def test_extend_pool_no_pool_falls_back_to_generate(monkeypatch):
    monkeypatch.setattr(tp, "load_pool", lambda m, tid, slug=None: None)
    calls = []
    monkeypatch.setattr(dt, "_generate_pool", lambda topic, m, rag: calls.append(topic["id"]))

    dt.extend_pool({"id": "t1", "name": "T"}, "Mod", ["f.pdf"], rag_fn=None)

    assert calls == ["t1"]


def test_extend_pool_empty_llm_makes_no_change(monkeypatch):
    saved = {}
    monkeypatch.setattr(tp, "load_pool", lambda m, tid, slug=None: {"tasks": [{"text": "A", "done": False}]})
    monkeypatch.setattr(tp, "save_pool", lambda *a, **k: saved.update({"saved": True}))
    monkeypatch.setattr(dt.mp, "load", lambda m: None)
    monkeypatch.setattr(dt, "_get_client", lambda: _fake_client("Keine neuen Aufgaben noetig."))

    added = dt.extend_pool({"id": "t1", "name": "T"}, "Mod", ["f.pdf"], rag_fn=None, rag_content="")

    assert added == 0
    assert saved == {}  # no padding, no save


# ─────────────────────────────── topic matching ──────────────────────────────

def test_topic_matching_affected_when_file_in_rag_verified():
    topic = {"id": "t1", "name": "DB", "subtopics": ["Normalformen"]}
    rag_fn = lambda q, m, k: "[Blatt5.pdf]\nInhalt zu Normalformen"
    matched, content = dt._topic_affected_by_new_files(topic, "Mod", ["Blatt5.pdf"], rag_fn)
    assert "Blatt5.pdf" in matched
    assert content


def test_topic_matching_not_affected():
    rag_fn = lambda q, m, k: "[Skript1.pdf]\nUnrelated"
    matched, _ = dt._topic_affected_by_new_files({"id": "t1", "name": "X"}, "Mod", ["Blatt5.pdf"], rag_fn)
    assert not matched


def test_topic_matching_fallback_name_containment():
    rag_fn = lambda q, m, k: ""  # no RAG hits at all
    topic = {"id": "t1", "name": "X", "dateien": ["Blatt5.pdf"]}
    matched, _ = dt._topic_affected_by_new_files(topic, "Mod", ["Blatt5.pdf"], rag_fn)
    assert "Blatt5.pdf" in matched


# ──────────────────────── extend_pools_for_new_files ──────────────────────────

def _roadmap(ids):
    return {"phases": [{"title": "P", "topics": [{"id": i, "name": i.upper()} for i in ids]}]}


def test_extend_pools_only_processes_affected_topics(monkeypatch):
    roadmap = _roadmap(["t1", "t2", "t3"])

    def _match(topic, m, files, rag):
        return (["f.pdf"], "content") if topic["id"] in ("t1", "t3") else ([], "content")

    monkeypatch.setattr(dt, "_topic_affected_by_new_files", _match)
    extended = []
    monkeypatch.setattr(
        dt, "extend_pool",
        lambda topic, m, files, rag=None, **k: extended.append(topic["id"]) or 2,
    )
    monkeypatch.setattr(dt, "_sync_roadmap_assignments", lambda *a, **k: None)
    events = []

    res = dt.extend_pools_for_new_files(
        "Mod", roadmap, ["f.pdf"], rag_fn=None,
        progress_cb=lambda d, t, n, a: events.append((d, t, n, a)),
    )

    assert sorted(extended) == ["t1", "t3"]
    assert res == {"T1": 2, "T3": 2}
    assert len(events) == 2
    assert all(total == 2 for _, total, _, _ in events)
    assert sorted(d for d, _, _, _ in events) == [1, 2]


def test_extend_pools_partial_failure_is_nonfatal(monkeypatch):
    roadmap = _roadmap(["t1", "t2"])
    monkeypatch.setattr(dt, "_topic_affected_by_new_files", lambda topic, m, f, rag: (["f.pdf"], "c"))
    monkeypatch.setattr(dt, "_sync_roadmap_assignments", lambda *a, **k: None)

    def _ext(topic, m, files, rag=None, **k):
        if topic["id"] == "t1":
            raise RuntimeError("boom")
        return 3

    monkeypatch.setattr(dt, "extend_pool", _ext)

    res = dt.extend_pools_for_new_files("Mod", roadmap, ["f.pdf"], rag_fn=None)

    assert res == {"T1": 0, "T2": 3}


_CTX_PROBE: "contextvars.ContextVar[str]" = contextvars.ContextVar("ext_probe", default="MISSING")


def test_extend_pools_propagates_request_context(monkeypatch):
    roadmap = _roadmap(["t1", "t2"])
    seen = []
    monkeypatch.setattr(
        dt, "_topic_affected_by_new_files",
        lambda topic, m, f, rag: (["f.pdf"], "c"),
    )
    monkeypatch.setattr(dt, "_sync_roadmap_assignments", lambda *a, **k: None)

    def _ext(topic, m, files, rag=None, **k):
        seen.append(_CTX_PROBE.get())
        return 1

    monkeypatch.setattr(dt, "extend_pool", _ext)

    _CTX_PROBE.set("request-user-token")
    dt.extend_pools_for_new_files("Mod", roadmap, ["f.pdf"], rag_fn=None, concurrency=2)

    assert seen == ["request-user-token"] * 2


def test_extend_pools_updates_roadmap_assignments(monkeypatch):
    roadmap = _roadmap(["t1"])
    monkeypatch.setattr(dt, "_topic_affected_by_new_files", lambda topic, m, f, rag: (["Blatt5.pdf"], "c"))
    monkeypatch.setattr(dt, "extend_pool", lambda *a, **k: 1)
    synced = {}
    monkeypatch.setattr(
        dt, "_sync_roadmap_assignments",
        lambda m, topics, files: synced.update({"topics": [t["id"] for t in topics], "files": files}),
    )

    dt.extend_pools_for_new_files("Mod", roadmap, ["Blatt5.pdf"], rag_fn=None)

    assert synced["topics"] == ["t1"]
    assert synced["files"] == ["Blatt5.pdf"]


def test_extend_pools_noop_without_new_files(monkeypatch):
    roadmap = _roadmap(["t1"])
    monkeypatch.setattr(dt, "_topic_affected_by_new_files", lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not match")))
    res = dt.extend_pools_for_new_files("Mod", roadmap, [], rag_fn=None)
    assert res == {}


# ──────────────────────── roadmap.add_files_to_topic ──────────────────────────

ROADMAP_MD = """\
# Lernplan: Mod
**Generiert:** 2026-06-01 · **Prüfungsdatum:** —
**Fortschritt:** 0/1 fertig · 0 dran · 1 offen

## Phase 1

### Thema A <!-- id:t1 status:todo prio:high h:2 -->
**Bedeutung:** etwas
**Dateien:** alt.pdf
"""


def test_add_files_to_topic_extends_existing_dateien():
    md, changed = rm.add_files_to_topic(ROADMAP_MD, "t1", ["neu.pdf"], [])
    assert changed
    assert "alt.pdf, neu.pdf" in md


def test_add_files_to_topic_inserts_aufgaben_when_missing():
    md, changed = rm.add_files_to_topic(ROADMAP_MD, "t1", [], ["Blatt5.pdf"])
    assert changed
    assert "**Aufgaben:** Blatt5.pdf" in md


def test_add_files_to_topic_noop_when_already_present():
    md, changed = rm.add_files_to_topic(ROADMAP_MD, "t1", ["alt.pdf"], [])
    assert not changed
    assert md == ROADMAP_MD


def test_add_files_to_topic_unknown_id_noop():
    md, changed = rm.add_files_to_topic(ROADMAP_MD, "t999", ["x.pdf"], [])
    assert not changed


# ─────────────────────────────── API endpoint ────────────────────────────────

def _drain(resp):
    """Collect a StreamingResponse body (Starlette wraps sync gens as async)."""
    import asyncio

    async def _collect():
        out = []
        async for chunk in resp.body_iterator:
            out.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk))
        return "".join(out)

    return asyncio.run(_collect())


def test_extend_pools_stream_noop_without_roadmap(monkeypatch):
    import app.api as api
    monkeypatch.setattr(api, "sanitize_module_name", lambda m: m)
    monkeypatch.setattr(api.rm, "load_roadmap_md", lambda m: None)

    resp = api.extend_pools_stream("Mod", api.ExtendPoolsRequest(files=["f.pdf"]))
    blob = _drain(resp)

    assert '"type": "done"' in blob
    assert '"topics_extended": 0' in blob
    assert '"tasks_added": 0' in blob


def test_extend_pools_stream_emits_progress_and_done(monkeypatch):
    import app.api as api
    monkeypatch.setattr(api, "sanitize_module_name", lambda m: m)
    monkeypatch.setattr(api.rm, "load_roadmap_md", lambda m: "# md")
    monkeypatch.setattr(api.rm, "parse_md", lambda md: {"phases": []})
    monkeypatch.setattr(api, "_pool_rag_fn", lambda m: None)

    def _fake(module, roadmap, files, rag_fn=None, progress_cb=None):
        if progress_cb:
            progress_cb(1, 1, "Thema A", 3)
        return {"Thema A": 3}

    monkeypatch.setattr(api.dt, "extend_pools_for_new_files", _fake)

    resp = api.extend_pools_stream("Mod", api.ExtendPoolsRequest(files=["f.pdf"]))
    blob = _drain(resp)

    assert '"type": "progress"' in blob
    assert '"type": "done"' in blob
    assert '"tasks_added": 3' in blob
    assert '"topics_extended": 1' in blob
