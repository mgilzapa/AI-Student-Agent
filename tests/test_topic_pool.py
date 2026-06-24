import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.lecture import daily_tasks as dt
from app.lecture import topic_pool as tp


# ─────────────────────────── Pool size formula ──────────────────────────────

def test_pool_size_example_from_spec():
    # 4h, 3 files, 4 subtopics → 4*1.5 + 3 + 4*0.5 = 11
    assert dt._pool_size(4, 3, 4) == 11


def test_pool_size_minimum_is_four():
    assert dt._pool_size(0, 0, 0) == 4


def test_pool_size_maximum_is_sixteen():
    # 20*1.5 + 10 + 10*0.5 = 45 → capped at 16
    assert dt._pool_size(20, 10, 10) == 16


def test_pool_size_exercises_raise_count():
    # Exercises weigh 1.5 each: 2h, 0 files, 0 subtopics, 3 exercises
    # → 2*1.5 + 0 + 0 + 3*1.5 = 7.5 → 8 (vs. 3 without exercises)
    assert dt._pool_size(2, 0, 0, 3) == 8


# ─────────────────────────── Fixtures ───────────────────────────────────────

SAMPLE_POOL = {
    "topic_id": "t3",
    "topic_name": "Normalformen",
    "generated_at": "2026-06-04",
    "pool_size": 4,
    "tasks": [
        {"text": "Task A", "done": True},
        {"text": "Task B", "done": False},
        {"text": "Task C", "done": False},
        {"text": "Task D", "done": False},
    ],
}


def _patch_load(monkeypatch, pool):
    monkeypatch.setattr(tp, "load_pool", lambda m, tid, slug=None: pool)


# ─────────────────────────── get_next_tasks ─────────────────────────────────

def test_get_next_tasks_skips_done_and_returns_n(monkeypatch):
    _patch_load(monkeypatch, copy.deepcopy(SAMPLE_POOL))
    tasks = tp.get_next_tasks("M", "t3", 2)
    assert [t["text"] for t in tasks] == ["Task B", "Task C"]
    assert all(t["done"] is False for t in tasks)


def test_get_next_tasks_caps_at_available_open(monkeypatch):
    _patch_load(monkeypatch, copy.deepcopy(SAMPLE_POOL))
    tasks = tp.get_next_tasks("M", "t3", 10)
    assert len(tasks) == 3


def test_get_next_tasks_no_pool_returns_empty(monkeypatch):
    _patch_load(monkeypatch, None)
    assert tp.get_next_tasks("M", "t3", 3) == []


# ─────────────────────────── mark_task_done ─────────────────────────────────

def test_mark_task_done_sets_flag_and_saves(monkeypatch):
    pool = copy.deepcopy(SAMPLE_POOL)
    saved = {}
    monkeypatch.setattr(tp, "load_pool", lambda m, tid, slug=None: pool)
    monkeypatch.setattr(tp, "save_pool", lambda m, tid, p, slug=None: saved.update({"pool": p}))
    tp.mark_task_done("M", "t3", "Task B")
    assert pool["tasks"][1]["done"] is True
    assert saved["pool"]["tasks"][1]["done"] is True


def test_mark_task_done_unknown_text_does_not_save(monkeypatch):
    pool = copy.deepcopy(SAMPLE_POOL)
    calls = []
    monkeypatch.setattr(tp, "load_pool", lambda m, tid, slug=None: pool)
    monkeypatch.setattr(tp, "save_pool", lambda m, tid, p, slug=None: calls.append(p))
    tp.mark_task_done("M", "t3", "Does not exist")
    assert calls == []


def test_unmark_task_clears_flag_and_saves(monkeypatch):
    pool = {"tasks": [{"text": "A", "done": True}]}
    saved = {}
    monkeypatch.setattr(tp, "load_pool", lambda m, tid, slug=None: pool)
    monkeypatch.setattr(tp, "save_pool", lambda m, tid, p, slug=None: saved.update({"pool": p}))
    tp.unmark_task("M", "t3", "A")
    assert pool["tasks"][0]["done"] is False
    assert saved["pool"]["tasks"][0]["done"] is False


# ─────────────────────────── is_pool_complete ───────────────────────────────

def test_is_pool_complete_false_when_open_tasks(monkeypatch):
    _patch_load(monkeypatch, copy.deepcopy(SAMPLE_POOL))
    assert tp.is_pool_complete("M", "t3") is False


def test_is_pool_complete_true_when_all_done(monkeypatch):
    _patch_load(monkeypatch, {"tasks": [{"text": "A", "done": True}, {"text": "B", "done": True}]})
    assert tp.is_pool_complete("M", "t3") is True


def test_is_pool_complete_false_when_no_pool(monkeypatch):
    _patch_load(monkeypatch, None)
    assert tp.is_pool_complete("M", "t3") is False


def test_is_pool_complete_false_when_empty_tasks(monkeypatch):
    _patch_load(monkeypatch, {"tasks": []})
    assert tp.is_pool_complete("M", "t3") is False


# ─────────────────────────── pool_progress ──────────────────────────────────

def test_pool_progress_counts_done_and_total(monkeypatch):
    _patch_load(monkeypatch, copy.deepcopy(SAMPLE_POOL))
    assert tp.pool_progress("M", "t3") == {"done": 1, "total": 4}


def test_pool_progress_no_pool(monkeypatch):
    _patch_load(monkeypatch, None)
    assert tp.pool_progress("M", "t3") == {"done": 0, "total": 0}
