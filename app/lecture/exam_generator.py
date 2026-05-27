"""
lecture/exam_generator.py
Generiert Probeklausuren per zweistufigem Claude-Sonnet-Call.
"""
import re
from datetime import date
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


def _storage_path(module_name: str, exam_n: int) -> str:
    return f"{_slug(module_name)}/exams/exam_{exam_n}.md"


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
    """Upload exam to Supabase Storage and record in exams table. Returns exam_n."""
    from app.storage import storage_backend as sb
    from app.storage.supabase_client import get_client, get_user_id
    from app.lecture import module_profile as mp

    exam_n = _next_exam_n(module_name)
    storage_path = _storage_path(module_name, exam_n)
    sb.write_text(storage_path, md_content)

    profile = mp.load(module_name)
    if profile and profile.get("id"):
        meta = _parse_frontmatter(md_content)
        try:
            get_client().table("exams").insert({
                "user_id":     get_user_id(),
                "module_id":   profile["id"],
                "storage_path": storage_path,
                "exam_n":      exam_n,
                "tasks_count": int(meta.get("num_tasks", 0) or 0),
                "total_points": int(meta.get("total_points", 0) or 0),
            }).execute()
        except Exception:
            pass
    return exam_n


def load_exam(module_name: str, n: int) -> Optional[str]:
    from app.storage import storage_backend as sb
    return sb.read_text(_storage_path(module_name, n))


def list_exams(module_name: str) -> list:
    """Returns [{n, generated, num_tasks, total_points}] from exams table or storage scan."""
    from app.storage.supabase_client import get_client, get_user_id
    from app.lecture import module_profile as mp

    profile = mp.load(module_name)
    if profile and profile.get("id"):
        try:
            rows = (
                get_client()
                .table("exams")
                .select("exam_n, tasks_count, total_points, created_at")
                .eq("module_id", profile["id"])
                .order("exam_n")
                .execute()
            ).data or []
            return [
                {
                    "n": r["exam_n"],
                    "generated": str(r.get("created_at", ""))[:10],
                    "num_tasks": r.get("tasks_count", 0),
                    "total_points": r.get("total_points", 0),
                }
                for r in rows
            ]
        except Exception:
            pass

    # Fallback: scan storage
    from app.storage import storage_backend as sb
    slug = _slug(module_name)
    paths = sb.list_prefix(f"{slug}/exams/")
    exams = []
    for path in paths:
        m = re.search(r"exam_(\d+)\.md$", path)
        if not m:
            continue
        n = int(m.group(1))
        content = sb.read_text(path)
        meta = _parse_frontmatter(content or "")
        exams.append({
            "n": n,
            "generated": meta.get("generated", ""),
            "num_tasks": int(meta.get("num_tasks", 0) or 0),
            "total_points": int(meta.get("total_points", 0) or 0),
        })
    return sorted(exams, key=lambda x: x["n"])


def delete_exam(module_name: str, n: int) -> bool:
    """Delete exam from storage and DB."""
    from app.storage import storage_backend as sb
    from app.storage.supabase_client import get_client, get_user_id
    from app.lecture import module_profile as mp

    storage_path = _storage_path(module_name, n)
    deleted = sb.delete(storage_path)

    profile = mp.load(module_name)
    if profile and profile.get("id"):
        try:
            get_client().table("exams").delete().eq("module_id", profile["id"]).eq("exam_n", n).execute()
        except Exception:
            pass
    return deleted
