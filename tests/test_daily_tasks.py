import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from app.lecture import daily_tasks as dt


SAMPLE_MD = """\
# Tagesplan: Analysis
**Generiert:** 2026-05-12 · **Modus:** konkret · **Lernzeit:** 2.0h
**Fortschritt:** 1/3 erledigt

## Grenzwerte <!-- topic_id:t1 -->
- [x] Lies Skript Kap. 1
- [ ] Löse Aufgabe 3

## Stetigkeit <!-- topic_id:t2 -->
- [ ] Definiere Stetigkeit
"""


def test_parse_plan_header():
    p = dt.parse_plan(SAMPLE_MD)
    assert p["generated"] == "2026-05-12"
    assert p["mode"] == "konkret"
    assert p["daily_hours"] == 2.0


def test_parse_plan_progress():
    p = dt.parse_plan(SAMPLE_MD)
    assert p["progress"]["done"] == 1
    assert p["progress"]["total"] == 3


def test_parse_plan_topics():
    p = dt.parse_plan(SAMPLE_MD)
    assert len(p["topics"]) == 2
    t1 = p["topics"][0]
    assert t1["id"] == "t1"
    assert t1["name"] == "Grenzwerte"
    assert len(t1["tasks"]) == 2
    assert t1["tasks"][0]["done"] is True
    assert t1["tasks"][1]["done"] is False


def test_parse_plan_second_topic():
    p = dt.parse_plan(SAMPLE_MD)
    t2 = p["topics"][1]
    assert t2["id"] == "t2"
    assert len(t2["tasks"]) == 1
    assert t2["tasks"][0]["done"] is False


def test_has_open_tasks_for_topic(tmp_path, monkeypatch):
    monkeypatch.setattr(dt, "current_plan_path", lambda m: tmp_path / "current_plan.md")
    (tmp_path / "current_plan.md").write_text(SAMPLE_MD, encoding="utf-8")
    assert dt.has_open_tasks_for_topic("Analysis", "t1") == 1
    assert dt.has_open_tasks_for_topic("Analysis", "t2") == 1
    assert dt.has_open_tasks_for_topic("Analysis", "t99") == 0


def test_has_open_tasks_no_plan(tmp_path, monkeypatch):
    monkeypatch.setattr(dt, "current_plan_path", lambda m: tmp_path / "missing.md")
    assert dt.has_open_tasks_for_topic("Analysis", "t1") == 0


def test_toggle_task_marks_done(tmp_path, monkeypatch):
    cp = tmp_path / "current_plan.md"
    cp.write_text(SAMPLE_MD, encoding="utf-8")
    monkeypatch.setattr(dt, "load_plan", lambda m, slug=None: cp.read_text(encoding="utf-8"))
    monkeypatch.setattr(dt, "save_plan", lambda m, md, slug=None: cp.write_text(md, encoding="utf-8"))
    dt.toggle_task("Analysis", "t1", 1, True)
    updated = dt.parse_plan(cp.read_text(encoding="utf-8"))
    assert updated["topics"][0]["tasks"][1]["done"] is True


def test_toggle_task_marks_undone(tmp_path, monkeypatch):
    cp = tmp_path / "current_plan.md"
    cp.write_text(SAMPLE_MD, encoding="utf-8")
    monkeypatch.setattr(dt, "load_plan", lambda m, slug=None: cp.read_text(encoding="utf-8"))
    monkeypatch.setattr(dt, "save_plan", lambda m, md, slug=None: cp.write_text(md, encoding="utf-8"))
    dt.toggle_task("Analysis", "t1", 0, False)
    updated = dt.parse_plan(cp.read_text(encoding="utf-8"))
    assert updated["topics"][0]["tasks"][0]["done"] is False


def test_toggle_task_refreshes_progress(tmp_path, monkeypatch):
    cp = tmp_path / "current_plan.md"
    cp.write_text(SAMPLE_MD, encoding="utf-8")
    monkeypatch.setattr(dt, "load_plan", lambda m, slug=None: cp.read_text(encoding="utf-8"))
    monkeypatch.setattr(dt, "save_plan", lambda m, md, slug=None: cp.write_text(md, encoding="utf-8"))
    dt.toggle_task("Analysis", "t1", 1, True)
    updated = dt.parse_plan(cp.read_text(encoding="utf-8"))
    assert updated["progress"]["done"] == 2
    assert updated["progress"]["total"] == 3


def test_toggle_task_no_plan_raises(monkeypatch):
    monkeypatch.setattr(dt, "load_plan", lambda m, slug=None: None)
    with pytest.raises(ValueError, match="Kein aktiver Plan"):
        dt.toggle_task("Analysis", "t1", 0, True)


def test_archive_creates_dated_file(tmp_path, monkeypatch):
    cp = tmp_path / "current_plan.md"
    cp.write_text(SAMPLE_MD, encoding="utf-8")
    monkeypatch.setattr(dt, "plan_dir", lambda m: tmp_path)
    monkeypatch.setattr(dt, "current_plan_path", lambda m: cp)
    from datetime import date
    today = date.today().isoformat()
    archive_p = dt.archive_current_plan("Analysis")
    assert archive_p is not None
    assert (tmp_path / f"{today}.md").exists()


def test_archive_no_plan_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(dt, "current_plan_path", lambda m: tmp_path / "missing.md")
    assert dt.archive_current_plan("Analysis") is None


def test_render_md_format():
    topics = [
        {"id": "t1", "name": "Grenzwerte", "tasks": [
            {"text": "Task A", "done": True},
            {"text": "Task B", "done": False},
        ]},
    ]
    md = dt._render_md("Analysis", "konkret", 2.0, topics)
    assert "# Tagesplan: Analysis" in md
    assert "**Generiert:**" in md
    assert "**Modus:** konkret" in md
    assert "**Lernzeit:** 2.0h" in md
    assert "**Fortschritt:** 1/2 erledigt" in md
    assert "## Grenzwerte <!-- topic_id:t1 -->" in md
    assert "- [x] Task A" in md
    assert "- [ ] Task B" in md


def test_select_topics_priority_order():
    roadmap_data = {
        "phases": [
            {
                "topics": [
                    {"id": "t1", "name": "A", "pruefungsrelevanz": "low", "hours": 1.0, "status": "todo"},
                    {"id": "t2", "name": "B", "pruefungsrelevanz": "high", "hours": 1.0, "status": "todo"},
                    {"id": "t3", "name": "C", "pruefungsrelevanz": "medium", "hours": 1.0, "status": "todo"},
                ]
            }
        ]
    }
    selected = dt._select_topics(roadmap_data, 2.0)
    assert selected[0]["id"] == "t2"
    assert selected[1]["id"] == "t3"


def test_select_topics_skips_done():
    roadmap_data = {
        "phases": [
            {
                "topics": [
                    {"id": "t1", "name": "Done", "pruefungsrelevanz": "high", "hours": 1.0, "status": "done"},
                    {"id": "t2", "name": "Open", "pruefungsrelevanz": "medium", "hours": 1.0, "status": "todo"},
                ]
            }
        ]
    }
    selected = dt._select_topics(roadmap_data, 5.0)
    assert all(t["id"] != "t1" for t in selected)
    assert any(t["id"] == "t2" for t in selected)
