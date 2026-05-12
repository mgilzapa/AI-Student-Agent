"""
Daily learning tasks — plan generation, persistence, task tracking.

Source of truth: data/processed/daily_tasks/<slug>/current_plan.md

Format:
  # Tagesplan: <Modul>
  **Generiert:** YYYY-MM-DD · **Modus:** konkret|grob · **Lernzeit:** Xh
  **Fortschritt:** X/Y erledigt

  ## <Topic Name> <!-- topic_id:tX -->
  - [x] Task text
  - [ ] Task text
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from anthropic import Anthropic

from . import module_profile as mp

DAILY_DIR = Path("data/processed/daily_tasks")
MODEL = "claude-sonnet-4-6"

_client: Optional[Anthropic] = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


# ─────────────────────────── Path helpers ───────────────────────────────────

def plan_dir(module_name: str) -> Path:
    profile = mp.load(module_name)
    slug = profile["slug"] if profile else mp._slugify(module_name)
    return DAILY_DIR / slug


def current_plan_path(module_name: str) -> Path:
    return plan_dir(module_name) / "current_plan.md"


def load_plan(module_name: str) -> Optional[str]:
    p = current_plan_path(module_name)
    return p.read_text(encoding="utf-8") if p.exists() else None


def save_plan(module_name: str, md: str) -> Path:
    p = current_plan_path(module_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(md, encoding="utf-8")
    return p


# ─────────────────────────── Parsing ────────────────────────────────────────

_HEADER_RE = re.compile(
    r"^\*\*Generiert:\*\*\s+(?P<date>\S+)\s*·\s*\*\*Modus:\*\*\s+(?P<mode>\S+)"
    r"\s*·\s*\*\*Lernzeit:\*\*\s+(?P<hours>[\d.]+)h"
)
_PROGRESS_RE = re.compile(r"^\*\*Fortschritt:\*\*\s+(?P<done>\d+)/(?P<total>\d+)")
_TOPIC_RE = re.compile(r"^## (?P<name>.+?) <!-- topic_id:(?P<id>\S+) -->")
_TASK_RE = re.compile(r"^- \[(?P<check>[ xX])\] (?P<text>.+)")


def parse_plan(md: str) -> Dict[str, Any]:
    """Parse current_plan.md into structured dict."""
    result: Dict[str, Any] = {
        "generated": "",
        "mode": "",
        "daily_hours": 0.0,
        "progress": {"done": 0, "total": 0},
        "topics": [],
    }
    current_topic: Optional[Dict[str, Any]] = None

    for line in md.splitlines():
        h = _HEADER_RE.match(line)
        if h:
            result["generated"] = h.group("date")
            result["mode"] = h.group("mode")
            try:
                result["daily_hours"] = float(h.group("hours"))
            except ValueError:
                pass
            continue

        pg = _PROGRESS_RE.match(line)
        if pg:
            result["progress"]["done"] = int(pg.group("done"))
            result["progress"]["total"] = int(pg.group("total"))
            continue

        tm = _TOPIC_RE.match(line)
        if tm:
            current_topic = {
                "id": tm.group("id"),
                "name": tm.group("name").strip(),
                "tasks": [],
            }
            result["topics"].append(current_topic)
            continue

        if current_topic is not None:
            tk = _TASK_RE.match(line)
            if tk:
                current_topic["tasks"].append({
                    "text": tk.group("text").strip(),
                    "done": tk.group("check").lower() == "x",
                })

    return result


def has_open_tasks_for_topic(module_name: str, topic_id: str) -> int:
    """Return count of open tasks for a topic in the current plan (0 if no plan)."""
    md = load_plan(module_name)
    if not md:
        return 0
    parsed = parse_plan(md)
    for t in parsed["topics"]:
        if t["id"] == topic_id:
            return sum(1 for task in t["tasks"] if not task["done"])
    return 0


# ─────────────────────────── Rendering ──────────────────────────────────────

def _render_md(module_name: str, mode: str, daily_hours: float, topics: List[Dict]) -> str:
    """Render plan markdown from structured topic list."""
    today = date.today().isoformat()
    total = sum(len(t["tasks"]) for t in topics)
    done_count = sum(
        sum(1 for tk in t["tasks"] if tk["done"]) for t in topics
    )
    lines: List[str] = [
        f"# Tagesplan: {module_name}",
        f"**Generiert:** {today} · **Modus:** {mode} · **Lernzeit:** {daily_hours}h",
        f"**Fortschritt:** {done_count}/{total} erledigt",
        "",
    ]
    for topic in topics:
        lines.append(f"## {topic['name']} <!-- topic_id:{topic['id']} -->")
        for task in topic["tasks"]:
            mark = "x" if task["done"] else " "
            lines.append(f"- [{mark}] {task['text']}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _refresh_progress(md: str) -> str:
    """Recompute the Fortschritt line from actual task states."""
    parsed = parse_plan(md)
    total = sum(len(t["tasks"]) for t in parsed["topics"])
    done_count = sum(
        sum(1 for tk in t["tasks"] if tk["done"]) for t in parsed["topics"]
    )
    new_line = f"**Fortschritt:** {done_count}/{total} erledigt"
    return re.sub(
        r"^\*\*Fortschritt:\*\*.*$", new_line, md, count=1, flags=re.MULTILINE
    )


# ─────────────────────────── Task toggle ────────────────────────────────────

def toggle_task(module_name: str, topic_id: str, task_index: int, done: bool) -> str:
    """Toggle a single task checkbox. Returns updated markdown. Raises ValueError if no plan."""
    md = load_plan(module_name)
    if not md:
        raise ValueError("Kein aktiver Plan vorhanden.")

    lines = md.splitlines()
    in_topic = False
    task_count = 0

    for i, line in enumerate(lines):
        tm = _TOPIC_RE.match(line)
        if tm:
            in_topic = (tm.group("id") == topic_id)
            task_count = 0
            continue
        if in_topic:
            tk = _TASK_RE.match(line)
            if tk:
                if task_count == task_index:
                    mark = "x" if done else " "
                    lines[i] = f"- [{mark}] {tk.group('text')}"
                    break
                task_count += 1

    updated = "\n".join(lines)
    if not updated.endswith("\n"):
        updated += "\n"
    updated = _refresh_progress(updated)
    save_plan(module_name, updated)
    return updated


# ─────────────────────────── Archive ────────────────────────────────────────

def archive_current_plan(module_name: str) -> Optional[Path]:
    """Copy current_plan.md to YYYY-MM-DD.md. Returns archive path or None."""
    src = current_plan_path(module_name)
    if not src.exists():
        return None
    today = date.today().isoformat()
    archive_path = plan_dir(module_name) / f"{today}.md"
    archive_path.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return archive_path


# ─────────────────────────── Carryover ──────────────────────────────────────

def _extract_carryover(old_md: str) -> List[Dict]:
    """Return topic dicts (id, name, tasks) containing only open tasks."""
    parsed = parse_plan(old_md)
    carry: List[Dict] = []
    for topic in parsed["topics"]:
        open_tasks = [tk for tk in topic["tasks"] if not tk["done"]]
        if open_tasks:
            carry.append({"id": topic["id"], "name": topic["name"], "tasks": open_tasks})
    return carry


# ─────────────────────────── Topic selection ────────────────────────────────

_PRIO_ORDER: Dict[str, int] = {
    "high": 0, "hoch": 0,
    "medium": 1, "mittel": 1,
    "low": 2, "niedrig": 2,
}


def _select_topics(roadmap_data: Dict[str, Any], daily_hours: float) -> List[Dict]:
    """Select roadmap topics for the day: phase order, priority-sorted, up to daily_hours."""
    selected: List[Dict] = []
    hours_filled = 0.0

    for phase in roadmap_data.get("phases", []):
        non_done = [
            t for t in (phase.get("topics") or [])
            if t.get("status") != "done"
        ]
        sorted_topics = sorted(
            non_done,
            key=lambda t: _PRIO_ORDER.get(
                (t.get("pruefungsrelevanz") or t.get("relevance") or "medium").lower(), 1
            ),
        )
        for topic in sorted_topics:
            if hours_filled >= daily_hours and selected:
                break
            try:
                topic_hours = float(topic.get("hours") or 1.0)
            except (TypeError, ValueError):
                topic_hours = 1.0
            selected.append(topic)
            hours_filled += topic_hours

    return selected


# ─────────────────────────── LLM task generation ────────────────────────────

_CONCRETE_PROMPT = """\
Du bist ein erfahrener universitärer Lerncoach.
Erstelle konkrete, umsetzbare Lernaufgaben für das Topic "{topic_name}" im Kurs "{module}".

