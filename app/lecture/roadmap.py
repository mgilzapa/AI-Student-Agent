"""
Roadmap generation, persistence, and smart merge.

Source of truth: ``data/processed/roadmaps/<slug>/<slug>.roadmap.md``.

The LLM is asked for a structured JSON object. The backend renders that JSON
into a Markdown file with HTML-comment metadata for each topic, plus a Mermaid
flowchart at the top. Topic status (``todo``/``doing``/``done``) lives inside
the Markdown comment AND the Mermaid classDef so both stay in sync. PATCHes
update the Markdown in place; smart-merge carries ``done``/``doing`` flags from
an old roadmap to a freshly generated one by topic-name matching.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from anthropic import Anthropic

from . import module_profile as mp

logger = logging.getLogger(__name__)
MODEL = "claude-sonnet-4-6"

_client: Optional[Anthropic] = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic()
    return _client


# ─────────────────────────── Generation prompt ──────────────────────────────

_GENERATE_PROMPT = """Erkenne die Sprache der Kursinhalte im KONTEXT-Abschnitt und antworte vollständig in dieser Sprache. Alle Phasennamen, Themen-Namen, Zusammenfassungen und sonstigen Inhalte müssen in der erkannten Sprache verfasst sein.

Du bist ein erfahrener universitärer Lerncoach.
Erstelle eine Lern-Roadmap als visuell darstellbarer Skill-Graph (roadmap.sh-Stil) für Modul "{modul}".
{user_focus_section}
KONTEXT
{context}
{exam_files_section}{available_files_section}{old_roadmap_section}

PHASEN-REIHENFOLGE (lass weg was nicht passt):
prerequisites → basics → core_concepts → methods → practice → exam_training → common_mistakes → review

ANTWORT-FORMAT (NUR valides JSON, keine Markdown-Codeblöcke, kein Kommentar):

{{
  "module": "{modul}",
  "exam_date": "YYYY-MM-DD oder leer",
  "layout": "top-down",
  "phases": [
    {{
      "id": "ph1",
      "title": "Voraussetzungen",
      "topics": [
        {{
          "id": "t1",
          "name": "Logik-Grundlagen",
          "relevance": "high|medium|low",
          "status": "done|in_progress|open",
          "hours": 2,
          "summary": "Was das Thema ist (1 Satz)",
          "exam_relevance_reason": "Warum prüfungsrelevant (1 Satz)",
          "subtopics": ["Unterthema 1", "Unterthema 2"],
          "files": ["Skript Kap.1.pdf"],
          "exercises": ["Übungsblatt 1 (1-3)"]
        }}
      ]
    }}
  ],
  "edges": [
    {{"from": "t1", "to": "t2"}},
    {{"from": "t1", "to": "t3"}},
    {{"from": "t2", "to": "t5"}}
  ]
}}

REGELN:

IDs & Konsistenz:
- Topic-IDs global eindeutig: t1, t2, t3, ... über alle Phasen. Nie wiederverwenden.
- Phase-IDs: ph1, ph2, ph3, ...
- Wenn alte Roadmap existiert: behalte vorhandene IDs und status-Werte.
  Neue Topics erhalten neue IDs (höher als die höchste vorhandene).

Status ("status"):
- "done": Thema wurde bereits erarbeitet (nur setzen wenn alte Roadmap diesen Status hat)
- "in_progress": Aktuell in Bearbeitung
- "open": Noch nicht begonnen (Default für neue Roadmaps)
- Status darf NUR aus alter Roadmap übernommen werden — erfinde keinen Fortschritt.

Zeitschätzung ("hours"):
- Nur ganze Zahlen (1, 2, 3, ...), keine Dezimalzahlen.
- Einfaches Faktenwissen / Definition: 1–2h
- Konzept mit Anwendung: 2–4h
- Komplexes Thema mit Beweisen / Implementierung: 4–8h

Prüfungsrelevanz ("relevance"):
- "high": direkt klausurrelevant laut Materialien, Klausurdateien oder typischem Prüfungsprofil
- "medium": notwendige Verständnisgrundlage für high-Topics
- "low": Hintergrundwissen / Kontext, selten direkt geprüft

Abhängigkeiten ("edges"):
- Jede Edge: {{"from": "tX", "to": "tY"}} — Pfeil von Voraussetzung zu aufbauendem Topic.
- Bilde echte topic-spezifische Abhängigkeiten ab, auch phasenübergreifend.
- Mindestens 1 eingehende Edge pro Topic das eine Voraussetzung hat.
- Keine Zyklen (DAG).
- Nicht alle Phasen müssen linear verbunden sein — cross-phase-Edges erlaubt.

