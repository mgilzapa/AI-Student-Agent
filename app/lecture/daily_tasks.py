"""
Daily learning tasks — plan generation, persistence, task tracking.

Source of truth: data/processed/daily_tasks/<slug>/current_plan.md
Completion history: data/processed/daily_tasks/<slug>/task_history.json

Format:
  # Tagesplan: <Modul>
  **Generiert:** YYYY-MM-DD · **Lernzeit:** Xh
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
from typing import Any, Callable, Dict, List, Optional, Tuple

from anthropic import Anthropic

from . import module_profile as mp

DAILY_DIR = Path("data/processed/daily_tasks")
MODEL = "claude-haiku-4-5-20251001"

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
    r"^\*\*Generiert:\*\*\s+(?P<date>\S+)\s*·\s*\*\*Lernzeit:\*\*\s+(?P<hours>[\d.]+)h"
)
_PROGRESS_RE = re.compile(r"^\*\*Fortschritt:\*\*\s+(?P<done>\d+)/(?P<total>\d+)")
_TOPIC_RE = re.compile(r"^## (?P<name>.+?) <!-- topic_id:(?P<id>\S+) -->")
_TASK_RE = re.compile(r"^- \[(?P<check>[ xX])\] (?P<text>.+)")


def parse_plan(md: str) -> Dict[str, Any]:
    """Parse current_plan.md into structured dict."""
    result: Dict[str, Any] = {
        "generated": "",
        "daily_hours": 0.0,
        "progress": {"done": 0, "total": 0},
        "topics": [],
    }
    current_topic: Optional[Dict[str, Any]] = None

    for line in md.splitlines():
        h = _HEADER_RE.match(line)
        if h:
            result["generated"] = h.group("date")
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

def _render_md(module_name: str, daily_hours: float, topics: List[Dict]) -> str:
    """Render plan markdown from structured topic list."""
    today = date.today().isoformat()
    total = sum(len(t["tasks"]) for t in topics)
    done_count = sum(
        sum(1 for tk in t["tasks"] if tk["done"]) for t in topics
    )
    lines: List[str] = [
        f"# Tagesplan: {module_name}",
        f"**Generiert:** {today} · **Lernzeit:** {daily_hours}h",
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


_PRIO_WEIGHTS: Dict[str, int] = {
    "high": 3, "hoch": 3,
    "medium": 2, "mittel": 2,
    "low": 1, "niedrig": 1,
}


def _select_topics(roadmap_data: Dict[str, Any], daily_hours: float) -> List[Tuple[Dict, float]]:
    """
    Select topics and allocate study hours for today.

    - 1 doing topic  → full focus, all hours go to it.
    - N doing topics → distribute hours proportionally by priority weight.
    - 0 doing topics → pick the highest-priority todo topic from the first phase that has any.

    Returns list of (topic_dict, allocated_hours).
    """
    phases = roadmap_data.get("phases", [])

    doing = [
        topic
        for phase in phases
        for topic in (phase.get("topics") or [])
        if topic.get("status") == "doing"
    ]

    if not doing:
        for phase in phases:
            todo = [t for t in (phase.get("topics") or []) if t.get("status") == "todo"]
            if todo:
                best = min(todo, key=lambda t: _PRIO_ORDER.get(
                    (t.get("pruefungsrelevanz") or t.get("relevance") or "medium").lower(), 1
                ))
                return [(best, daily_hours)]
        return []

    if len(doing) == 1:
        return [(doing[0], daily_hours)]

    # Multiple doing topics: distribute hours by priority weight
    weights = [
        _PRIO_WEIGHTS.get(
            (t.get("pruefungsrelevanz") or t.get("relevance") or "medium").lower(), 2
        )
        for t in doing
    ]
    total_weight = sum(weights)
    result: List[Tuple[Dict, float]] = []
    remaining = daily_hours
    for i, (topic, w) in enumerate(zip(doing, weights)):
        if i == len(doing) - 1:
            alloc = max(0.5, round(remaining, 1))
        else:
            alloc = max(0.5, round(w / total_weight * daily_hours, 1))
            remaining -= alloc
        result.append((topic, alloc))
    return result


def _pick_review_topic(roadmap_data: Dict[str, Any]) -> Optional[Dict]:
    """Return a random done topic from the roadmap, or None if none exist."""
    done_topics = [
        topic
        for phase in (roadmap_data.get("phases") or [])
        for topic in (phase.get("topics") or [])
        if topic.get("status") == "done"
    ]
    return random.choice(done_topics) if done_topics else None


# ─────────────────────────── File classification ────────────────────────────

_EXERCISE_FILE_RE = re.compile(
    r"\b(blatt|übung|uebung|aufgabe|exercise|sheet|hw|hausaufgabe|tut|tutorial|assignment)\b",
    re.IGNORECASE,
)

_LECTURE_FILE_RE = re.compile(
    r"\b(week|woche|vorlesung|lecture|lect|lec|VL|skript|mitschrift|folien|folie|slides|handout|sitzung|einheit|chapter)\b"
    r"|\bV\d{2,}\b|\bVO\d{2,}\b|\bL\d{2,}\b|\bKap\d*\b|\bCh\d+\b|\b(WT|WS|SS|SM)\d{2,4}\b",
    re.IGNORECASE,
)

# Categories the user can assign to files in the module profile
_EXERCISE_CATEGORIES = {"übungsblatt", "klausur"}
_LECTURE_CATEGORIES = {"vorlesung"}


def _lookup_file_type(name: str, file_types: Dict[str, str]) -> Optional[str]:
    """Return the user-assigned category for a file, matching by basename."""
    if name in file_types:
        return file_types[name]
    name_lower = name.lower()
    for key, typ in file_types.items():
        if Path(key).name.lower() == name_lower:
            return typ
    return None


def _split_files(
    files: List[str],
    exercises: List[str],
    file_types: Optional[Dict[str, str]] = None,
) -> tuple[List[str], List[str]]:
    """Separate lecture/script files from exercise sheets.

    If `file_types` (from the module profile) is provided, explicit user markings take
    precedence: "übungsblatt"/"klausur" → exercises, "vorlesung" → lecture files.
    For files with no explicit marking the original regex heuristic is used as fallback.
    """
    lecture_files: List[str] = []
    promoted: List[str] = []
    for f in files:
        if file_types:
            category = _lookup_file_type(f, file_types)
            if category in _EXERCISE_CATEGORIES:
                promoted.append(f)
                continue
            if category in _LECTURE_CATEGORIES:
                lecture_files.append(f)
                continue
        if _LECTURE_FILE_RE.search(f):
            lecture_files.append(f)
        elif _EXERCISE_FILE_RE.search(f):
            promoted.append(f)
        else:
            lecture_files.append(f)
    merged_exercises = promoted + [e for e in exercises if e not in promoted]
    return lecture_files, merged_exercises


# ─────────────────────────── LLM task generation ────────────────────────────

_TASK_PROMPT = """\
Du bist ein universitärer Lerncoach. Erstelle {n} Lernaufgaben für heute.