ZEITBUDGET: {hours}h
SUBTOPICS: {subtopics}
DATEIEN: {files}
AUFGABEN: {exercises}

RAG-KONTEXT (aus Kursmaterialien):
{rag_context}

Generiere 3–6 konkrete Aufgaben, die zusammen ca. {hours}h füllen.
Jede Aufgabe beginnt mit einem Verb (z.B. "Lies", "Löse", "Definiere", "Berechne").
Sei spezifisch — keine vagen Aufgaben wie "Thema lernen".

Antworte NUR als JSON-Array von Strings (Aufgabentexte), keine Erläuterungen:
["Aufgabe 1 Text", "Aufgabe 2 Text"]"""


def _generate_tasks_for_topic(
    topic: Dict[str, Any], module_name: str, rag_fn: Callable
) -> List[Dict[str, Any]]:
    """Call LLM to generate concrete tasks for a topic. Falls back to a single stub task."""
    try:
        rag_context = rag_fn(
            f"Erkläre die wichtigsten Konzepte und Lernziele zu {topic.get('name', '')}",
            module_name,
            top_k=8,
        )
        prompt = _CONCRETE_PROMPT.format(
            topic_name=topic.get("name", ""),
            module=module_name,
            hours=topic.get("hours", 1.0),
            subtopics=", ".join(topic.get("subtopics") or []),
            files=", ".join(topic.get("dateien") or topic.get("files") or []),
            exercises=", ".join(topic.get("aufgaben") or topic.get("exercises") or []),
            rag_context=(rag_context or "")[:2000],
        )
        resp = _get_client().messages.create(
            model=MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw).strip()
        task_texts = json.loads(raw)
        if not isinstance(task_texts, list):
            raise ValueError("Expected list")
        return [
            {"text": str(txt).strip(), "done": False}
            for txt in task_texts
            if str(txt).strip()
        ]
    except Exception as exc:
        print(f"[daily_tasks] LLM task gen failed for {topic.get('name')}: {exc}")
        return [{"text": f"{topic.get('name', 'Topic')} erarbeiten", "done": False}]


# ─────────────────────────── Main generate ──────────────────────────────────

def generate(
    module_name: str,
    *,
    mode: str,
    daily_hours: float,
    roadmap_data: Dict[str, Any],
    rag_fn: Callable,
) -> str:
    """Generate a new daily plan, archive old one, apply carryover. Returns new plan md."""
    # 1) Carryover from existing plan (before archiving)
    old_md = load_plan(module_name)
    carry_topics = _extract_carryover(old_md) if old_md else []

    # 2) Archive old plan
    archive_current_plan(module_name)

    # 3) Select fresh topics from roadmap
    selected = _select_topics(roadmap_data, daily_hours)
    carry_ids = {t["id"] for t in carry_topics}

    # 4) Build new topic list (carryover first, then new)
    new_topics: List[Dict] = []
    for topic in selected:
        tid = str(topic.get("id") or "")
        if tid in carry_ids:
            continue
        if mode == "grob":
            tasks = [{"text": str(topic.get("name", "Topic")), "done": False}]
        else:
            tasks = _generate_tasks_for_topic(topic, module_name, rag_fn)
        new_topics.append({
            "id": tid,
            "name": str(topic.get("name", "")),
            "tasks": tasks,
        })

    # 5) Combine and save
    all_topics = carry_topics + new_topics
    md = _render_md(module_name, mode, daily_hours, all_topics)
    save_plan(module_name, md)
    return md
