"""
lecture/summarizer.py
Zwei-Stufen-Generierung: erst Konzept-Extraktion, dann tiefe Zusammenfassung.
"""

import json
import anthropic

from . import module_profile as mp

client = anthropic.Anthropic()
MODEL = "claude-haiku-4-5-20251001"

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

Die Konzepte sollten die wichtigsten Themen und Ideen der Vorlesung sein, besonders im Hinblick auf die Prüfungsrelevanz.
"""

# ── Stufe 2: Tiefe Zusammenfassung ────────────────────────────────────────────

STAGE2_PROMPT = """Du bist ein Dozent für das Modul {modul} und willst eine tiefe Zusammenfassung der Vorlesung erstellen. 
Die Zusammenfassung soll den Studierenden wirklich helfen, die Konzepte zu verstehen und sich auf die Prüfung vorzubereiten.

Du bekommst folgende Informationen:
<konzepte_json>
{konzepte_json}
</konzepte_json>

<modul_profile>
{stil}
{prompt_hint}
</modul_profile>

<exam_profile>
{exam_profile}
</exam_profile>

<vorlesungsinhalt>
{inhalt}
</vorlesungsinhalt>

Hier sind wichtige Regeln für die Zusammenfassung:
- Bleib immer in deiner Rolle als Dozent, der wirklich erklären will.
- Erkläre die Konzepte so, dass sie für Studierende verständlich sind, die das Thema zum ersten Mal lernen.
- Nutze die Informationen aus dem Vorlesungsinhalt, aber auch dein "Dozentenwissen", um die Konzepte zu erklären.
- Verknüpfe die Konzepte untereinander und mit dem Vorlesungsinhalt, damit die Studierenden den roten Faden erkennen.
- Berücksichtige die Prüfungsrelevanz der Konzepte, aber erkläre auch die weniger relevanten, damit das Gesamtverständnis stimmt.   
- Nutze die Informationen aus dem Prüfungsprofil, um die Zusammenfassung auf die Prüfung vorzubereiten.
- Erklare alle Große Themen, die in der Vorlesung behandelt wurden, auch wenn sie nicht explizit als Konzepte extrahiert wurden.
- Nutze Beispiele, Analogien und einfache Erklärungen, um die Konzepte verständlich zu machen.
- Mathematische Formeln immer korrekt in LaTeX setzen, damit sie klar und professionell aussehen.
- Vermeide es, einfach nur den Vorlesungsinhalt umzustrukturieren. Füge echtes "Dozentenwissen" hinzu, um die Konzepte zu erklären und zu verknüpfen.
- Fachbegriffe beim ersten Auftreten **fett** markieren, damit die Studierenden sie leicht erkennen können.

Antworte mit einer ausführlichen, gut strukturierten Zusammenfassung im Markdown-Format, die die Konzepte erklärt, Beispiele gibt und die Verbindungen 
zwischen den Themen herstellt. Die Zusammenfassung soll den Studierenden wirklich helfen, die Vorlesung zu verstehen und sich auf die Prüfung vorzubereiten.

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

    response = client.messages.create(
        model=MODEL,
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
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

    response = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text.strip()


def _save_summary(profile: dict, titel: str, summary: str) -> str:
    from datetime import date
    import re
    from app.storage import storage_backend as sb
    from app.storage.supabase_client import get_client, get_user_id

    safe_titel = re.sub(r"[^a-z0-9]+", "-", titel.lower())[:40]
    slug = profile["slug"]
    storage_path = f"{slug}/summaries/{date.today()}_{safe_titel}.md"
    sb.write_text(storage_path, summary)

    if profile.get("id"):
        try:
            get_client().table("summaries").insert({
                "user_id":      get_user_id(),
                "module_id":    profile["id"],
                "title":        titel,
                "storage_path": storage_path,
            }).execute()
        except Exception:
            pass
    return storage_path


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