Dateien & Aufgaben:
- "files": NUR Dateinamen aus VERFÜGBARE-DATEIEN-Liste. Erfinde keine. Kein Match → [].
- "exercises": konkrete Übungsblätter / Aufgaben aus dem Kontext.

Fokus:
- Wenn NUTZER-FOKUS angegeben: Direkt auf diesen Fokus ausrichten.
  Nicht-fokussierte Themen nur wenn sie direkte, unverzichtbare Voraussetzung eines high-Topics sind.
- Wenn KLAUSUR-DATEIEN vorhanden: Topics aus Klausuren → relevance "high".
  Phase "exam_training" referenziert Klausur-Dateien in "files".

Kein Kontext:
- Wenn KONTEXT leer: Generiere allgemeine Hochschul-Roadmap für "{modul}".
  "files": [] und "exercises": [] überall.

Umfang: 4–7 Phasen, 3–7 Topics pro Phase.

Antworte NUR mit dem JSON-Objekt."""


# ─────────────────────────────── Generation ─────────────────────────────────

def generate(
    module_name: str,
    *,
    exam_date: str = "",
    focus: str = "",
    course_context: str = "",
    exam_profile: str = "",
    old_md: str = "",
    available_files: Optional[List[str]] = None,
    exam_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Call the LLM and return the parsed JSON roadmap structure."""
    profile = mp.load(module_name) or {}

    sections: List[str] = [f"MODUL: {module_name}"]
    if exam_date:
        sections.append(f"PRÜFUNGSDATUM: {exam_date}")
    if profile.get("schwerpunkte"):
        sections.append("SCHWERPUNKTE: " + ", ".join(profile["schwerpunkte"]))
    if profile.get("pruefungsrelevant"):
        sections.append("PRÜFUNGSRELEVANTE THEMEN (aus Altklausur-Analyse): "
                        + ", ".join(profile["pruefungsrelevant"]))
    if exam_profile:
        sections.append("PRÜFUNGSPROFIL (Aufgabentypen, Häufigkeiten):\n" + exam_profile[:3000])
    sections.append("KURSINHALTE (aus den hochgeladenen Materialien):\n" + course_context[:6000])

    focus_section = ""
    if focus:
        focus_section = (
            f"\n⚠ NUTZER-FOKUS (höchste Priorität — Roadmap muss sich darauf konzentrieren):\n"
            f"{focus}\n"
        )

    exam_files_section = ""
    if exam_files:
        exam_list = "\n".join(f"  - {f}" for f in exam_files)
        exam_files_section = (
            "\n\nKLAUSUR-DATEIEN (vom Nutzer als Altklausuren markiert — "
            "Roadmap bereitet auf ähnliche Prüfung vor):\n" + exam_list + "\n"
        )

    files_section = ""
    if available_files:
        files_list = "\n".join(f"  - {f}" for f in available_files)
        files_section = (
            "\n\nVERFÜGBARE DATEIEN (NUR diese Namen dürfen in 'dateien' verwendet werden — "
            "keine anderen, keine erfundenen):\n" + files_list
        )

    old_section = ""
    if old_md:
        old_section = (
            "\n\nALTE ROADMAP (Topic-Namen + IDs erhalten wo möglich):\n```\n"
            + old_md[:4000] + "\n```"
        )

    prompt = _GENERATE_PROMPT.format(
        modul=module_name,
        user_focus_section=focus_section,
        context="\n\n".join(sections),
        exam_files_section=exam_files_section,
        available_files_section=files_section,
        old_roadmap_section=old_section,
    )

    response = _get_client().messages.create(
        model=MODEL,
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    # Strip markdown code fences if the LLM wraps the JSON
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text).strip()
    return json.loads(text)


# ─────────────────────── Hour scaling ──────────────────────────────────────

