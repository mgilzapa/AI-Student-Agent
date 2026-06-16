"""
Practice worksheet ("Übungsblatt") — a topic-scoped sheet of fresh exercises in
the style of the module's own materials. Unlike the completion quiz, a worksheet
does not gate card completion; it is pure practice and may be generated as often
as the user likes (one at a time).

The LLM infers the exercise format from the course type, exactly like the quiz:
- Math / CS / Engineering → calculations, algorithm traces, small code/proof tasks
- Theory / Economics / Law / Linguistics → multiple-choice, definitions, short-answer
- Mixed → the LLM decides from the available materials

Storage: ``{slug}/worksheet_{topic_id}_{seq}.json`` (a topic can hold many)

    {
      "worksheet_id": "t3_001",
      "topic_id": "t3",
      "topic_name": "Normalformen",
      "title": "Übungsblatt: Normalformen",
      "generated_at": "2026-06-14",
      "exercises": [
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
# Reuse the battle-tested, LaTeX-tolerant JSON parser from the quiz feature — the
# exercise schema (type/question/solution/options/correct) is identical.
from .topic_quiz import _parse_quiz

# Gemini 2.5 Flash via the OpenAI-compatible endpoint (see app/llm_clients.py).
MODEL = os.getenv("WORKSHEET_MODEL", "gemini-2.5-flash")

# A full worksheet (4–6 exercises, each with a detailed LaTeX solution) needs far
# more room than the quiz; a small budget truncates the JSON mid-array.
# _salvage_exercises below recovers gracefully if a response still gets cut off.
MAX_TOKENS = 8000

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


def _worksheet_path(module_name: str, worksheet_id: str) -> str:
    return f"{_slug_for(module_name)}/worksheet_{worksheet_id}.json"


def _list_raw(module_name: str) -> List[Dict[str, Any]]:
    """Return parsed JSON of every saved worksheet for a module (full objects)."""
    from app.storage import storage_backend as sb
    from app.storage.supabase_client import get_client, get_user_id
    slug = _slug_for(module_name)
    uid = get_user_id()
    folder = f"{uid}/{slug}"
    try:
        items = get_client().storage.from_("processed").list(folder) or []
    except Exception:
        items = []
    result: List[Dict[str, Any]] = []
    for item in items:
        name = item.get("name", "")
        if not name.startswith("worksheet_") or not name.endswith(".json"):
            continue
        raw = sb.read_text(f"{slug}/{name}")
        if not raw:
            continue
        try:
            result.append(json.loads(raw))
        except Exception:
            continue
    return result


def list_worksheets(module_name: str) -> List[Dict[str, Any]]:
    """Return metadata of all saved worksheets for a module (newest first)."""
    out = []
    for w in _list_raw(module_name):
        out.append({
            "worksheet_id": w.get("worksheet_id", ""),
            "topic_id": w.get("topic_id", ""),
            "topic_name": w.get("topic_name", ""),
            "title": w.get("title", ""),
            "generated_at": w.get("generated_at", ""),
            "num_exercises": len(w.get("exercises", [])),
        })
    # Sort newest first by the numeric seq suffix of the worksheet_id.
    out.sort(key=lambda m: _seq_of(m.get("worksheet_id", "")), reverse=True)
    return out


def _seq_of(worksheet_id: str) -> int:
    m = re.search(r"_(\d+)$", worksheet_id or "")
    return int(m.group(1)) if m else 0


def _next_seq(module_name: str, topic_id: str) -> int:
    """Next 1-based sequence number for a topic's worksheets."""
    seqs = [
        _seq_of(w.get("worksheet_id", ""))
        for w in _list_raw(module_name)
        if w.get("topic_id") == topic_id
    ]
    return (max(seqs) + 1) if seqs else 1


