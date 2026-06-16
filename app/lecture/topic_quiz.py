"""
Completion quiz — a topic-scoped quiz generated when a card's task pool is
exhausted. Passing the quiz marks the roadmap card as ``done``.

The LLM infers the question format from the course type:
- Math / CS / Engineering → calculation, algorithm traces, code exercises
- Theory / Economics / Law / Linguistics → multiple-choice, definitions, short-answer
- Mixed → the LLM decides from the available materials

Storage: ``{slug}/topic_quiz_{topic_id}.json``

    {
      "topic_id": "t3",
      "topic_name": "Normalformen",
      "generated_at": "2026-06-04",
      "questions": [
        {"type": "open", "question": "...", "solution": "..."},
        {"type": "multiple_choice", "question": "...",
         "options": ["A: ...", "B: ...", "C: ...", "D: ..."],
         "correct": 2, "solution": "..."}
      ]
    }
"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any, Dict, List, Optional

import openai

from . import module_profile as mp
from ..llm_clients import make_gemini_client

# Gemini 2.5 Flash Lite via the OpenAI-compatible endpoint (see app/llm_clients.py).
MODEL = os.getenv("QUIZ_MODEL", "gemini-2.5-flash-lite")

_client: Optional[openai.OpenAI] = None


def _get_client() -> openai.OpenAI:
    global _client
    if _client is None:
        _client = make_gemini_client()
    return _client


# ─────────────────────────── Storage helpers ────────────────────────────────

def _slug_for(module_name: str) -> str:
    profile = mp.load(module_name)
    return profile["slug"] if profile else mp._slugify(module_name)


def _quiz_path(module_name: str, topic_id: str) -> str:
    return f"{_slug_for(module_name)}/topic_quiz_{topic_id}.json"


def list_quizzes(module_name: str) -> List[Dict[str, Any]]:
    """Return metadata of all saved quizzes for a module."""
    from app.storage import storage_backend as sb
    from app.storage.supabase_client import get_client, get_user_id
    slug = _slug_for(module_name)
    uid = get_user_id()
    folder = f"{uid}/{slug}"
    try:
        items = get_client().storage.from_("processed").list(folder) or []
    except Exception:
        items = []
    result = []
    for item in items:
        name = item.get("name", "")
        if not name.startswith("topic_quiz_") or not name.endswith(".json"):
            continue
        raw = sb.read_text(f"{slug}/{name}")
        if not raw:
            continue
        try:
            q = json.loads(raw)
            result.append({
                "topic_id": q.get("topic_id", ""),
                "topic_name": q.get("topic_name", ""),
                "generated_at": q.get("generated_at", ""),
                "num_questions": len(q.get("questions", [])),
                "completed": bool(q.get("completed_at")),
            })
        except Exception:
            continue
    return result


def delete_quiz(module_name: str, topic_id: str) -> bool:
    """Delete the quiz file for a topic."""
    from app.storage import storage_backend as sb
    return sb.delete(_quiz_path(module_name, topic_id))


def load_quiz(module_name: str, topic_id: str) -> Optional[Dict[str, Any]]:
    from app.storage import storage_backend as sb
    raw = sb.read_text(_quiz_path(module_name, topic_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def save_quiz(module_name: str, topic_id: str, quiz: Dict[str, Any]) -> None:
    from app.storage import storage_backend as sb
    sb.write_text(
        _quiz_path(module_name, topic_id),
        json.dumps(quiz, ensure_ascii=False, indent=2),
    )


def mark_completed(module_name: str, topic_id: str) -> bool:
    """Record that the user finished this topic's quiz (gates worksheet unlock).

    Stamps ``completed_at`` into the saved quiz JSON. Returns False if no quiz
    file exists for the topic.
    """
    quiz = load_quiz(module_name, topic_id)
    if not quiz:
        return False
    quiz["completed_at"] = date.today().isoformat()
    save_quiz(module_name, topic_id, quiz)
    return True


# ─────────────────────────── Response parsing ───────────────────────────────

def _extract_first_json_object(text: str) -> str:
    """Return the substring of *text* that forms the first complete JSON object."""
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    in_string = False
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\" and in_string:
            i += 2
            continue
        if ch == '"':
            in_string = not in_string
        elif not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        i += 1
    return text[start:]  # malformed but let json.loads report the error


def _parse_quiz(raw_text: str) -> List[Dict[str, Any]]:
    """Parse the LLM response into a clean list of question dicts.

    Tolerates code fences, an object wrapper (``{"questions": [...]}``) or a bare
    array. Drops questions without text; a ``multiple_choice`` question without
    options/correct degrades to an ``open`` question.
    """
    text = (raw_text or "").strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text).strip()

    # LaTeX inside JSON strings (e.g. \Omega, \mathcal) uses backslash sequences
    # that are invalid JSON escapes. Double *lone* backslashes so json.loads
    # decodes them as a literal backslash + letter (e.g. \Omega → \\Omega).
    # An alternation consumes valid escape sequences (\\, \", \n, \uXXXX, …) as a
    # unit and leaves them untouched — otherwise a model that ALREADY emits valid
    # JSON (Claude writes \\Omega) would have its second backslash double-escaped
    # into \\\Omega, producing an "Invalid \escape" error.
    text = re.sub(
        r'\\(["\\/bfnrtu]|u[0-9a-fA-F]{4})|\\',
        lambda m: m.group(0) if m.group(1) else r'\\',
        text,
    )

    data = None
    parse_error: Optional[str] = None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        parse_error = f"json.loads failed at pos {exc.pos}: {exc.msg}"
        extracted = _extract_first_json_object(text)
        try:
            data = json.loads(extracted)
            parse_error = None
        except Exception as exc2:
            parse_error = f"{parse_error} | fallback failed: {exc2}"
    except Exception as exc:
        parse_error = str(exc)

    if data is None:
        import sys
        print(f"[quiz] parse failure: {parse_error}", file=sys.stderr)
        print(f"[quiz] raw text snippet: {text[:300]!r}", file=sys.stderr)
        return []

    if isinstance(data, dict):
        # "questions" (quiz) or "exercises" (worksheet) — same item schema.
        items = data.get("questions") or data.get("exercises") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []

    questions: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question") or "").strip()
        if not question:
            continue
        solution = str(item.get("solution") or "").strip()
        qtype = str(item.get("type") or "open").strip()
        options = item.get("options")

        if qtype == "multiple_choice" and isinstance(options, list) and len(options) >= 2:
            try:
                correct = int(item.get("correct"))
            except (TypeError, ValueError):
                correct = 0
            correct = max(0, min(correct, len(options) - 1))
            questions.append({
                "type": "multiple_choice",
                "question": question,
                "options": [str(o) for o in options],
                "correct": correct,
                "solution": solution,
            })
        else:
            questions.append({
                "type": "open",
                "question": question,
                "solution": solution,
            })

    return questions


# ─────────────────────────── LLM generation ─────────────────────────────────

_QUIZ_PROMPT = """\
Du bist ein universitärer Prüfer. Erstelle ein kurzes Abschluss-Quiz für EIN Thema,
mit dem der Lernende prüfen kann, ob er das Thema beherrscht.

