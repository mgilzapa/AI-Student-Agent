"""
Daily learning tasks — plan generation, persistence, task tracking.

Source of truth: data/processed/daily_tasks/<slug>/current_plan.md
Completion history: data/processed/daily_tasks/<slug>/task_history.json

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
import random
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


def task_history_path(module_name: str) -> Path:
    return plan_dir(module_name) / "task_history.json"


def load_plan(module_name: str) -> Optional[str]:
    p = current_plan_path(module_name)
    return p.read_text(encoding="utf-8") if p.exists() else None


def save_plan(module_name: str, md: str) -> Path:
    p = current_plan_path(module_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(md, encoding="utf-8")
    return p


# ─────────────────────────── Task history ───────────────────────────────────

def load_task_history(module_name: str) -> List[Dict[str, Any]]:
    """Load completed-task history. Each entry: {topic_id, topic_name, task_text, completed_date}."""
    p = task_history_path(module_name)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_task_history(module_name: str, history: List[Dict[str, Any]]) -> None:
    p = task_history_path(module_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def record_completed_task(
    module_name: str, topic_id: str, topic_name: str, task_text: str
) -> None:
    """Append a completed task to history (idempotent — skips if already recorded today)."""
    history = load_task_history(module_name)
    today = date.today().isoformat()
    already = any(
        e["topic_id"] == topic_id and e["task_text"] == task_text
        for e in history
    )
    if not already:
        history.append({
            "topic_id": topic_id,
            "topic_name": topic_name,
            "task_text": task_text,
            "completed_date": today,
        })
        _save_task_history(module_name, history)


def remove_completed_task(module_name: str, topic_id: str, task_text: str) -> None:
    """Remove a task from history (used when user un-checks a task)."""
    history = load_task_history(module_name)
    history = [
        e for e in history
        if not (e["topic_id"] == topic_id and e["task_text"] == task_text)
    ]
    _save_task_history(module_name, history)


def get_completed_texts_for_topic(module_name: str, topic_id: str) -> List[str]:
    """Return list of task texts already completed for a given topic (across all days)."""
    return [
        e["task_text"]
        for e in load_task_history(module_name)
        if e["topic_id"] == topic_id
    ]


def get_stats(module_name: str) -> Dict[str, Any]:
    """Return completion stats: total, per-topic counts, and per-day counts."""
    history = load_task_history(module_name)

    per_topic: Dict[str, Dict[str, Any]] = {}
    per_day: Dict[str, int] = {}

    for entry in history:
        tid = entry["topic_id"]
        tname = entry.get("topic_name", tid)
        completed_date = entry.get("completed_date", "")

        if tid not in per_topic:
            per_topic[tid] = {"topic_id": tid, "topic_name": tname, "count": 0}
        per_topic[tid]["count"] += 1

        if completed_date:
            per_day[completed_date] = per_day.get(completed_date, 0) + 1

    return {
        "total_completed": len(history),
        "per_topic": sorted(per_topic.values(), key=lambda x: x["count"], reverse=True),
        "per_day": dict(sorted(per_day.items(), reverse=True)),
    }


def get_review_tasks(module_name: str, count: int = 3) -> List[Dict[str, Any]]:
    """Return the `count` most recently completed tasks from history."""
    history = load_task_history(module_name)
    if not history:
        return []
    return list(reversed(history[-count:]))


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
    current_topic_name = ""

    for i, line in enumerate(lines):
        tm = _TOPIC_RE.match(line)
        if tm:
            in_topic = (tm.group("id") == topic_id)
            current_topic_name = tm.group("name").strip() if in_topic else ""
            task_count = 0
            continue
        if in_topic:
            tk = _TASK_RE.match(line)
            if tk:
                if task_count == task_index:
                    mark = "x" if done else " "
                    lines[i] = f"- [{mark}] {tk.group('text')}"
                    task_text = tk.group("text").strip()
                    if done:
                        record_completed_task(module_name, topic_id, current_topic_name, task_text)
                    else:
                        remove_completed_task(module_name, topic_id, task_text)
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
    """Select exactly one topic for the day: first 'doing' card, else first 'todo' card."""
    phases = roadmap_data.get("phases", [])

    # 1) First doing card (in phase order)
    for phase in phases:
        for topic in (phase.get("topics") or []):
            if topic.get("status") == "doing":
                return [topic]

    # 2) First todo card (priority-sorted within each phase)
    for phase in phases:
        todo_topics = [t for t in (phase.get("topics") or []) if t.get("status") == "todo"]
        sorted_topics = sorted(
            todo_topics,
            key=lambda t: _PRIO_ORDER.get(
                (t.get("pruefungsrelevanz") or t.get("relevance") or "medium").lower(), 1
            ),
        )
        if sorted_topics:
            return [sorted_topics[0]]

    return []


def _pick_review_topic(roadmap_data: Dict[str, Any]) -> Optional[Dict]:
    """Return a random done topic from the roadmap, or None if none exist."""
    done_topics = [
        topic
        for phase in (roadmap_data.get("phases") or [])
        for topic in (phase.get("topics") or [])
        if topic.get("status") == "done"
    ]
    return random.choice(done_topics) if done_topics else None


# ─────────────────────────── LLM task generation ────────────────────────────

_CONCRETE_PROMPT = """\
Du bist ein universitärer Lerncoach.
Erstelle Lernaufgaben für Topic "{topic_name}" im Kurs "{module}".

