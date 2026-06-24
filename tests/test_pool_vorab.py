"""Tests for the pool pre-generation feature, Gemini pool parsing, and the
read-only topic-pool endpoint (design: 2026-06-20-pool-vorab-und-card-tasks)."""
import contextvars
import sys
import threading
import time
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.lecture import daily_tasks as dt
from app.lecture import topic_pool as tp


def _roadmap(n_topics):
    """Build a roadmap spreading n_topics across two phases."""
    topics = [
        {"id": f"t{i}", "name": f"Topic {i}", "status": "todo", "hours": 2}
        for i in range(n_topics)
    ]
    half = n_topics // 2
    return {"phases": [
        {"title": "P1", "topics": topics[:half]},
        {"title": "P2", "topics": topics[half:]},
    ]}


# ─────────────────────────── generate_all_pools ──────────────────────────────

def test_generate_all_pools_one_per_topic(monkeypatch):
    roadmap = _roadmap(5)
    seen = []
    monkeypatch.setattr(dt, "_generate_pool", lambda topic, m, rag: seen.append(topic["id"]))

    count = dt.generate_all_pools("Mod", roadmap, rag_fn=None)

    assert count == 5
    assert sorted(seen) == ["t0", "t1", "t2", "t3", "t4"]


def test_generate_all_pools_calls_progress_cb_per_topic(monkeypatch):
    roadmap = _roadmap(4)
    monkeypatch.setattr(dt, "_generate_pool", lambda topic, m, rag: None)
    events = []

    def cb(done, total, name):
        events.append((done, total, name))

    dt.generate_all_pools("Mod", roadmap, rag_fn=None, progress_cb=cb)

    assert len(events) == 4
    assert all(total == 4 for _, total, _ in events)
    # done counter is monotonic and ends at total
    assert sorted(d for d, _, _ in events) == [1, 2, 3, 4]


def test_generate_all_pools_respects_concurrency(monkeypatch):
    roadmap = _roadmap(6)
    active = {"now": 0, "max": 0}
    lock = threading.Lock()

    def _slow(topic, m, rag):
        with lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.05)
        with lock:
            active["now"] -= 1

    monkeypatch.setattr(dt, "_generate_pool", _slow)

    dt.generate_all_pools("Mod", roadmap, rag_fn=None, concurrency=2)

    assert active["max"] <= 2


def test_generate_all_pools_partial_failure_is_nonfatal(monkeypatch):
    roadmap = _roadmap(3)
    done = []

    def _maybe_boom(topic, m, rag):
        if topic["id"] == "t1":
            raise RuntimeError("kaputt")
        done.append(topic["id"])

    monkeypatch.setattr(dt, "_generate_pool", _maybe_boom)

    count = dt.generate_all_pools("Mod", roadmap, rag_fn=None)

    # The failing topic does not abort the batch; the others still run.
    assert sorted(done) == ["t0", "t2"]
    assert count == 2


_CTX_PROBE: "contextvars.ContextVar[str]" = contextvars.ContextVar("ctx_probe", default="MISSING")


def test_generate_all_pools_propagates_request_context(monkeypatch):
    """Pool generation runs in worker threads; the per-request Supabase auth
    context (a ContextVar) must be replayed into them or storage RLS fails closed."""
    roadmap = _roadmap(4)
    seen = []

    def _read_ctx(topic, m, rag):
        seen.append(_CTX_PROBE.get())

    monkeypatch.setattr(dt, "_generate_pool", _read_ctx)

    _CTX_PROBE.set("request-user-token")
    dt.generate_all_pools("Mod", roadmap, rag_fn=None, concurrency=3)

    assert seen == ["request-user-token"] * 4


# ─────────────────────────── Gemini pool parsing ─────────────────────────────

def _fake_client(content):
    msg = types.SimpleNamespace(content=content)
    choice = types.SimpleNamespace(message=msg)
    resp = types.SimpleNamespace(choices=[choice])
    create = lambda **kw: resp
    completions = types.SimpleNamespace(create=create)
    chat = types.SimpleNamespace(completions=completions)
    return types.SimpleNamespace(chat=chat)


def _wire_pool_gen(monkeypatch, content):
    monkeypatch.setattr(dt, "_get_client", lambda: _fake_client(content))
    monkeypatch.setattr(dt.mp, "load", lambda m: None)
    saved = {}
    monkeypatch.setattr(tp, "save_pool", lambda m, tid, pool, slug=None: saved.update(pool))
    return saved


def test_generate_pool_parses_gemini_array_with_code_fences(monkeypatch):
    content = (
        "```json\n"
        '[{"text": "Lies Kapitel 1", "minutes": 20}, '
        '{"text": "Fasse Kapitel 1 zusammen", "minutes": 25}]\n'
        "```"
    )
    saved = _wire_pool_gen(monkeypatch, content)

    dt._generate_pool({"id": "t1", "name": "Intro", "hours": 2}, "Mod", rag_fn=None)

    texts = [t["text"] for t in saved["tasks"]]
    assert "Lies Kapitel 1" in texts
    assert "Fasse Kapitel 1 zusammen" in texts


def test_generate_pool_parses_array_with_surrounding_text(monkeypatch):
    content = (
        "Hier ist dein Aufgaben-Pool:\n"
        '[{"text": "Erklaere Normalformen", "minutes": 30}]\n'
        "Viel Erfolg!"
    )
    saved = _wire_pool_gen(monkeypatch, content)

    dt._generate_pool({"id": "t2", "name": "DB", "hours": 2}, "Mod", rag_fn=None)

    texts = [t["text"] for t in saved["tasks"]]
    assert texts == ["Erklaere Normalformen"]


# ─────────────────────────── pool read endpoint ──────────────────────────────

def test_daily_pool_get_returns_tasks_and_progress(monkeypatch):
    import app.api as api
    pool = {"tasks": [
        {"text": "A", "done": True, "minutes": 30},
        {"text": "B", "done": False, "minutes": 45},
    ]}
    monkeypatch.setattr(api.tp, "load_pool", lambda m, tid: pool)

    out = api.daily_pool_get("Mod", "t1")

    assert out["exists"] is True
    assert out["progress"] == {"done": 1, "total": 2}
    assert {tk["text"] for tk in out["tasks"]} == {"A", "B"}


def test_daily_pool_get_empty_when_no_pool(monkeypatch):
    import app.api as api
    monkeypatch.setattr(api.tp, "load_pool", lambda m, tid: None)

    out = api.daily_pool_get("Mod", "t1")

    assert out == {"exists": False}