Erkenne zuerst die Art des Moduls und wähle das passende Frageformat:
- Mathematik/Informatik/Technik → Rechenaufgaben, Algorithmen nachvollziehen, kleine Code-/Beweisaufgaben (type "open").
- Theorie (Wirtschaft/Recht/Linguistik/Sozialwissenschaften) → Multiple-Choice, Definitionen, Kurzantworten.
- Gemischt → entscheide anhand der verfügbaren Materialien.

THEMA: {topic_name}
SUBTOPICS: {subtopics}
{rag_section}
ANFORDERUNGEN:
- 4 bis 6 Fragen, steigende Schwierigkeit.
- Nur Inhalte verwenden, die zum Thema und den Materialien passen.
- Für jede Frage eine vollständige Musterlösung.
- Mathematische Ausdrücke in LaTeX ($...$ inline, $$...$$ display).

ANTWORT-FORMAT (NUR valides JSON, keine Markdown-Codeblöcke):
{{
  "questions": [
    {{"type": "open", "question": "…", "solution": "…"}},
    {{"type": "multiple_choice", "question": "…",
      "options": ["A: …", "B: …", "C: …", "D: …"],
      "correct": 2, "solution": "… (warum C richtig ist)"}}
  ]
}}

- "type" ist entweder "open" oder "multiple_choice".
- Bei "multiple_choice": "options" mit 4 Einträgen und "correct" als 0-basierter Index.
- Mische die Formate passend zum Modul.

Antworte NUR mit dem JSON-Objekt."""


def generate(
    module_name: str,
    topic: Dict[str, Any],
    rag_context: str = "",
) -> Dict[str, Any]:
    """Generate and persist a completion quiz for a topic. Returns the quiz dict."""
    topic_id = str(topic.get("id") or "")
    name = str(topic.get("name") or "")
    subtopics = [
        str(s).strip()
        for s in (topic.get("subtopics") or topic.get("untergruppen") or [])
        if str(s).strip()
    ]

    rag_section = ""
    if rag_context:
        rag_section = f"INHALT AUS DEM LERNMATERIAL:\n{rag_context[:6000]}\n"

    prompt = _QUIZ_PROMPT.format(
        topic_name=name,
        subtopics=", ".join(subtopics) if subtopics else "—",
        rag_section=rag_section,
    )

    resp = _get_client().chat.completions.create(
        model=MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = (resp.choices[0].message.content or "").strip()
    questions = _parse_quiz(raw)

    if not questions:
        import sys
        print(f"[quiz] FULL RAW RESPONSE:\n{raw}", file=sys.stderr)
        raise ValueError(
            f"Quiz-Generierung lieferte keine auswertbaren Fragen für '{name}'. "
            f"Antwort-Anfang: {raw[:120]!r}"
        )

    quiz = {
        "topic_id": topic_id,
        "topic_name": name,
        "generated_at": date.today().isoformat(),
        "questions": questions,
    }
    save_quiz(module_name, topic_id, quiz)
    return quiz