def load_worksheet(module_name: str, worksheet_id: str) -> Optional[Dict[str, Any]]:
    from app.storage import storage_backend as sb
    raw = sb.read_text(_worksheet_path(module_name, worksheet_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def save_worksheet(module_name: str, worksheet: Dict[str, Any]) -> None:
    from app.storage import storage_backend as sb
    sb.write_text(
        _worksheet_path(module_name, worksheet["worksheet_id"]),
        json.dumps(worksheet, ensure_ascii=False, indent=2),
    )


def delete_worksheet(module_name: str, worksheet_id: str) -> bool:
    from app.storage import storage_backend as sb
    return sb.delete(_worksheet_path(module_name, worksheet_id))


# ─────────────────────────── LLM generation ─────────────────────────────────

_WORKSHEET_PROMPT = """\
Du bist ein universitärer Dozent. Erstelle EIN neues Übungsblatt für EIN Thema —
neue Aufgaben im selben Stil wie die Materialien des Kurses, damit der Lernende den
Stoff einübt.

Erkenne zuerst die Art des Moduls und wähle das passende Aufgabenformat:
- Mathematik/Informatik/Technik → Rechenaufgaben, Algorithmen nachvollziehen, kleine Code-/Beweisaufgaben (type "open").
- Theorie (Wirtschaft/Recht/Linguistik/Sozialwissenschaften) → Multiple-Choice, Definitionen, Kurzantworten.
- Gemischt → entscheide anhand der verfügbaren Materialien.

THEMA: {topic_name}
SUBTOPICS: {subtopics}
{rag_section}
ANFORDERUNGEN:
- 4 bis 6 Aufgaben, steigende Schwierigkeit, ein vollständiges Übungsblatt.
- NEUE Aufgaben — übernimm keine Aufgabe wörtlich aus den Materialien, sondern erstelle
  ähnliche Aufgaben im gleichen Stil und Niveau.
- Nur Inhalte verwenden, die zum Thema und den Materialien passen.
- Für jede Aufgabe eine vollständige, nachvollziehbare Musterlösung.
- Mathematische Ausdrücke in LaTeX ($...$ inline, $$...$$ display).
- Gib dem Blatt einen kurzen, treffenden Titel.

ANTWORT-FORMAT (NUR valides JSON, keine Markdown-Codeblöcke):
{{
  "title": "Übungsblatt: …",
  "exercises": [
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


def _extract_all_objects(text: str) -> List[str]:
    """Return every *complete*, balanced top-level ``{...}`` object substring.

    A trailing object that never closes (truncated response) is dropped. String
    contents — including escaped quotes — are skipped so braces inside strings
    don't throw off the depth count.
    """
    objs: List[str] = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 0
        in_string = False
        j = i
        complete = False
        while j < n:
            ch = text[j]
            if ch == "\\" and in_string:
                j += 2
                continue
            if ch == '"':
                in_string = not in_string
            elif not in_string:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        objs.append(text[i : j + 1])
                        complete = True
                        break
            j += 1
        if not complete:
            break  # truncated trailing object — nothing usable past here
        i = j + 1
    return objs


def _salvage_exercises(raw_text: str) -> List[Dict[str, Any]]:
    """Recover exercises from a truncated response.

    When the outer JSON object is cut off mid-array, ``_parse_quiz`` fails wholesale
    even though the earlier exercise objects are individually complete. We locate the
    ``"exercises": [`` array, pull out the complete objects, and re-parse them as a
    fresh array through the normal parser (so LaTeX/normalization handling applies).
    """
    text = (raw_text or "").strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text).strip()

    m = re.search(r'"exercises"\s*:\s*\[', text)
    start = m.end() if m else text.find("[")
    if start < 0:
        start = 0
    objs = _extract_all_objects(text[start:])
    if not objs:
        return []
    return _parse_quiz("[" + ",".join(objs) + "]")


def _extract_title(raw_text: str, fallback: str) -> str:
    """Best-effort pull of the "title" field from the raw JSON response."""
    text = (raw_text or "").strip()
    text = re.sub(r"^```(?:json)?\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            title = str(obj.get("title") or "").strip()
            if title:
                return title
    except Exception:
        pass
    m = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', text)
    if m:
        # Proper JSON-string unescaping (handles \uXXXX, \") without mangling UTF-8.
        try:
            title = json.loads('"' + m.group(1) + '"').strip()
        except Exception:
            title = m.group(1).strip()
        if title:
            return title
    return fallback


def generate(
    module_name: str,
    topic: Dict[str, Any],
    rag_context: str = "",
) -> Dict[str, Any]:
    """Generate and persist a new practice worksheet for a topic. Returns the dict."""
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

    prompt = _WORKSHEET_PROMPT.format(
        topic_name=name,
        subtopics=", ".join(subtopics) if subtopics else "—",
        rag_section=rag_section,
    )

    resp = _get_client().chat.completions.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = (resp.choices[0].message.content or "").strip()
    exercises = _parse_quiz(raw)
    if not exercises:
        # Response likely truncated mid-array — salvage the complete exercises.
        exercises = _salvage_exercises(raw)

    if not exercises:
        import sys
        print(f"[worksheet] FULL RAW RESPONSE:\n{raw}", file=sys.stderr)
        raise ValueError(
            f"Übungsblatt-Generierung lieferte keine auswertbaren Aufgaben für '{name}'. "
            f"Antwort-Anfang: {raw[:120]!r}"
        )

    seq = _next_seq(module_name, topic_id)
    worksheet_id = f"{topic_id}_{seq:03d}"
    title = _extract_title(raw, f"Übungsblatt: {name}" if name else "Übungsblatt")

    worksheet = {
        "worksheet_id": worksheet_id,
        "topic_id": topic_id,
        "topic_name": name,
        "title": title,
        "generated_at": date.today().isoformat(),
        "exercises": exercises,
    }
    save_worksheet(module_name, worksheet)
    return worksheet