ZEITBUDGET HEUTE: {daily_hours}h (das ist die verfügbare Zeit für heute — nicht die Gesamt-Kartenzeit)
VERFÜGBARE DATEIEN: {files}
AUFGABEN / ÜBUNGSBLÄTTER: {exercises}
{already_done_section}
Teile die {daily_hours}h auf in:
- [Theorie] ca. {theory_hours}h: Lesen/Verstehen aus den Dateien → {theory_count} Task(s)
- [Praxis] ca. {practice_hours}h: Aufgaben aus den Übungsblättern → {practice_count} Task(s)

REGELN (strikt):
- Jede Aufgabe referenziert genau eine Datei ODER ein Übungsblatt aus den obigen Listen.
- Jeder Task beginnt mit [Theorie] oder [Praxis].
- Erfinde KEINE eigenen Aufgaben oder Inhalte.
- Ein Task = ein kurzer Satz, max. eine Zeile.
- Wenn keine Dateien vorhanden: nur [Praxis]-Tasks. Wenn keine Aufgaben vorhanden: nur [Theorie]-Tasks.
- Erstelle KEINE Tasks, die in BEREITS ERLEDIGT aufgelistet sind.

Antworte NUR als JSON-Array von Strings:
["[Theorie] Lies: ...", "[Praxis] Bearbeite: ..."]"""


def _task_counts(daily_hours: float) -> tuple[int, int]:
    """Return (theory_count, practice_count) based on available study time."""
    if daily_hours < 2:
        return 1, 1
    elif daily_hours < 4:
        return 2, 1
    else:
        return 2, 2


def _build_tasks_from_metadata(
    topic: Dict[str, Any],
    daily_hours: float = 2.0,
    completed_texts: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Build tasks directly from topic files/exercises without LLM."""
    files = topic.get("dateien") or topic.get("files") or []
    exercises = topic.get("aufgaben") or topic.get("exercises") or []
    theory_count, practice_count = _task_counts(daily_hours)
    done_set = set(completed_texts or [])

    tasks: List[Dict[str, Any]] = []
    added_theory = 0
    for f in files:
        if added_theory >= theory_count:
            break
        text = f"[Theorie] Lies: {f}"
        if text not in done_set:
            tasks.append({"text": text, "done": False})
            added_theory += 1

    added_practice = 0
    for ex in exercises:
        if added_practice >= practice_count:
            break
        text = f"[Praxis] Bearbeite: {ex}"
        if text not in done_set:
            tasks.append({"text": text, "done": False})
            added_practice += 1

    if not tasks:
        tasks = [{"text": topic.get("name", "Topic"), "done": False}]
    return tasks


