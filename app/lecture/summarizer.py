"""
lecture/summarizer.py
Zwei-Stufen-Generierung: erst Konzept-Extraktion, dann tiefe Zusammenfassung.
"""

import json
from pathlib import Path
from openai import OpenAI

from . import module_profile as mp

client = OpenAI()
MODEL = "gpt-4o-mini"

# ── Stufe 1: Konzept-Extraktion ───────────────────────────────────────────────

STAGE1_PROMPT = """Analysiere diesen Vorlesungsinhalt für das Modul "{modul}".

MODUL-PROFIL:
- Schwerpunkte: {schwerpunkte}
- Prüfungsrelevante Themen (aus Altklausuren): {pruefungsrelevant}
- Stil: {stil}
{prompt_hint}

VORLESUNGSINHALT:
{inhalt}

VORHERIGE VORLESUNGEN (roter Faden):
{history}

Antworte NUR mit einem JSON-Objekt:
{{
  "titel": "Kurztitel dieser Vorlesung",
  "konzepte": [
    {{
      "name": "Konzeptname",
      "pruefungsrelevanz": "hoch|mittel|niedrig",
      "baut_auf": "Vorheriges Konzept oder leer",
      "kernaussage": "Ein Satz was das Konzept ist"
    }}
  ],
  "verbindung_vorherige": "Wie hängt das mit letzter VL zusammen (1 Satz)"
}}

Maximal 7 Konzepte. Sortiere nach Lernreihenfolge (nicht nach Relevanz).
"""

# ── Stufe 2: Tiefe Zusammenfassung ────────────────────────────────────────────

STAGE2_PROMPT = """Erstelle eine Vorlesungszusammenfassung für Modul "{modul}".

KONZEPT-STRUKTUR (aus Analyse):
{konzepte_json}

MODUL-STIL: {stil}
{prompt_hint}

PRÜFUNGSPROFIL:
{exam_profile}

VORLESUNGSINHALT:
{inhalt}

Erstelle die Zusammenfassung nach diesem exakten Template:

# {titel}
**Modul:** {modul} | **Niveau:** [erkenne aus Inhalt]

---

## 🎯 Lernziele
[3-5 konkrete Lernziele aus dem Inhalt ableiten]

---

## 🧭 Überblick
[2-3 Sätze roter Faden + Verbindung zur letzten Vorlesung: {verbindung_vorherige}]

---

## 📖 Hauptinhalt

[Für jedes Konzept aus der Struktur:]

### [Konzeptname]
[Tiefe abhängig von Prüfungsrelevanz:]

WENN hoch:
- Intuition (2-3 Sätze, Analogie/Alltagsbeispiel)
- Formale Definition / Algorithmus (mit Schritten)
- Typische Prüfungsaufgabe aus dem Prüfungsprofil ableiten + Musterlösung
- > ⚠️ Häufiger Fehler: [was Studenten oft falsch machen]

WENN mittel:
- Erklärung (3-4 Sätze)
- Konkretes Beispiel
- > 💡 Merke: [Kernaussage]

WENN niedrig:
- 2-3 Sätze Überblick

---

## 🔗 Konzeptübersicht
[ASCII-Diagramm oder Tabelle mit Zusammenhängen zwischen den Konzepten]

---

## ❓ Verständnisfragen
[3 Fragen die Verständnis testen, nicht Auswendiglernen.
Bei hoher Prüfungsrelevanz: prüfungsnahe Formulierung nutzen]

---

## 🗝️ Schlüsselbegriffe
| Begriff | Bedeutung |
|--------|-----------|
[Alle Konzepte + wichtige Fachbegriffe]

---

## 📝 TL;DR
[3-5 Sätze: das Wesentliche. Jemand der nur das liest soll den Kern verstehen.]

STILREGELN:
- Erklärender Fließtext, nicht reine Bullet-Listen
- Fachbegriffe beim ersten Auftreten **fett**
- Explizite Übergänge zwischen Abschnitten ("Aufbauend darauf...", "Das führt uns zu...")
- Ton: wie ein guter Dozent der wirklich erklären will
"""


