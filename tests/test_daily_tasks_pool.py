"""Pool-aware behavior added to daily_tasks (generate + toggle_task)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.lecture import daily_tasks as dt
from app.lecture import topic_pool as tp


SAMPLE_MD = """\
# Tagesplan: Analysis
**Generiert:** 2026-06-04 · **Lernzeit:** 2.0h
**Fortschritt:** 0/2 erledigt

## Grenzwerte <!-- topic_id:t1 -->
- [ ] Task B
- [ ] Task C
"""


def _wire(monkeypatch, md_box, slug_calls=None):
    # toggle_task resolves the slug once and threads it through; mock _slug_for so
    # the test never touches Supabase, and (optionally) count the resolutions.
    def _slug(m):
        if slug_calls is not None:
            slug_calls.append(m)
        return "analysis"
    monkeypatch.setattr(dt, "_slug_for", _slug)
    monkeypatch.setattr(dt, "load_plan", lambda m, slug=None: md_box["md"])
    monkeypatch.setattr(dt, "save_plan", lambda m, md, slug=None: md_box.update({"md": md}))
    monkeypatch.setattr(dt, "record_completed_task", lambda *a, **k: None)
    monkeypatch.setattr(dt, "remove_completed_task", lambda *a, **k: None)


def test_toggle_task_returns_md_and_marks_pool(monkeypatch):
    md_box = {"md": SAMPLE_MD}
    _wire(monkeypatch, md_box)
    marked = []
    monkeypatch.setattr(tp, "mark_task_done", lambda m, tid, txt, slug=None: marked.append((tid, txt)))
    monkeypatch.setattr(tp, "is_pool_complete", lambda m, tid, slug=None: False)

    result = dt.toggle_task("Analysis", "t1", 0, True)

    assert "- [x] Task B" in result["md"]
    assert result["card_completed"] is False
    assert marked == [("t1", "Task B")]


def test_toggle_task_card_completed_when_pool_done(monkeypatch):
    md_box = {"md": SAMPLE_MD}
    _wire(monkeypatch, md_box)
    monkeypatch.setattr(tp, "mark_task_done", lambda m, tid, txt, slug=None: None)
    monkeypatch.setattr(tp, "is_pool_complete", lambda m, tid, slug=None: True)

    result = dt.toggle_task("Analysis", "t1", 1, True)

    assert result["card_completed"] is True
    assert result["topic_id"] == "t1"
    assert result["topic_name"] == "Grenzwerte"


def test_toggle_task_uncheck_unmarks_pool_and_not_completed(monkeypatch):
    md_box = {"md": SAMPLE_MD.replace("- [ ] Task B", "- [x] Task B")}
    _wire(monkeypatch, md_box)
    unmarked = []
    monkeypatch.setattr(tp, "unmark_task", lambda m, tid, txt, slug=None: unmarked.append((tid, txt)))
    # Even if the pool reports complete, un-checking must not signal completion.
    monkeypatch.setattr(tp, "is_pool_complete", lambda m, tid, slug=None: True)

    result = dt.toggle_task("Analysis", "t1", 0, False)

    assert result["card_completed"] is False
    assert unmarked == [("t1", "Task B")]


def test_toggle_task_resolves_slug_once(monkeypatch):
    """A single toggle must resolve the slug exactly once — every storage helper
    receives the cached slug instead of re-querying the module profile."""
    md_box = {"md": SAMPLE_MD}
    slug_calls = []
    _wire(monkeypatch, md_box, slug_calls)
    monkeypatch.setattr(tp, "mark_task_done", lambda m, tid, txt, slug=None: None)
    monkeypatch.setattr(tp, "is_pool_complete", lambda m, tid, slug=None: False)

    dt.toggle_task("Analysis", "t1", 0, True)

    assert slug_calls == ["Analysis"]


def test_generate_uses_existing_pool(monkeypatch):
    """When a pool exists, generate draws from it instead of calling the LLM."""
    roadmap_data = {
        "phases": [{
            "topics": [
                {"id": "t1", "name": "Grenzwerte", "status": "doing",
                 "relevance": "high", "hours": 2},
            ]
        }]
    }
    saved = {}
    monkeypatch.setattr(dt, "load_plan", lambda m: None)
    monkeypatch.setattr(dt, "save_plan", lambda m, md: saved.update({"md": md}))
    monkeypatch.setattr(dt, "archive_current_plan", lambda m: None)
    monkeypatch.setattr(dt, "get_completed_texts_for_topic", lambda m, tid: [])

    # Pool already exists → load_pool returns it, _generate_pool must NOT be called.
    pool = {"tasks": [{"text": "Pool task 1", "done": False},
                      {"text": "Pool task 2", "done": False}]}
    monkeypatch.setattr(tp, "load_pool", lambda m, tid: pool)

    def _boom(*a, **k):
        raise AssertionError("_generate_pool should not be called when a pool exists")
    monkeypatch.setattr(dt, "_generate_pool", _boom)

    md = dt.generate("Analysis", daily_hours=2.0, roadmap_data=roadmap_data, rag_fn=None)

    assert "Pool task 1" in md


def test_generate_creates_pool_when_missing(monkeypatch):
    """When no pool exists, generate creates it once, then draws tasks."""
    roadmap_data = {
        "phases": [{
            "topics": [
                {"id": "t1", "name": "Grenzwerte", "status": "doing",
                 "relevance": "high", "hours": 2},
            ]
        }]
    }
    saved = {}
    monkeypatch.setattr(dt, "load_plan", lambda m: None)
    monkeypatch.setattr(dt, "save_plan", lambda m, md: saved.update({"md": md}))
    monkeypatch.setattr(dt, "archive_current_plan", lambda m: None)
    monkeypatch.setattr(dt, "get_completed_texts_for_topic", lambda m, tid: [])

    state = {"pool": None, "gen_calls": 0}

    def _load(m, tid):
        return state["pool"]

    def _gen(topic, module_name, rag_fn):
        state["gen_calls"] += 1
        state["pool"] = {"tasks": [{"text": "Fresh task", "done": False}]}

    monkeypatch.setattr(tp, "load_pool", _load)
    monkeypatch.setattr(dt, "_generate_pool", _gen)

    md = dt.generate("Analysis", daily_hours=2.0, roadmap_data=roadmap_data, rag_fn=None)

    assert state["gen_calls"] == 1
    assert "Fresh task" in md