def scale_hours_to_exam_date(
    data: Dict[str, Any],
    exam_date: str,
    daily_study_hours: float = 8.0,
) -> Dict[str, Any]:
    """
    Rescale topic hours so they sum to (remaining_days × daily_study_hours).

    Uses the LLM's raw hours as proportional weights and distributes the
    total available study time (days × 8h) across topics as whole hours.
    """
    if not exam_date:
        return data

    try:
        exam_dt = date.fromisoformat(exam_date.strip())
    except ValueError:
        return data

    remaining_days = max(1, (exam_dt - date.today()).days)
    total_available = remaining_days * daily_study_hours

    all_topics = [
        topic
        for phase in (data.get("phases") or [])
        for topic in (phase.get("topics") or [])
    ]
    if not all_topics:
        return data

    total_raw = sum(max(float(t.get("hours") or 1.0), 0.1) for t in all_topics)

    for topic in all_topics:
        raw = max(float(topic.get("hours") or 1.0), 0.1)
        scaled = max(1, round(raw / total_raw * total_available))
        topic["hours"] = scaled

    return data


# ──────────────────────────── Markdown render ───────────────────────────────

def _safe_mermaid_label(name: str) -> str:
    """Mermaid breaks on `[]"` inside node labels — neutralize them."""
    return name.replace('[', '(').replace(']', ')').replace('"', "'").replace('\n', ' ').strip()


def render_md(module_name: str, data: Dict[str, Any]) -> str:
    """Render the JSON roadmap structure into our exact Markdown format."""
    today = date.today().isoformat()
    exam_date = (data.get("exam_date") or "").strip() or "—"
    phases = data.get("phases") or []
    edges = data.get("mermaid_edges") or []

    total = sum(len(p.get("topics") or []) for p in phases)

    out: List[str] = [
        f"# Lernplan: {module_name}",
        f"**Generiert:** {today} · **Prüfungsdatum:** {exam_date}",
        f"**Fortschritt:** 0/{total} fertig · 0 dran · {total} offen",
        "",
        "```mermaid",
        "flowchart TD",
    ]

    # Mermaid nodes — all start as todo
    for phase in phases:
        for topic in phase.get("topics") or []:
            tid = str(topic.get("id") or "").strip() or "tx"
            label = _safe_mermaid_label(str(topic.get("name") or ""))
            out.append(f'  {tid}["{label}"]:::todo')

    # Edges
    valid_ids = {str(t.get("id")) for p in phases for t in (p.get("topics") or [])}
    for edge in edges:
        if isinstance(edge, (list, tuple)) and len(edge) == 2:
            src, dst = str(edge[0]), str(edge[1])
            if src in valid_ids and dst in valid_ids:
                out.append(f"  {src} --> {dst}")

    # Click handlers (one per topic) — dispatched to selectTopic() in the frontend.
    for phase in phases:
        for topic in phase.get("topics") or []:
            tid = str(topic.get("id") or "").strip()
            if tid:
                out.append(f'  click {tid} call selectTopic("{tid}")')

    # Status classDefs
    out.extend([
        "  classDef todo fill:#475569,stroke:#334155,color:#cbd5e1",
        "  classDef doing fill:#f59e0b,stroke:#d97706,color:#fff",
        "  classDef done fill:#10b981,stroke:#059669,color:#fff",
        "```",
        "",
    ])

    # Phase sections
    for phase in phases:
        out.append(f"## {str(phase.get('title') or 'Phase').strip()}")
        out.append("")
        for topic in phase.get("topics") or []:
            tid = str(topic.get("id") or "").strip() or "tx"
            name = str(topic.get("name") or "").strip() or "(unbenannt)"
            # LLM returns 'relevance'; fall back to German key for old data
            prio = str(topic.get("relevance") or topic.get("pruefungsrelevanz") or "medium").strip()
            try:
                hours = max(1, round(float(topic.get("hours") or 1.0)))
            except (TypeError, ValueError):
                hours = 1
            out.append(f"### {name} <!-- id:{tid} status:todo prio:{prio} h:{hours} -->")
            # LLM returns 'summary'; fall back to 'bedeutung' for old data
            bedeutung = (topic.get("summary") or topic.get("bedeutung") or "").strip()
            if bedeutung:
                out.append(f"**Bedeutung:** {bedeutung}")
            # LLM returns 'exam_relevance_reason'; fall back to 'warum_relevant'
            warum = (topic.get("exam_relevance_reason") or topic.get("warum_relevant") or "").strip()
            if warum:
                out.append(f"**Warum relevant:** {warum}")
            subs = [str(s).strip() for s in (topic.get("subtopics") or []) if str(s).strip()]
            if subs:
                out.append("**Subtopics:** " + " · ".join(subs))
            # LLM returns 'files'; fall back to 'dateien'
            datnr = [str(d).strip() for d in (topic.get("files") or topic.get("dateien") or []) if str(d).strip()]
            if datnr:
                out.append("**Dateien:** " + ", ".join(datnr))
            # LLM returns 'exercises'; fall back to 'aufgaben'
            aufg = [str(a).strip() for a in (topic.get("exercises") or topic.get("aufgaben") or []) if str(a).strip()]
            if aufg:
                out.append("**Aufgaben:** " + ", ".join(aufg))
            out.append("")

    return "\n".join(out).rstrip() + "\n"