THEMA: {topic_name}
SUBTOPICS: {subtopics}
LERNZEIT HEUTE: {hours}h
VERFÜGBARE DATEIEN: {files}
ÜBUNGSBLÄTTER: {exercises}
{rag_section}{already_done_section}
Jede Aufgabe ist ein kurzer Titel/Referenz — kein langer Satz, keine Aufgabenstellung.
Wenn möglich: Übungsblatt-Aufgaben priorisieren.

FORMAT (kurze Referenz, max. 6–8 Wörter):
✓ "Blatt3 – Aufgabe 1"
✓ "Blatt3 – Aufgaben 2–4"
✓ "VL5 – Normalformen (1NF/2NF/3NF)"
✓ "Skript Kap. 4 – Algorithmus X"
✓ "Wiederholung: Transaktionen"
✗ "Löse Aufgabe 3 aus Blatt3 vollständig und überprüfe jeden Schritt (~40 min)"
✗ "Erkläre den Unterschied zwischen X und Y in eigenen Worten"

Antworte NUR als JSON-Array mit genau {n} Strings.\
"""


def _task_count_for_hours(hours: float) -> int:
    """Return number of tasks to generate for the given study time."""
    if hours < 1.0:
        return 2
    elif hours < 2.0:
        return 3
    elif hours < 3.5:
        return 4
    else:
        return min(6, 4 + round(hours - 3.5))


def _build_tasks_from_metadata(
    topic: Dict[str, Any],
    hours: float = 2.0,
    completed_texts: Optional[List[str]] = None,
    file_types: Optional[Dict[str, str]] = None,
) -> List[Dict[str, Any]]:
    """Fallback: build tasks from topic metadata without LLM."""
    files, exercises = _split_files(
        topic.get("dateien") or topic.get("files") or [],
        topic.get("aufgaben") or topic.get("exercises") or [],
        file_types,
    )
    n = _task_count_for_hours(hours)
    done_set = set(completed_texts or [])
    name = topic.get("name", "Topic")
    subtopics = [str(s).strip() for s in (topic.get("subtopics") or topic.get("untergruppen") or []) if str(s).strip()]
    focus = f"Fokus: {', '.join(subtopics[:2])}" if subtopics else "Kernkonzepte erarbeiten"
    theory_min = round(hours * 0.5 * 60)
    practice_min = round(hours * 0.4 * 60)

    tasks: List[Dict[str, Any]] = []

    for ex in exercises:
        if len(tasks) >= n:
            break
        text = ex
        if text not in done_set:
            tasks.append({"text": text, "done": False})

    for f in files:
        if len(tasks) >= n:
            break
        label = f"{f} – {subtopics[0]}" if subtopics else f"{f} – {name}"
        if label not in done_set:
            tasks.append({"text": label, "done": False})

    if not tasks:
        tasks.append({"text": f"{name}" + (f" – {subtopics[0]}" if subtopics else ""), "done": False})

    return tasks[:n]


def _generate_tasks_for_topic(
    topic: Dict[str, Any],
    module_name: str,
    rag_fn: Callable,
    hours: float = 2.0,
    completed_texts: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Generate content-grounded tasks using RAG + topic metadata."""
    name = topic.get("name", "")
    subtopics = [str(s).strip() for s in (topic.get("subtopics") or topic.get("untergruppen") or []) if str(s).strip()]
    profile = mp.load(module_name)
    file_types: Dict[str, str] = (profile.get("file_types") or {}) if profile else {}
    files, exercises = _split_files(
        topic.get("dateien") or topic.get("files") or [],
        topic.get("aufgaben") or topic.get("exercises") or [],
        file_types,
    )

    # Query RAG: topic name + subtopics for richer, content-grounded tasks
    rag_query = name + ((" — " + ", ".join(subtopics[:4])) if subtopics else "")
    rag_content = rag_fn(rag_query, module_name, 6) if rag_fn else ""

    n = _task_count_for_hours(hours)
    done_list = completed_texts or []

    rag_section = ""
    if rag_content:
        rag_section = f"INHALT AUS DEM LERNMATERIAL:\n{rag_content[:3000]}\n\n"

    already_done_section = ""
    if done_list:
        already_done_section = (
            "BEREITS ERLEDIGT (nicht wiederholen):\n"
            + "\n".join(f"- {t}" for t in done_list)
            + "\n\n"
        )

    prompt = _TASK_PROMPT.format(
        n=n,
        topic_name=name,
        subtopics=", ".join(subtopics) if subtopics else "—",
        hours=hours,
        files=", ".join(files) if files else "—",
        exercises=", ".join(exercises) if exercises else "—",
        rag_section=rag_section,
        already_done_section=already_done_section,
    )

    try:
        resp = _get_client().messages.create(
            model=MODEL,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw).strip()
        task_texts = json.loads(raw)
        if not isinstance(task_texts, list):
            raise ValueError("Expected list")
        return [
            {"text": str(t).strip(), "done": False}
            for t in task_texts[:n]
            if str(t).strip()
        ]
    except Exception as exc:
        print(f"[daily_tasks] LLM task gen failed for {name}: {exc}")
        return _build_tasks_from_metadata(topic, hours, completed_texts, file_types)