def _generate_tasks_for_topic(
    topic: Dict[str, Any],
    module_name: str,
    rag_fn: Callable,
    daily_hours: float = 2.0,
    completed_texts: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Generate concrete tasks referencing uploaded files/exercises, scoped to daily_hours."""
    files = topic.get("dateien") or topic.get("files") or []
    exercises = topic.get("aufgaben") or topic.get("exercises") or []

    if not files and not exercises:
        return [{"text": topic.get("name", "Topic"), "done": False}]

    theory_count, practice_count = _task_counts(daily_hours)
    max_tasks = theory_count + practice_count
    theory_hours = round(daily_hours * 0.6, 1)
    practice_hours = round(daily_hours * 0.4, 1)

    # Only expose files/exercises that fit within today's budget to the LLM
    files_for_prompt = files[:theory_count] if files else []
    exercises_for_prompt = exercises[:practice_count] if exercises else []

    done_list = completed_texts or []
    if done_list:
        already_done_section = (
            "BEREITS ERLEDIGT (diese Tasks NICHT wiederholen):\n"
            + "\n".join(f"- {t}" for t in done_list)
            + "\n"
        )
    else:
        already_done_section = ""

    try:
        prompt = _CONCRETE_PROMPT.format(
            topic_name=topic.get("name", ""),
            module=module_name,
            daily_hours=daily_hours,
            theory_hours=theory_hours,
            practice_hours=practice_hours,
            theory_count=theory_count,
            practice_count=practice_count,
            files=", ".join(files_for_prompt) if files_for_prompt else "—",
            exercises=", ".join(exercises_for_prompt) if exercises_for_prompt else "—",
            already_done_section=already_done_section,
        )
        resp = _get_client().messages.create(
            model=MODEL,
            max_tokens=400,
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
            for txt in task_texts[:max_tasks]
            if str(txt).strip()
        ]
    except Exception as exc:
        print(f"[daily_tasks] LLM task gen failed for {topic.get('name')}: {exc}")
        return _build_tasks_from_metadata(topic, daily_hours, completed_texts)


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
    # 1) Carryover from existing plan (before archiving) — deduplicated by topic ID
    old_md = load_plan(module_name)
    raw_carry = _extract_carryover(old_md) if old_md else []
    seen_carry: set = set()
    carry_topics: List[Dict] = []
    for t in raw_carry:
        if t["id"] not in seen_carry:
            seen_carry.add(t["id"])
            carry_topics.append(t)

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
        completed_texts = get_completed_texts_for_topic(module_name, tid)
        if mode == "grob":
            tasks = _build_tasks_from_metadata(topic, daily_hours, completed_texts)
        else:
            tasks = _generate_tasks_for_topic(topic, module_name, rag_fn, daily_hours, completed_texts)
        new_topics.append({
            "id": tid,
            "name": str(topic.get("name", "")),
            "tasks": tasks,
        })

    # 5) Combine
    all_topics = carry_topics + new_topics
    all_ids = {t["id"] for t in all_topics}

    # 6) Optionally append review task for a random done card (only if >= 3h study time)
    if daily_hours >= 3:
        review = _pick_review_topic(roadmap_data)
        if review:
            review_id = f"{review.get('id', 'r')}_review"
            if review_id not in all_ids:  # never duplicate a review
                all_topics.append({
                    "id": review_id,
                    "name": f"Wiederholung: {review.get('name', '')}",
                    "tasks": [{
                        "text": (
                            f"[30 min] Wiederhole {review.get('name', '')} — "
                            f"gehe die wichtigsten Konzepte nochmal durch"
                        ),
                        "done": False,
                    }],
                })

    # 7) Save
    md = _render_md(module_name, mode, daily_hours, all_topics)
    save_plan(module_name, md)
    return md