# ──────────────────────────── Markdown parsing ──────────────────────────────

_TOPIC_HEADING_RE = re.compile(
    r"^### (?P<name>.+?) <!-- id:(?P<id>\S+) status:(?P<status>todo|doing|done) "
    r"prio:(?P<prio>\S+) h:(?P<h>\S+) -->\s*$"
)
_PHASE_HEADING_RE = re.compile(r"^## (?P<title>.+)$")
_FIELD_LINE_RE = re.compile(r"^\*\*(?P<key>[\wäöüÄÖÜ ]+):\*\*\s+(?P<val>.+)$")
_META_LINE_RE = re.compile(
    r"^\*\*Generiert:\*\*\s+(?P<gen>\S+)\s*·\s*\*\*Prüfungsdatum:\*\*\s+(?P<exam>.+)$"
)
_PROGRESS_LINE_RE = re.compile(r"^\*\*Fortschritt:\*\*.*$")
_MERMAID_BLOCK_RE = re.compile(r"```mermaid\n(?P<body>.*?)\n```", re.DOTALL)


def parse_md(md: str) -> Dict[str, Any]:
    """Parse a roadmap markdown file into a structured dict."""
    result: Dict[str, Any] = {
        "exists": True,
        "exam_date": "",
        "generated_at": "",
        "progress": {"todo": 0, "doing": 0, "done": 0},
        "mermaid": "",
        "phases": [],
        "raw_md": md,
    }

    mb = _MERMAID_BLOCK_RE.search(md)
    if mb:
        result["mermaid"] = mb.group("body")

    in_mermaid = False
    current_phase: Optional[Dict[str, Any]] = None
    current_topic: Optional[Dict[str, Any]] = None

    for line in md.splitlines():
        if line.startswith("```mermaid"):
            in_mermaid = True
            continue
        if line.startswith("```"):
            in_mermaid = False
            continue
        if in_mermaid:
            continue

        meta = _META_LINE_RE.match(line)
        if meta:
            result["generated_at"] = meta.group("gen").strip()
            ed = meta.group("exam").strip()
            result["exam_date"] = "" if ed == "—" else ed
            continue

        ph = _PHASE_HEADING_RE.match(line)
        if ph:
            current_phase = {"title": ph.group("title").strip(), "topics": []}
            result["phases"].append(current_phase)
            current_topic = None
            continue

        tm = _TOPIC_HEADING_RE.match(line)
        if tm and current_phase is not None:
            try:
                hours = max(1, round(float(tm.group("h"))))
            except ValueError:
                hours = 1
            current_topic = {
                "id": tm.group("id"),
                "name": tm.group("name").strip(),
                "status": tm.group("status"),
                "pruefungsrelevanz": tm.group("prio"),
                "hours": hours,
                "bedeutung": "",
                "warum_relevant": "",
                "subtopics": [],
                "dateien": [],
                "aufgaben": [],
            }
            current_phase["topics"].append(current_topic)
            result["progress"][current_topic["status"]] += 1
            continue

        if current_topic is not None:
            f = _FIELD_LINE_RE.match(line)
            if f:
                key = f.group("key").strip().lower()
                val = f.group("val").strip()
                if key == "bedeutung":
                    current_topic["bedeutung"] = val
                elif key == "warum relevant":
                    current_topic["warum_relevant"] = val
                elif key == "subtopics":
                    current_topic["subtopics"] = [s.strip() for s in val.split("·") if s.strip()]
                elif key == "dateien":
                    current_topic["dateien"] = [s.strip() for s in val.split(",") if s.strip()]
                elif key == "aufgaben":
                    current_topic["aufgaben"] = [s.strip() for s in val.split(",") if s.strip()]

    return result


# ─────────────────────── Status updates (in-place edits) ────────────────────

def _refresh_progress_line(md: str) -> str:
    parsed = parse_md(md)
    p = parsed["progress"]
    total = p["todo"] + p["doing"] + p["done"]
    line = f"**Fortschritt:** {p['done']}/{total} fertig · {p['doing']} dran · {p['todo']} offen"
    return _PROGRESS_LINE_RE.sub(line, md, count=1)