# ─────────────────────────── Main generate ──────────────────────────────────

def generate(
    module_name: str,
    *,
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
        if t["id"].endswith("_review"):
            continue  # review tasks are optional — never carry over
        if t["id"] not in seen_carry:
            seen_carry.add(t["id"])
            carry_topics.append(t)

    # 2) Archive old plan
    archive_current_plan(module_name)

    # 3) Select fresh topics from roadmap (each with allocated hours)
    selected = _select_topics(roadmap_data, daily_hours)
    carry_ids = {t["id"] for t in carry_topics}

    # 4) Build new topic list (carryover first, then new)
    new_topics: List[Dict] = []
    for topic, alloc_hours in selected:
        tid = str(topic.get("id") or "")
        if tid in carry_ids:
            continue
        completed_texts = get_completed_texts_for_topic(module_name, tid)
        tasks = _generate_tasks_for_topic(topic, module_name, rag_fn, alloc_hours, completed_texts)
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
                            f"Wiederhole '{review.get('name', '')}': fasse die 3 wichtigsten Konzepte "
                            f"in eigenen Worten zusammen und beantworte 2 typische Prüfungsfragen dazu (~30 min)"
                        ),
                        "done": False,
                    }],
                })

    # 7) Save
    md = _render_md(module_name, daily_hours, all_topics)
    save_plan(module_name, md)
    return md
