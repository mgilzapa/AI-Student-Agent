"""
lecture/exam_generator.py
Generiert Probeklausuren per zweistufigem Claude-Sonnet-Call.
"""
import re
from datetime import date
from pathlib import Path
from typing import Optional

from anthropic import Anthropic

client = Anthropic()
MODEL = "claude-sonnet-4-6"

_STYLE_PROMPT = """Analysiere diese Klausur(en) für das Modul "{modul}".

KLAUSUREN:
{klausuren_text}

Beschreibe in 3-5 Sätzen den Klausurstil:
- Typische Aufgabentypen (Beweis, Berechnung, Multiple Choice, Erkläre…)
- Punkteverteilung
- Aufbau und Reihenfolge
- Formulierungsgewohnheiten

Antworte als knapper Fließtext, kein JSON."""

_GENERATE_PROMPT = """Generiere eine Probeklausur für das Modul "{modul}".

{style_section}

INHALT AUS DEN VORLESUNGSMATERIALIEN:
{rag_context}

ANFORDERUNGEN:
- {num_tasks} Aufgaben
- Gesamtpunkte: {total_points}
- Verteile Punkte sinnvoll (höhere Punkte = komplexere Aufgaben)

AUSGABE-FORMAT (exakt so):
---
module: {modul}
generated: {today}
num_tasks: {num_tasks}
total_points: {total_points}
exam_n: {exam_n}
---

# Probeklausur {exam_n} — {modul}

**Gesamtpunkte:** {total_points} | **Aufgaben:** {num_tasks}

---

## Aufgabe 1 (X Punkte)

[Aufgabentext]

:::solution
**Musterlösung:**

[Lösung]
:::

---

[weitere Aufgaben im gleichen Muster]

REGELN:
- :::solution muss auf einer eigenen Zeile stehen, ::: (allein) beendet den Block
- Nur Inhalte aus den bereitgestellten Materialien verwenden
- Mathematische Ausdrücke in LaTeX ($...$ inline, $$...$$ display)"""


def _slug(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[äöüß]", lambda m: {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}[m.group()], s)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _exams_dir(module_name: str) -> Path:
    return Path("data/processed/exams") / _slug(module_name)


def _parse_frontmatter(md: str) -> dict:
    meta: dict = {}
    if not md.startswith("---"):
        return meta
    end = md.find("---", 3)
    if end == -1:
        return meta
    for line in md[3:end].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()
    return meta


def _next_exam_n(module_name: str) -> int:
    existing = list_exams(module_name)
    if not existing:
        return 1
    used = {e["n"] for e in existing}
    for n in range(1, 101):
        if n not in used:
            return n
    raise ValueError("Alle Klausurplätze (1–100) belegt. Bitte alte Klausuren löschen.")


def analyze_exam_style(exam_texts: list[str], module_name: str = "") -> str:
    """Schritt 1 (optional): Analysiert Klausurstil aus Altklausur-Texten."""
    if not exam_texts:
        return ""
    klausuren_text = "\n\n---KLAUSUR---\n\n".join(
        f"[Klausur {i+1}]\n{t[:3000]}" for i, t in enumerate(exam_texts)
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        temperature=0,
        messages=[{
            "role": "user",
            "content": _STYLE_PROMPT.format(modul=module_name, klausuren_text=klausuren_text),
        }],
    )
    return response.content[0].text.strip()


def generate(
    module_name: str,
    exam_style: str,
    rag_context: str,
    num_tasks: int,
    total_points: int,
) -> str:
    """Schritt 2: Generiert Probeklausur + Musterlösungen als .md-String."""
    exam_n = _next_exam_n(module_name)
    style_section = (
        f"KLAUSURSTIL (aus Altklausuren):\n{exam_style}"
        if exam_style
        else "KLAUSURSTIL: Keine Altklausuren — verwende einen akademischen Stil."
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=8096,
        temperature=0.3,
        messages=[{
            "role": "user",
            "content": _GENERATE_PROMPT.format(
                modul=module_name,
                style_section=style_section,
                rag_context=rag_context[:8000],
                num_tasks=num_tasks,
                total_points=total_points,
                today=date.today().isoformat(),
                exam_n=exam_n,
            ),
        }],
    )
    return response.content[0].text.strip()


def save_exam(module_name: str, md_content: str) -> int:
    """Speichert Klausur unter data/processed/exams/{slug}/exam_{n}.md. Gibt n zurück."""
    exam_n = _next_exam_n(module_name)
    out_dir = _exams_dir(module_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"exam_{exam_n}.md").write_text(md_content, encoding="utf-8")
    return exam_n


def load_exam(module_name: str, n: int) -> Optional[str]:
    """Liest Klausur n. Gibt None zurück wenn nicht vorhanden."""
    path = _exams_dir(module_name) / f"exam_{n}.md"
    return path.read_text(encoding="utf-8") if path.exists() else None


def list_exams(module_name: str) -> list[dict]:
    """Gibt [{n, generated, num_tasks, total_points}] für alle gespeicherten Klausuren zurück."""
    out_dir = _exams_dir(module_name)
    if not out_dir.exists():
        return []
    exams = []
    for path in sorted(out_dir.glob("exam_*.md")):
        m = re.match(r"exam_(\d+)\.md$", path.name)
        if not m:
            continue
        meta = _parse_frontmatter(path.read_text(encoding="utf-8"))
        exams.append({
            "n": int(m.group(1)),
            "generated": meta.get("generated", ""),
            "num_tasks": int(meta.get("num_tasks", 0) or 0),
            "total_points": int(meta.get("total_points", 0) or 0),
        })
    return exams


def delete_exam(module_name: str, n: int) -> bool:
    """Löscht Klausur n. Gibt True zurück wenn gelöscht."""
    path = _exams_dir(module_name) / f"exam_{n}.md"
    if path.exists():
        path.unlink()
        return True
    return False