def update_topic_status(md: str, topic_id: str, new_status: str) -> str:
    """Update a topic's status in both the topic heading comment AND the Mermaid classDef."""
    if new_status not in ("todo", "doing", "done"):
        raise ValueError(f"Invalid status: {new_status}")

    # 1) Topic heading comment
    head_pat = re.compile(
        rf"(^### .+ <!-- id:{re.escape(topic_id)} status:)(?:todo|doing|done)"
        rf"( prio:\S+ h:\S+ -->\s*$)",
        re.MULTILINE,
    )
    md = head_pat.sub(rf"\g<1>{new_status}\g<2>", md)

    # 2) Mermaid classDef on this node line: e.g.  `  t2["Pattern"]:::todo`
    merm_pat = re.compile(
        rf"^(\s*{re.escape(topic_id)}\[[^\]]*\]):::(?:todo|doing|done)\s*$",
        re.MULTILINE,
    )
    md = merm_pat.sub(rf"\g<1>:::{new_status}", md)

    # 3) Aggregate progress
    return _refresh_progress_line(md)


# ──────────────────────────────── Smart merge ───────────────────────────────

_NORM_RE = re.compile(r"\W+", re.UNICODE)


def _normalize_name(s: str) -> str:
    return _NORM_RE.sub("", (s or "").lower())


def merge_status(old_md: str, new_md: str) -> Tuple[str, Dict[str, Any]]:
    """
    Carry over done/doing status from old_md → new_md by matching topic names.
    Returns (merged_md, diff_info).
    """
    old = parse_md(old_md)
    new = parse_md(new_md)

    # Lookup of old non-todo statuses by normalized topic name
    old_status: Dict[str, str] = {}
    for phase in old["phases"]:
        for topic in phase["topics"]:
            if topic["status"] in ("done", "doing"):
                old_status[_normalize_name(topic["name"])] = topic["status"]

    # Walk new_md line by line; on each topic-heading, if name matches an old non-todo, carry the status.
    new_lines = new_md.splitlines()
    preserved = 0
    preserved_ids: List[Tuple[str, str]] = []  # (id, status) to update mermaid afterwards

    for i, line in enumerate(new_lines):
        m = _TOPIC_HEADING_RE.match(line)
        if not m:
            continue
        norm = _normalize_name(m.group("name"))
        if norm in old_status:
            new_status = old_status[norm]
            new_lines[i] = (
                f"### {m.group('name')} <!-- id:{m.group('id')} status:{new_status} "
                f"prio:{m.group('prio')} h:{m.group('h')} -->"
            )
            preserved += 1
            preserved_ids.append((m.group("id"), new_status))

    merged = "\n".join(new_lines)
    if not merged.endswith("\n"):
        merged += "\n"

    # Sync mermaid classDefs for the carried-over topics
    for tid, st in preserved_ids:
        merm_pat = re.compile(
            rf"^(\s*{re.escape(tid)}\[[^\]]*\]):::(?:todo|doing|done)\s*$",
            re.MULTILINE,
        )
        merged = merm_pat.sub(rf"\g<1>:::{st}", merged)

    merged = _refresh_progress_line(merged)

    old_names = {_normalize_name(t["name"]) for p in old["phases"] for t in p["topics"]}
    new_names = {_normalize_name(t["name"]) for p in new["phases"] for t in p["topics"]}
    diff = {
        "added_count": len(new_names - old_names),
        "removed_count": len(old_names - new_names),
        "status_preserved": preserved,
    }
    return merged, diff


# ───────────────────────────── Storage operations ──────────────────────────────

def _slug_for(module_name: str) -> str:
    profile = mp.load(module_name)
    return profile["slug"] if profile else mp._slugify(module_name)


def load_roadmap_md(module_name: str) -> Optional[str]:
    from app.storage import storage_backend as sb
    slug = _slug_for(module_name)
    return sb.read_text(f"{slug}/roadmap.md")


def save_roadmap_md(module_name: str, md: str) -> str:
    from app.storage import storage_backend as sb
    slug = _slug_for(module_name)
    path = f"{slug}/roadmap.md"
    sb.write_text(path, md)
    return path


def delete_roadmap(module_name: str) -> bool:
    from app.storage import storage_backend as sb
    slug = _slug_for(module_name)
    return sb.delete(f"{slug}/roadmap.md")