# ── Public API ─────────────────────────────────────────────────────────────────

def summarize(modul_name: str, vorlesungsinhalt: str) -> dict:
    """
    Gibt zurück: {
        "summary": str,          # fertige Zusammenfassung (Markdown)
        "konzepte": list,         # extrahierte Konzepte aus Stufe 1
        "titel": str,
        "saved_to": str,          # Pfad wo gespeichert
    }
    """
    profile = mp.load(modul_name) or _default_profile(modul_name)
    history = mp.load_history(profile)
    exam_profile = mp.load_exam_profile(profile)

    # ── Stufe 1 ──────────────────────────────────────────────────────────────
    stage1_result = _run_stage1(vorlesungsinhalt, profile, history)
    konzepte = stage1_result.get("konzepte", [])
    titel = stage1_result.get("titel", "Vorlesung")
    verbindung = stage1_result.get("verbindung_vorherige", "")

    # ── Stufe 2 ──────────────────────────────────────────────────────────────
    summary = _run_stage2(
        vorlesungsinhalt=vorlesungsinhalt,
        profile=profile,
        konzepte=konzepte,
        titel=titel,
        verbindung_vorherige=verbindung,
        exam_profile=exam_profile,
    )

    # ── History aktualisieren ─────────────────────────────────────────────────
    konzept_namen = [k["name"] for k in konzepte]
    mp.append_history(profile, titel, konzept_namen, verbindung)

    # ── Zusammenfassung speichern ─────────────────────────────────────────────
    saved_to = _save_summary(profile, titel, summary)

    return {
        "summary": summary,
        "konzepte": konzepte,
        "titel": titel,
        "saved_to": saved_to,
    }


# ── Interne Hilfsfunktionen ───────────────────────────────────────────────────

def _run_stage1(inhalt: str, profile: dict, history: str) -> dict:
    prompt = STAGE1_PROMPT.format(
        modul=profile["name"],
        schwerpunkte=", ".join(profile.get("schwerpunkte", [])) or "nicht angegeben",
        pruefungsrelevant=", ".join(profile.get("pruefungsrelevant", [])) or "nicht angegeben",
        stil=profile.get("stil", "mixed"),
        prompt_hint=f"- Hinweis: {profile['prompt_hint']}" if profile.get("prompt_hint") else "",
        inhalt=inhalt[:4000],
        history=history[-1500:] if history else "Erste Vorlesung dieses Moduls.",
    )

    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.choices[0].message.content.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        import re
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(match.group()) if match else {"konzepte": [], "titel": "Vorlesung"}


def _run_stage2(
    vorlesungsinhalt: str,
    profile: dict,
    konzepte: list,
    titel: str,
    verbindung_vorherige: str,
    exam_profile: str,
) -> str:
    prompt = STAGE2_PROMPT.format(
        modul=profile["name"],
        konzepte_json=json.dumps(konzepte, ensure_ascii=False, indent=2),
        stil=profile.get("stil", "mixed"),
        prompt_hint=f"Zusätzlicher Stilhinweis: {profile['prompt_hint']}" if profile.get("prompt_hint") else "",
        exam_profile=exam_profile or "Kein Prüfungsprofil vorhanden.",
        inhalt=vorlesungsinhalt[:6000],
        titel=titel,
        verbindung_vorherige=verbindung_vorherige or "Erste Vorlesung / kein Bezug.",
    )

    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content.strip()


def _save_summary(profile: dict, titel: str, summary: str) -> str:
    from datetime import date
    import re
    safe_titel = re.sub(r"[^a-z0-9]+", "-", titel.lower())[:40]
    out_dir = Path("data/processed/summaries") / profile["slug"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{date.today()}_{safe_titel}.md"
    path.write_text(summary, encoding="utf-8")
    return str(path)


def _default_profile(modul_name: str) -> dict:
    """Fallback wenn kein Profil vorhanden."""
    return {
        "name": modul_name,
        "slug": modul_name.lower(),
        "schwerpunkte": [],
        "pruefungsrelevant": [],
        "stil": "mixed",
        "prompt_hint": "",
        "exam_profile": "",
        "history": "",
    }
