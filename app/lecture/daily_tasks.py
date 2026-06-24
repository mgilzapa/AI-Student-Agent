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

import contextvars
import json
import os
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import openai

from . import module_profile as mp
from ..llm_clients import make_gemini_client

# Pool generation runs on Gemini 2.5 Flash Lite via the OpenAI-compatible endpoint
# (see app/llm_clients.py). Roadmap/exam/worksheet generation are unaffected.
MODEL = os.getenv("POOL_MODEL", "gemini-2.5-flash-lite")

_client: Optional[openai.OpenAI] = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = make_gemini_client()
    return _client


# ─────────────────────────── Storage helpers ─────────────────────────────────

def _slug_for(module_name: str) -> str:
    profile = mp.load(module_name)
    return profile["slug"] if profile else mp._slugify(module_name)


def task_history_path(module_name: str):
    """Returns the storage path string (used externally to check/delete history)."""
    return _slug_for(module_name) + "/task_history.json"


def load_plan(module_name: str, slug: Optional[str] = None) -> Optional[str]:
    from app.storage import storage_backend as sb
    slug = slug or _slug_for(module_name)
    return sb.read_text(f"{slug}/daily_plan.md")


def save_plan(module_name: str, md: str, slug: Optional[str] = None) -> str:
    from app.storage import storage_backend as sb
    slug = slug or _slug_for(module_name)
    path = f"{slug}/daily_plan.md"
    sb.write_text(path, md)
    return path


# ─────────────────────────── Task history ───────────────────────────────────

