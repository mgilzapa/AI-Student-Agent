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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from . import module_profile as mp

logger = logging.getLogger(__name__)
MODEL = "gpt-4o-mini"

_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client

ROADMAPS_DIR = Path("data/processed/roadmaps")


# ─────────────────────────── Generation prompt ──────────────────────────────

_GENERATE_PROMPT = """Du bist ein erfahrener universitärer Lerncoach.
Erstelle eine systematische, hierarchische Lern-Roadmap als JSON für Modul "{modul}".

KONTEXT
{context}
{old_roadmap_section}

PHASEN-REIHENFOLGE (lass weg was nicht passt):
voraussetzungen → grundlagen → kernkonzepte → methoden → uebung → klausurtraining → typische_fehler → wiederholung

Themen aus dem PRÜFUNGSPROFIL und der PRÜFUNGSRELEVANTE-Liste haben automatisch hohe Priorität.

ANTWORT-FORMAT (NUR valides JSON, keine Markdown-Codeblöcke, kein Kommentar):

{{
  "exam_date": "YYYY-MM-DD oder leer",
  "phases": [
    {{
      "title": "Phase 1 · Voraussetzungen",
      "topics": [
        {{
          "id": "t1",
          "name": "Logik-Grundlagen",
          "pruefungsrelevanz": "hoch|mittel|niedrig",
          "hours": 1.5,
          "bedeutung": "Was das Thema ist (1 Satz)",
          "warum_relevant": "Warum prüfungsrelevant (1 Satz)",
          "subtopics": ["Unterthema 1", "Unterthema 2"],
          "dateien": ["Skript Kap.1.pdf"],
          "aufgaben": ["Übung 1 (1-3)"]
        }}
      ]
    }}
  ],
  "mermaid_edges": [
    ["t1", "t2"],
    ["t1", "t3"]
  ]
}}

REGELN:
- Topic-IDs sind eindeutig: t1, t2, t3, ... über alle Phasen hinweg.
- Wenn alte Roadmap existiert: behalte vorhandene Topic-Namen + IDs wo möglich, damit Status mitwandert.
- "dateien" sollen REALE Dateinamen aus dem Kontext sein (Skript, Übung, Klausur, …).
- "aufgaben" referenzieren konkrete Übungsblätter / Aufgaben aus dem Kontext.
- mermaid_edges definiert die Lernreihenfolge — Pfeile von Voraussetzungen zu darauf aufbauenden Topics.
  Mindestens jede Phase mit der nächsten verknüpfen, plus topic-spezifische Bezüge wenn sinnvoll.
- 4-7 Phasen, 3-7 Topics pro Phase, je nach Materialumfang.
- Antworte NUR mit dem JSON-Objekt."""


# ─────────────────────────────── Generation ─────────────────────────────────

def generate(
    module_name: str,
    *,
    exam_date: str = "",
    focus: str = "",
    course_context: str = "",
    exam_profile: str = "",
    old_md: str = "",
) -> Dict[str, Any]:
    """Call the LLM and return the parsed JSON roadmap structure."""
    profile = mp.load(module_name) or {}

    sections: List[str] = [f"MODUL: {module_name}"]
    if exam_date:
        sections.append(f"PRÜFUNGSDATUM: {exam_date}")
    if focus:
        sections.append(f"FOKUS / WORAUF VORBEREITEN: {focus}")
    if profile.get("schwerpunkte"):
        sections.append("SCHWERPUNKTE: " + ", ".join(profile["schwerpunkte"]))
    if profile.get("pruefungsrelevant"):
        sections.append("PRÜFUNGSRELEVANTE THEMEN (aus Altklausur-Analyse): "
                        + ", ".join(profile["pruefungsrelevant"]))
    if exam_profile:
        sections.append("PRÜFUNGSPROFIL (Aufgabentypen, Häufigkeiten):\n" + exam_profile[:3000])
    sections.append("KURSINHALTE (aus den hochgeladenen Materialien):\n" + course_context[:6000])

    old_section = ""
    if old_md:
        old_section = (
            "\n\nALTE ROADMAP (Topic-Namen + IDs erhalten wo möglich):\n```\n"
            + old_md[:4000] + "\n```"
        )

    prompt = _GENERATE_PROMPT.format(
        modul=module_name,
        context="\n\n".join(sections),
        old_roadmap_section=old_section,
    )

    response = _get_client().chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        max_tokens=4000,
    )
    return json.loads(response.choices[0].message.content)


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
            prio = str(topic.get("pruefungsrelevanz") or "mittel").strip()
            try:
                hours = float(topic.get("hours") or 1.0)
            except (TypeError, ValueError):
                hours = 1.0
            out.append(f"### {name} <!-- id:{tid} status:todo prio:{prio} h:{hours} -->")
            if topic.get("bedeutung"):
                out.append(f"**Bedeutung:** {topic['bedeutung'].strip()}")
            if topic.get("warum_relevant"):
                out.append(f"**Warum relevant:** {topic['warum_relevant'].strip()}")
            subs = [str(s).strip() for s in (topic.get("subtopics") or []) if str(s).strip()]
            if subs:
                out.append("**Subtopics:** " + " · ".join(subs))
            datnr = [str(d).strip() for d in (topic.get("dateien") or []) if str(d).strip()]
            if datnr:
                out.append("**Dateien:** " + ", ".join(datnr))
            aufg = [str(a).strip() for a in (topic.get("aufgaben") or []) if str(a).strip()]
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
                hours = float(tm.group("h"))
            except ValueError:
                hours = 1.0
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


# ───────────────────────────── File operations ──────────────────────────────

def roadmap_path(module_name: str) -> Path:
    profile = mp.load(module_name)
    slug = profile["slug"] if profile else mp._slugify(module_name)
    return ROADMAPS_DIR / slug / f"{slug}.roadmap.md"


def load_roadmap_md(module_name: str) -> Optional[str]:
    p = roadmap_path(module_name)
    return p.read_text(encoding="utf-8") if p.exists() else None


def save_roadmap_md(module_name: str, md: str) -> Path:
    p = roadmap_path(module_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(md, encoding="utf-8")
    return p


def delete_roadmap(module_name: str) -> bool:
    p = roadmap_path(module_name)
    if p.exists():
        p.unlink()
        return True
    return False
