"""
lecture/exam_analyzer.py
Analysiert Altklausuren und erstellt ein Prüfungsprofil.
Nutzt einen einzigen Claude-API-Call pro Klausur-Batch.
"""

import json
import re
from pathlib import Path
from openai import OpenAI

from . import module_profile as mp

client = OpenAI()
MODEL = "gpt-4o"

EXAM_ANALYSIS_PROMPT = """Du analysierst {n} Altklausur(en) für das Modul "{modul}".

KLAUSUREN:
{klausuren_text}

Antworte NUR mit einem JSON-Objekt (kein Markdown, keine Erklärung):
{{
  "themen": [
    {{
      "name": "Themenname",
      "auftreten": <Anzahl in wie vielen Klausuren>,
      "aufgabentypen": ["Berechnung", "Beweis", "Erklärung", "Trace", "Multiple Choice"],
      "relevanz": "hoch|mittel|niedrig"
    }}
  ],
  "typische_formulierungen": ["Formulierung 1", "Formulierung 2"],
  "nie_gefragt": ["Thema das in VL vorkommt aber nie in Prüfung"]
}}

Sortiere themen nach auftreten (absteigend).
Relevanz: hoch = >60% der Klausuren, mittel = 30-60%, niedrig = <30%.
"""


def analyze(modul_name: str, klausur_texts: list[str]) -> str:
    """
    modul_name     : Name des Moduls
    klausur_texts  : Liste von Klausur-Volltexten
    Gibt Pfad zum gespeicherten Prüfungsprofil zurück.
    """
    profile = mp.load(modul_name)
    if not profile:
        raise ValueError(f"Modul '{modul_name}' nicht gefunden. Erst Onboarding durchführen.")

    n = len(klausur_texts)
    klausuren_text = "\n\n---KLAUSUR TRENNER---\n\n".join(
        f"[Klausur {i+1}]\n{t[:3000]}" for i, t in enumerate(klausur_texts)
    )

    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=1500,
        messages=[{
            "role": "user",
            "content": EXAM_ANALYSIS_PROMPT.format(
                n=n,
                modul=modul_name,
                klausuren_text=klausuren_text,
            )
        }]
    )

    raw = response.choices[0].message.content.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        data = json.loads(match.group()) if match else {}

    exam_profile_md = _render_exam_profile(profile["name"], n, data)

    # Speichern
    exam_path = Path(profile["exam_profile"])
    exam_path.parent.mkdir(parents=True, exist_ok=True)
    exam_path.write_text(exam_profile_md, encoding="utf-8")

    # pruefungsrelevant im Profil aktualisieren
    top_topics = [t["name"] for t in data.get("themen", []) if t.get("relevanz") == "hoch"]
    mp.update_exam_topics(profile["slug"], top_topics)

    return str(exam_path)


def _render_exam_profile(modul: str, n: int, data: dict) -> str:
    lines = [
        f"# Prüfungsprofil: {modul}",
        f"Analysiert: {n} Klausur(en)\n",
        "## Themen nach Häufigkeit\n",
        "| Thema | Auftreten | Aufgabentypen | Relevanz |",
        "|-------|-----------|---------------|----------|",
    ]

    icons = {"hoch": "🔴", "mittel": "🟡", "niedrig": "🟢"}
    for t in data.get("themen", []):
        typen = ", ".join(t.get("aufgabentypen", []))
        icon = icons.get(t.get("relevanz", "niedrig"), "⚪")
        lines.append(f"| {t['name']} | {t['auftreten']}/{n} | {typen} | {icon} {t['relevanz']} |")

    if data.get("typische_formulierungen"):
        lines += ["\n## Typische Aufgabenformulierungen\n"]
        for f in data["typische_formulierungen"]:
            lines.append(f'- "{f}"')

    if data.get("nie_gefragt"):
        lines += ["\n## Nie gefragt (trotz Vorlesungsinhalt)\n"]
        for t in data["nie_gefragt"]:
            lines.append(f"- {t}")

    return "\n".join(lines)