def load_task_history(module_name: str, slug: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load completed-task history."""
    from app.storage import storage_backend as sb
    slug = slug or _slug_for(module_name)
    raw = sb.read_text(f"{slug}/task_history.json")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []


def _save_task_history(module_name: str, history: List[Dict[str, Any]], slug: Optional[str] = None) -> None:
    from app.storage import storage_backend as sb
    slug = slug or _slug_for(module_name)
    sb.write_text(f"{slug}/task_history.json", json.dumps(history, ensure_ascii=False, indent=2))


def load_dashboard_bundle(module_name: str, slug: Optional[str] = None):
    """Load everything the dashboard needs for one module in a single pass.

    Resolves the slug ONCE (load_plan + load_task_history would otherwise each
    call _slug_for → mp.load again), then reads the plan and history. Returns
    ``(parsed_plan_or_None, history_list)``. Designed to be run per module in a
    worker thread so the dashboard can fan the modules out concurrently.

    ``slug`` may be supplied by the caller (resolved in bulk via
    ``module_profile.all_slugs()``) to skip the per-module ``mp.load`` entirely.
    """
    from app.storage import storage_backend as sb
    slug = slug or _slug_for(module_name)
    md = sb.read_text(f"{slug}/daily_plan.md")
    parsed = parse_plan(md) if md else None
    raw = sb.read_text(f"{slug}/task_history.json")
    history: List[Dict[str, Any]] = []
    if raw:
        try:
            history = json.loads(raw)
        except Exception:
            history = []
    return parsed, history


def record_completed_task(
    module_name: str, topic_id: str, topic_name: str, task_text: str, slug: Optional[str] = None
) -> None:
    """Append a completed task to history (idempotent — skips if already recorded today)."""
    history = load_task_history(module_name, slug)
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
        _save_task_history(module_name, history, slug)


def remove_completed_task(module_name: str, topic_id: str, task_text: str, slug: Optional[str] = None) -> None:
    """Remove a task from history (used when user un-checks a task)."""
    history = load_task_history(module_name, slug)
    history = [
        e for e in history
        if not (e["topic_id"] == topic_id and e["task_text"] == task_text)
    ]
    _save_task_history(module_name, history, slug)


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
_MIN_RE = re.compile(r"\s*<!-- min:(?P<m>\d+) -->\s*$")


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
                raw_text = tk.group("text")
                m = _MIN_RE.search(raw_text)
                current_topic["tasks"].append({
                    "text": _MIN_RE.sub("", raw_text).strip(),
                    "done": tk.group("check").lower() == "x",
                    "minutes": int(m.group("m")) if m else 45,
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
            mins = task.get("minutes", 45)
            lines.append(f"- [{mark}] {task['text']} <!-- min:{mins} -->")
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

def toggle_task(module_name: str, topic_id: str, task_index: int, done: bool) -> Dict[str, Any]:
    """Toggle a single task checkbox and sync the topic pool.

    Returns ``{"md", "card_completed", "topic_id", "topic_name"}``.
    ``card_completed`` is True only when checking a task exhausts the topic's pool.
    Raises ValueError if no plan exists.
    """
    from . import topic_pool as tp

    # Resolve the storage slug ONCE and thread it through every read/write below.
    # Otherwise each helper re-resolves it via _slug_for → mp.load (a DB round-trip),
    # turning a single toggle into ~7 redundant profile lookups.
    slug = _slug_for(module_name)

    md = load_plan(module_name, slug)
    if not md:
        raise ValueError("Kein aktiver Plan vorhanden.")

    lines = md.splitlines()
    in_topic = False
    task_count = 0
    current_topic_name = ""
    task_text = ""

    for i, line in enumerate(lines):
        tm = _TOPIC_RE.match(line)
        if tm:
            in_topic = (tm.group("id") == topic_id)
            current_topic_name = tm.group("name").strip() if in_topic else current_topic_name
            task_count = 0
            continue
        if in_topic:
            tk = _TASK_RE.match(line)
            if tk:
                if task_count == task_index:
                    mark = "x" if done else " "
                    lines[i] = f"- [{mark}] {tk.group('text')}"
                    task_text = _MIN_RE.sub("", tk.group("text")).strip()
                    if done:
                        record_completed_task(module_name, topic_id, current_topic_name, task_text, slug)
                    else:
                        remove_completed_task(module_name, topic_id, task_text, slug)
                    break
                task_count += 1

    updated = "\n".join(lines)
    if not updated.endswith("\n"):
        updated += "\n"
    updated = _refresh_progress(updated)
    save_plan(module_name, updated, slug)

    # Mirror the toggle into the topic pool. Review cards (`tX_review`) have no pool.
    card_completed = False
    if task_text and not topic_id.endswith("_review"):
        if done:
            tp.mark_task_done(module_name, topic_id, task_text, slug)
            card_completed = tp.is_pool_complete(module_name, topic_id, slug)
        else:
            tp.unmark_task(module_name, topic_id, task_text, slug)

    return {
        "md": updated,
        "card_completed": card_completed,
        "topic_id": topic_id,
        "topic_name": current_topic_name,
    }


# ─────────────────────────── Archive ────────────────────────────────────────

def delete_plan_and_history(module_name: str) -> None:
    """Delete the current daily plan and task history (called when roadmap is regenerated)."""
    from app.storage import storage_backend as sb
    slug = _slug_for(module_name)
    sb.delete(f"{slug}/daily_plan.md")
    sb.delete(f"{slug}/task_history.json")


def archive_current_plan(module_name: str) -> Optional[str]:
    """Copy daily_plan.md to YYYY-MM-DD.md in storage. Returns archive path or None."""
    from app.storage import storage_backend as sb
    content = load_plan(module_name)
    if not content:
        return None
    slug = _slug_for(module_name)
    today = date.today().isoformat()
    archive_path = f"{slug}/{today}.md"
    sb.write_text(archive_path, content)
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


# ─────────────────────────── Task sizing ────────────────────────────────────


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


def _pool_size(hours: float, n_files: int, n_subtopics: int, n_exercises: int = 0) -> int:
    """Total number of tasks the pool should hold for a topic. Clamped to [4, 16].

    Exercise sheets get the heaviest weight: when worksheets are available we want
    enough pool slots that the actual exercises can be worked through over the
    roadmap (see _POOL_PROMPT), so the cap rises above the no-exercise case.
    """
    return max(4, min(16, round(
        hours * 1.5 + n_files * 1.0 + n_subtopics * 0.5 + n_exercises * 1.5
    )))


_POOL_PROMPT = """\
Du bist ein AI Lerncoach. Erstelle einen vollständigen Aufgaben-Pool für EIN Thema.
Der Pool umfasst ALLE Aufgaben, die der Lernende über mehrere Lerntage hinweg
abarbeiten muss, um das Thema vollständig zu beherrschen. Wenn der Pool leer ist,
gilt das Thema als gemeistert.

Verstehe zuerst, um was für ein Modul es sich handelt:
- Informatik/Mathematik/Technik → eher praktische Aufgaben (Übungen lösen, Algorithmen, Code).
- Theoriekurse (Wirtschaft/Linguistik/Recht) → eher Konzepte verstehen und zusammenfassen.

THEMA: {topic_name}
SUBTOPICS: {subtopics}
DATEIEN (aus Roadmap-Zuweisung): {files}
ÜBUNGSBLÄTTER (aus Roadmap-Zuweisung): {exercises}
{rag_files_section}{rag_section}
ANZAHL AUFGABEN: genau {n}

Richtlinien:
- Decke das gesamte Thema und alle Subtopics ab — vom Verstehen der Grundlagen
  bis zum prüfungsnahen Anwenden.
- WICHTIG — Übungsblätter/Skript abarbeiten: Wenn Übungsblätter oder Skript-/Vorlesungsdateien
  verfügbar sind, ist es zentral, dass am Ende des Pools die meisten konkreten Aufgaben daraus
  tatsächlich bearbeitet wurden. Priorisiere echte Aufgaben aus dem vorhandenen Material
  (einzelne Aufgaben eines Blattes, Abschnitte des Skripts) gegenüber generischen
  Verständnis-Aufgaben. Verteile die verfügbaren Aufgaben so über den Pool, dass keine wichtige
  Übungsaufgabe ausgelassen wird — generische Aufgaben (zusammenfassen, erklären) nur ergänzend,
  wenn das konkrete Material erschöpft ist.
- Datei-Referenzen: Bevorzuge Dateien aus "INHALTLICH VERIFIZIERTE DATEIEN" — diese enthalten
  nachweislich Inhalt zu diesem Thema. Nutze Dateien aus "DATEIEN (aus Roadmap-Zuweisung)" nur
  wenn sie inhaltlich zum Thema passen (Dateiname ist ein Hinweis, kein Beweis).
  Referenziere KEINE Datei, wenn du nicht sicher bist, dass sie zu diesem Thema gehört.
- INHALT AUS DEM LERNMATERIAL dient nur zum Verstehen des Themas. Kopiere KEINEN Text daraus
  in die Aufgaben — referenziere ausschließlich Dateinamen, nie Chunk-Inhalte oder Auszüge.
- Aufgaben-Nummern: Erfinde KEINE Aufgaben-Nummern. Nutze eine Nummer (z.B. "Aufgabe 3")
  NUR wenn du sie explizit im INHALT AUS DEM LERNMATERIAL gelesen hast.
  Wenn du keine genaue Nummer siehst, beschreibe den Inhalt stattdessen
  (z.B. "Löse die Normalisierungsaufgabe aus Blatt3" statt "Löse Aufgabe 5 aus Blatt3").
- Schreibe keine Aufgabenstellungen, sondern kurze, konkrete Referenzen, die anleiten,
  was genau zu tun ist.
- Vermeide vage Formulierungen wie "Lerne Thema X". Sei konkret und handlungsorientiert.
- Steigere die Schwierigkeit über den Pool hinweg leicht (erst Verständnis, dann Anwendung).
- Keine Dopplungen.

Beispiele für gute Aufgaben:
- "Löse Aufgabe 3 aus Blatt3 vollständig und kontrolliere deine Lösungen." (Nummer aus Kontext bekannt)
- "Bearbeite die ER-Modellierungs-Aufgabe aus Blatt2." (Nummer nicht sichtbar → Inhalt beschreiben)
- "Lese Kapitel 4 der VL5 und fasse die 3 wichtigsten Konzepte in eigenen Worten zusammen."
- "Erkläre die Normalformen (1NF, 2NF, 3NF) anhand eigener Beispiele."

Antworte NUR als JSON-Array mit genau {n} Objekten:
[{{"text": "Aufgabenbeschreibung", "minutes": 30}}, ...]

Schätze "minutes" konservativ — tendiere zur unteren Grenze, der Lernende kann immer länger arbeiten:
- Einen Abschnitt / eine Seite lesen oder durcharbeiten: 10–20 min
- Konzept erklären, zusammenfassen oder Mindmap erstellen: 15–25 min
- Einfache Übungsaufgabe (1 Teilaufgabe): 20–35 min
- Komplexe Übungsaufgabe (mehrere Teilaufgaben / ganzes Blatt): 45–90 min
- Eigene Beispiele entwickeln oder Prüfungsfragen beantworten: 15–30 min"""


def _fallback_pool_tasks(
    files: List[str], exercises: List[str], subtopics: List[str], name: str, n: int
) -> List[Dict[str, Any]]:
    """Build pool tasks from metadata when the LLM is unavailable."""
    tasks: List[Dict[str, Any]] = []
    for ex in exercises:
        if len(tasks) >= n:
            break
        tasks.append({"text": ex, "done": False, "minutes": 60})
    for f in files:
        if len(tasks) >= n:
            break
        label = f"{f} – {subtopics[0]}" if subtopics else f"{f} – {name}"
        tasks.append({"text": label, "done": False, "minutes": 25})
    _PAD_TEMPLATES = [
        ("Lies deine Notizen zu '{}' und markiere unverstandene Stellen.", 20),
        ("Fasse '{}' in 5 Sätzen zusammen.", 30),
        ("Erkläre '{}' so, als würdest du es einem Kommilitonen erklären.", 35),
        ("Notiere 3 mögliche Prüfungsfragen zu '{}' und beantworte sie.", 40),
        ("Erstelle eine Mindmap zu '{}'.", 30),
        ("Überprüfe dein Verständnis von '{}' mit einer Selbstabfrage.", 25),
    ]
    i = 0
    while len(tasks) < n and subtopics:
        sub = subtopics[i % len(subtopics)]
        text = f"Erkläre '{sub}' aus {name} in eigenen Worten mit einem Beispiel."
        if not any(t["text"] == text for t in tasks):
            tasks.append({"text": text, "done": False, "minutes": 35})
        i += 1
        if i > n * 3:
            break
    pad_idx = 0
    while len(tasks) < n:
        text, mins = _PAD_TEMPLATES[pad_idx % len(_PAD_TEMPLATES)]
        text = text.format(name)
        if not any(t["text"] == text for t in tasks):
            tasks.append({"text": text, "done": False, "minutes": mins})
        pad_idx += 1
        if pad_idx > n * len(_PAD_TEMPLATES):
            break
    return tasks[:n]


def _fetch_topic_rag(
    name: str,
    subtopics: List[str],
    module_name: str,
    rag_fn: Optional[Callable],
    *,
    include_exercises: bool = True,
) -> str:
    """Retrieve RAG content for a topic (topic query + optional exercise query).

    Shared by pool generation, topic matching, and pool extension so they all see
    the same retrieval. Returns "" when no ``rag_fn`` is supplied or nothing hits.
    """
    if not rag_fn:
        return ""
    rag_query = name + ((" — " + ", ".join(subtopics[:4])) if subtopics else "")
    rag_content = rag_fn(rag_query, module_name, 8) or ""
    # Second pass: exercise-specific chunks so the LLM reads actual exercise
    # numbers instead of guessing them.
    if include_exercises:
        ex_content = rag_fn(f"Aufgabe Übung {name}", module_name, 6) or ""
        if ex_content:
            existing_chunks = set(rag_content.split("\n\n"))
            new_chunks = [c for c in ex_content.split("\n\n") if c not in existing_chunks]
            if new_chunks:
                rag_content = (
                    rag_content + "\n\n" + "\n\n".join(new_chunks) if rag_content else ex_content
                )
    return rag_content


def _extract_rag_verified_files(rag_content: str) -> List[str]:
    """Extract source filenames referenced in RAG hits (e.g. ``[lecture.pdf]``).

    These files provably contain content for the query, unlike roadmap
    assignments which are based only on filenames and can be wrong.
    """
    if not rag_content:
        return []
    matches = re.findall(r"\[([^\[\]\n]+\.\w+)\]", rag_content)
    return list(dict.fromkeys(
        m.split(":", 1)[-1].strip() if ":" in m else m
        for m in matches
    ))


def _parse_task_array(raw: str, cap: int) -> List[Dict[str, Any]]:
    """Parse the LLM's JSON task array, tolerating code fences / surrounding chatter.

    Returns up to ``cap`` task dicts (``text``/``done``/``minutes``), deduped by
    exact text. Raises if no JSON array can be recovered so the caller can fall back.
    """
    raw = (raw or "").strip()
    # Gemini (no assistant prefill) may wrap the array in code fences or chatter.
    raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
    raw = re.sub(r"\n?```\s*$", "", raw).strip()
    first_bracket = raw.find("[")
    last_bracket = raw.rfind("]")
    if first_bracket == -1 or last_bracket == -1 or last_bracket < first_bracket:
        raise ValueError("No JSON array in response")
    parsed = json.loads(raw[first_bracket : last_bracket + 1])
    if not isinstance(parsed, list):
        raise ValueError("Expected list")
    tasks: List[Dict[str, Any]] = []
    seen: set = set()
    for t in parsed:
        if isinstance(t, dict):
            text = str(t.get("text", "")).strip()
            try:
                minutes = max(5, int(t.get("minutes", 45)))
            except (TypeError, ValueError):
                minutes = 45
        else:
            text = str(t).strip()
            minutes = 45
        if text and text not in seen:
            seen.add(text)
            tasks.append({"text": text, "done": False, "minutes": minutes})
        if len(tasks) >= cap:
            break
    return tasks


def _dedup_similar(
    tasks: List[Dict[str, Any]],
    existing_texts: Optional[List[str]] = None,
    threshold: float = 0.75,
) -> List[Dict[str, Any]]:
    """Drop tasks that are near-duplicates (SequenceMatcher ratio > threshold) of
    ``existing_texts`` or of an earlier task in the list."""
    from difflib import SequenceMatcher
    existing_lower = [e.lower() for e in (existing_texts or [])]
    deduped: List[Dict[str, Any]] = []
    for task in tasks:
        txt = task["text"].lower()
        if any(SequenceMatcher(None, txt, e).ratio() > threshold for e in existing_lower):
            continue
        if any(SequenceMatcher(None, txt, d["text"].lower()).ratio() > threshold for d in deduped):
            continue
        deduped.append(task)
    return deduped


def _generate_pool(
    topic: Dict[str, Any],
    module_name: str,
    rag_fn: Optional[Callable] = None,
) -> None:
    """Generate all pool tasks for a topic in one LLM call and persist via topic_pool."""
    from . import topic_pool as tp

    topic_id = str(topic.get("id") or "")
    name = str(topic.get("name") or "")

    subtopics = [
        str(s).strip()
        for s in (topic.get("subtopics") or topic.get("untergruppen") or [])
        if str(s).strip()
    ]
    profile = mp.load(module_name)
    file_types: Dict[str, str] = (profile.get("file_types") or {}) if profile else {}
    files, exercises = _split_files(
        topic.get("dateien") or topic.get("files") or [],
        topic.get("aufgaben") or topic.get("exercises") or [],
        file_types,
    )
    try:
        hours = float(topic.get("hours") or 2.0)
    except (TypeError, ValueError):
        hours = 2.0

    n = _pool_size(hours, len(files), len(subtopics), len(exercises))

    rag_content = _fetch_topic_rag(
        name, subtopics, module_name, rag_fn, include_exercises=bool(exercises)
    )

    # Source filenames from RAG hits provably contain content for this topic,
    # unlike roadmap assignments which are based only on filenames.
    rag_verified_files = _extract_rag_verified_files(rag_content)

    rag_section = f"INHALT AUS DEM LERNMATERIAL:\n{rag_content[:4500]}\n\n" if rag_content else ""
    rag_files_section = (
        f"INHALTLICH VERIFIZIERTE DATEIEN (enthalten nachweislich Inhalt zu diesem Thema): "
        f"{', '.join(rag_verified_files)}\n"
        if rag_verified_files else ""
    )

    prompt = _POOL_PROMPT.format(
        topic_name=name,
        subtopics=", ".join(subtopics) if subtopics else "—",
        files=", ".join(files) if files else "—",
        exercises=", ".join(exercises) if exercises else "—",
        rag_files_section=rag_files_section,
        rag_section=rag_section,
        n=n,
    )

    tasks: List[Dict[str, Any]] = []
    try:
        resp = _get_client().chat.completions.create(
            model=MODEL,
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
        )
        tasks = _parse_task_array(resp.choices[0].message.content or "", n)
    except Exception as exc:
        print(f"[daily_tasks] pool gen failed for {name}: {exc}")

    if not tasks:
        tasks = _fallback_pool_tasks(files, exercises, subtopics, name, n)

    # Remove near-duplicate tasks (similarity > 75%)
    tasks = _dedup_similar(tasks)

    # Cap total minutes at hours * 60
    budget = hours * 60
    total_min = sum(t["minutes"] for t in tasks)
    if total_min > budget:
        factor = budget / total_min
        for t in tasks:
            t["minutes"] = max(5, round(t["minutes"] * factor))

    pool = {
        "topic_id": topic_id,
        "topic_name": name,
        "generated_at": date.today().isoformat(),
        "pool_size": len(tasks),
        "tasks": tasks,
    }
    tp.save_pool(module_name, topic_id, pool)


# ─────────────────────── Pool extension (new uploads) ───────────────────────

_POOL_EXTEND_PROMPT = """\
Du bist ein AI Lerncoach. Zu einem bestehenden Aufgaben-Pool für EIN Thema wurde
neues Lernmaterial hochgeladen. Erzeuge NUR zusätzliche Aufgaben, die sich aus dem
NEUEN Material ergeben — die bestehenden Aufgaben bleiben unverändert.

THEMA: {topic_name}
SUBTOPICS: {subtopics}
NEUE DATEIEN: {new_files}

BEREITS VORHANDENE AUFGABEN (NICHT wiederholen, nichts inhaltlich Ähnliches erneut erzeugen):
{existing_tasks}

INHALT AUS DEN NEUEN DATEIEN (Hauptquelle für die neuen Aufgaben):
{new_content}
{context_section}
Richtlinien:
- Erzeuge bis zu {max_new} neue, konkrete Aufgaben — aber NUR so viele, wie das neue
  Material wirklich hergibt. Lieber wenige gute als generische Fülltasks. Bietet das
  neue Material kaum Substanz, gib weniger (oder ein leeres Array) zurück.
- Beziehe dich konkret auf das neue Material (einzelne Aufgaben eines neuen Blattes,
  Abschnitte eines neuen Skripts).
- Aufgaben-Nummern: Nutze eine Nummer (z.B. "Aufgabe 3") NUR wenn du sie explizit im
  INHALT gelesen hast. Sonst beschreibe den Inhalt.
- Referenziere ausschließlich Dateinamen, kopiere KEINEN Text aus dem Material.
- Keine Dopplungen mit den bereits vorhandenen Aufgaben.
- Vermeide vage Formulierungen wie "Lerne Thema X". Sei konkret und handlungsorientiert.

Antworte NUR als JSON-Array (höchstens {max_new} Objekte, weniger ist ausdrücklich erlaubt):
[{{"text": "Aufgabenbeschreibung", "minutes": 30}}, ...]

Schätze "minutes" konservativ (10–90 min je nach Aufwand)."""


def _topic_affected_by_new_files(
    topic: Dict[str, Any],
    module_name: str,
    new_files: List[str],
    rag_fn: Optional[Callable],
) -> Tuple[List[str], str]:
    """Decide which of the newly uploaded files are relevant to ``topic``.

    Runs the same RAG retrieval as pool generation, then keeps any new file whose
    basename shows up in the RAG-verified source files. Falls back to a
    filename-containment check against the topic's roadmap assignments. Returns
    ``(matched_files, rag_content)`` so the content can be reused by ``extend_pool``.
    """
    name = str(topic.get("name") or "")
    subtopics = [
        str(s).strip()
        for s in (topic.get("subtopics") or topic.get("untergruppen") or [])
        if str(s).strip()
    ]
    rag_content = _fetch_topic_rag(name, subtopics, module_name, rag_fn)

    verified = {Path(v).name for v in _extract_rag_verified_files(rag_content)}
    matched = [f for f in new_files if f and Path(f).name in verified]
    if matched:
        return matched, rag_content

    # Fallback: the new file is named in the topic's roadmap assignments.
    assigned_blob = " ".join(
        str(a) for a in (
            (topic.get("dateien") or topic.get("files") or [])
            + (topic.get("aufgaben") or topic.get("exercises") or [])
        )
    )
    matched = [f for f in new_files if f and Path(f).name in assigned_blob]
    return matched, rag_content


def extend_pool(
    topic: Dict[str, Any],
    module_name: str,
    new_files: List[str],
    rag_fn: Optional[Callable] = None,
    *,
    max_new: int = 8,
    rag_content: Optional[str] = None,
) -> int:
    """Append new tasks derived from newly uploaded files to a topic's pool.

    Existing tasks (done and open) are preserved. Returns the number of tasks
    actually added. If no pool exists yet, falls back to first-time generation via
    ``_generate_pool`` and returns the resulting pool size.
    """
    from . import topic_pool as tp

    topic_id = str(topic.get("id") or "")
    name = str(topic.get("name") or "")

    pool = tp.load_pool(module_name, topic_id)
    if not pool:
        _generate_pool(topic, module_name, rag_fn)
        fresh = tp.load_pool(module_name, topic_id)
        return len((fresh or {}).get("tasks") or [])

    existing_tasks = pool.get("tasks") or []
    existing_texts = [str(t.get("text", "")) for t in existing_tasks]

    subtopics = [
        str(s).strip()
        for s in (topic.get("subtopics") or topic.get("untergruppen") or [])
        if str(s).strip()
    ]
    if rag_content is None:
        rag_content = _fetch_topic_rag(name, subtopics, module_name, rag_fn)

    # Split retrieved chunks into those sourced from the new files (primary
    # signal) and everything else (context).
    new_basenames = {Path(f).name for f in new_files if f}
    focused: List[str] = []
    context: List[str] = []
    for chunk in (rag_content.split("\n\n") if rag_content else []):
        m = re.match(r"\s*\[([^\[\]\n]+)\]", chunk)
        src = Path(m.group(1).split(":", 1)[-1].strip()).name if m else ""
        (focused if src in new_basenames else context).append(chunk)
    new_content = ("\n\n".join(focused)[:4500] if focused else (rag_content or "")[:2000]) or "—"
    context_blob = "\n\n".join(context)[:2000]
    context_section = (
        f"\nWEITERER KONTEXT (bestehendes Material, nur zur Orientierung):\n{context_blob}\n"
        if context_blob else ""
    )

    prompt = _POOL_EXTEND_PROMPT.format(
        topic_name=name,
        subtopics=", ".join(subtopics) if subtopics else "—",
        new_files=", ".join(Path(f).name for f in new_files if f) if new_basenames else "—",
        existing_tasks="\n".join(f"- {txt}" for txt in existing_texts) if existing_texts else "—",
        new_content=new_content,
        context_section=context_section,
        max_new=max_new,
    )

    new_tasks: List[Dict[str, Any]] = []
    try:
        resp = _get_client().chat.completions.create(
            model=MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        new_tasks = _parse_task_array(resp.choices[0].message.content or "", max_new)
    except Exception as exc:
        print(f"[daily_tasks] pool extend failed for {name}: {exc}")

    # No fallback padding on extension — we never append generic filler tasks.
    if not new_tasks:
        return 0

    # Dedup against existing pool tasks (incl. done) and among the new ones.
    new_tasks = _dedup_similar(new_tasks, existing_texts)
    if not new_tasks:
        return 0

    pool["tasks"] = existing_tasks + new_tasks
    pool["pool_size"] = len(pool["tasks"])
    pool["extended_at"] = date.today().isoformat()  # generated_at stays untouched
    tp.save_pool(module_name, topic_id, pool)
    return len(new_tasks)


def _sync_roadmap_assignments(
    module_name: str, topics: List[Dict[str, Any]], new_files: List[str]
) -> None:
    """Add the new files to the matched topics' assignments in the saved roadmap so
    a later full regeneration keeps the association."""
    from . import roadmap as rm

    md = rm.load_roadmap_md(module_name)
    if not md:
        return
    profile = mp.load(module_name)
    file_types: Dict[str, str] = (profile.get("file_types") or {}) if profile else {}
    changed = False
    for topic in topics:
        tid = str(topic.get("id") or "")
        files = [f for f in (topic.get("_matched_files") or []) if f] or new_files
        basenames = [Path(f).name for f in files if f]
        lecture_files, exercise_files = _split_files(basenames, [], file_types)
        if not tid:
            continue
        md, did = rm.add_files_to_topic(md, tid, lecture_files, exercise_files)
        changed = changed or did
    if changed:
        rm.save_roadmap_md(module_name, md)


def extend_pools_for_new_files(
    module_name: str,
    roadmap_data: Dict[str, Any],
    new_files: List[str],
    rag_fn: Optional[Callable] = None,
    *,
    concurrency: int = 6,
    progress_cb: Optional[Callable[[int, int, str, int], None]] = None,
) -> Dict[str, int]:
    """Extend the pools of every topic affected by the newly uploaded files.

    Determines affected topics via RAG matching (in parallel), then extends those
    pools in parallel and keeps the saved roadmap's file/exercise assignments in
    sync. ``progress_cb(done, total, topic_name, added)`` fires once per affected
    topic, where ``total`` is the number of affected topics. Returns
    ``{topic_name: added_count}`` for the affected topics. Individual failures are
    non-fatal. Request context (Supabase auth ContextVars) is replayed into the
    worker threads so storage RLS still applies.
    """
    topics = [
        topic
        for phase in (roadmap_data.get("phases") or [])
        for topic in (phase.get("topics") or [])
        if str(topic.get("id") or "")
    ]
    new_files = [f for f in (new_files or []) if f]
    if not topics or not new_files:
        return {}

    parent_ctx = contextvars.copy_context()

    # Stage 1 — determine affected topics (RAG calls) in parallel.
    def _match(topic: Dict[str, Any]):
        for _var, _val in parent_ctx.items():
            _var.set(_val)
        try:
            matched_files, content = _topic_affected_by_new_files(
                topic, module_name, new_files, rag_fn
            )
        except Exception as exc:
            print(f"[daily_tasks] extend match failed for {topic.get('name')}: {exc}")
            return None
        if not matched_files:
            return None
        topic["_matched_files"] = matched_files
        return (topic, content, matched_files)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        matched = [r for r in pool.map(_match, topics) if r]

    total = len(matched)
    if total == 0:
        return {}

    # Stage 2 — extend the affected pools in parallel.
    results: Dict[str, int] = {}
    lock = threading.Lock()
    counter = {"done": 0}

    def _extend(item) -> None:
        topic, content, matched_files = item
        for _var, _val in parent_ctx.items():
            _var.set(_val)
        name = str(topic.get("name") or "")
        try:
            added = extend_pool(
                topic, module_name, matched_files, rag_fn, rag_content=content
            )
        except Exception as exc:
            print(f"[daily_tasks] extend_pool failed for {name}: {exc}")
            added = 0
        with lock:
            counter["done"] += 1
            done = counter["done"]
            results[name] = added
        if progress_cb:
            progress_cb(done, total, name, added)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        list(pool.map(_extend, matched))

    # Keep the saved roadmap's assignments in sync (one write).
    try:
        _sync_roadmap_assignments(module_name, [t for t, _c, _f in matched], new_files)
    except Exception as exc:
        print(f"[daily_tasks] roadmap assignment sync failed: {exc}")

    return results


def generate_all_pools(
    module_name: str,
    roadmap_data: Dict[str, Any],
    rag_fn: Optional[Callable] = None,
    *,
    concurrency: int = 6,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> int:
    """Generate a task pool for EVERY topic in the roadmap, in parallel.

    Iterates all topics across all phases and calls ``_generate_pool`` per topic via a
    bounded ``ThreadPoolExecutor``. Calls ``progress_cb(done_count, total, topic_name)``
    after each completed topic if provided. Returns the number of pools generated.
    Individual failures are non-fatal (``_generate_pool`` already falls back to
    metadata tasks); a topic that still raises is skipped without aborting the batch.

    Runs synchronously (blocks until all topics are done) so it is usable from a
    plain ``/accept`` handler as well as from a background thread feeding an SSE
    stream.
    """
    topics = [
        topic
        for phase in (roadmap_data.get("phases") or [])
        for topic in (phase.get("topics") or [])
        if str(topic.get("id") or "")
    ]
    total = len(topics)
    if total == 0:
        return 0

    # Worker threads start with an empty contextvars context, but storage access
    # is scoped to the per-request Supabase user (JWT / user_id stashed in
    # ContextVars by the auth middleware). Snapshot the caller's context and
    # replay it inside each worker so RLS still applies and writes don't fail closed.
    parent_ctx = contextvars.copy_context()

    lock = threading.Lock()
    counter = {"done": 0, "ok": 0}

    def _work(topic: Dict[str, Any]) -> None:
        for _var, _val in parent_ctx.items():
            _var.set(_val)
        name = str(topic.get("name") or "")
        try:
            _generate_pool(topic, module_name, rag_fn)
            ok = True
        except Exception as exc:  # _generate_pool already self-heals; guard anyway
            print(f"[daily_tasks] generate_all_pools failed for {name}: {exc}")
            ok = False
        with lock:
            counter["done"] += 1
            if ok:
                counter["ok"] += 1
            done = counter["done"]
        if progress_cb:
            progress_cb(done, total, name)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        list(pool.map(_work, topics))

    return counter["ok"]


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

    # 4) Build new topic list (carryover first, then new). Tasks are drawn
    #    sequentially from each topic's pool; the pool is generated lazily the
    #    first time a topic is scheduled.
    from . import topic_pool as tp

    new_topics: List[Dict] = []
    for topic, alloc_hours in selected:
        tid = str(topic.get("id") or "")
        if tid in carry_ids:
            continue
        pool = tp.load_pool(module_name, tid)
        pool_has_open = pool and any(not t.get("done") for t in (pool.get("tasks") or []))
        if not pool_has_open:
            # No pool yet, or pool is exhausted (stale from old roadmap) — regenerate
            _generate_pool(topic, module_name, rag_fn)
        tasks = tp.get_tasks_for_hours(module_name, tid, alloc_hours)
        if not tasks:
            continue
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
