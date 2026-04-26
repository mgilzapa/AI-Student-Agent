"""
lecture/pipeline.py
Haupt-Einstiegspunkt für das Vorlesungs-Feature.
Wird von app/main.py aufgerufen – kein Breaking Change zur bestehenden Pipeline.

Verwendung:
    from app.lecture.pipeline import process_lecture

    result = process_lecture(
        filename="VL_AlgoDat_05.pdf",
        text="[extrahierter Text aus bestehendem Parser]",
    )
    print(result["summary"])
"""

from .detector import detect
from . import module_profile as mp
from .onboarding import run as onboarding_run
from .summarizer import summarize


def process_lecture(filename: str, text: str, modul_name: str | None = None) -> dict | None:
    """
    Hauptfunktion – wird aus app/main.py aufgerufen.

    filename    : Originaldateiname
    text        : Volltext des Dokuments (aus bestehendem Parser)
    modul_name  : Optional manuell überschreiben

    Rückgabe:
        None  → kein Vorlesungsinhalt erkannt
        dict  → {
            "summary": str,
            "konzepte": list,
            "titel": str,
            "modul": str,
            "saved_to": str,
            "confidence": str,
        }
    """
    # ── 1. Erkennung ──────────────────────────────────────────────────────────
    detection = detect(filename, text[:2000])

    if not detection.is_lecture:
        return None  # Kein Vorlesungsinhalt → bestehende Pipeline läuft normal weiter

    if detection.confidence == "low":
        answer = input(
            f'❓ Ist "{filename}" eine Vorlesung oder ein Lehrbuch? (v=Vorlesung / l=Lehrbuch / Enter=Vorlesung): '
        ).strip().lower()
        if answer == "l":
            return None

    # ── 2. Modul bestimmen ────────────────────────────────────────────────────
    resolved_modul = modul_name or _resolve_modul(detection.modul_hint)
    if not resolved_modul:
        return None  # User hat abgebrochen

    # ── 3. Profil laden oder Onboarding ───────────────────────────────────────
    profile = mp.load(resolved_modul)
    if not profile:
        print(f'\n📚 Modul "{resolved_modul}" noch nicht bekannt.')
        profile = onboarding_run(modul_hint=resolved_modul)

    # ── 4. Zwei-Stufen-Zusammenfassung ────────────────────────────────────────
    print(f'\n⚙️  Erstelle Zusammenfassung für "{profile["name"]}"...')
    result = summarize(profile["name"], text)

    print(f'\n✅ Zusammenfassung gespeichert: {result["saved_to"]}')
    print(f'   Konzepte: {", ".join(k["name"] for k in result["konzepte"])}')
    print('\n❓ Soll ich eines der Konzepte noch tiefer ausarbeiten? (Name eingeben oder Enter überspringen)')
    deep_dive = input("   > ").strip()
    if deep_dive:
        _deep_dive(deep_dive, profile["name"], text)

    return {**result, "modul": profile["name"], "confidence": detection.confidence}


def _resolve_modul(hint: str | None) -> str | None:
    """Modulname aus Hint bestätigen oder manuell eingeben."""
    if hint:
        answer = input(f'\n📘 Modul erkannt: "{hint}" – korrekt? (Enter=ja / anderen Namen eingeben): ').strip()
        return answer if answer else hint
    else:
        name = input("\n📘 Für welches Modul ist diese Vorlesung? > ").strip()
        return name if name else None


def _deep_dive(konzept: str, modul_name: str, original_text: str) -> None:
    """Vertieft ein einzelnes Konzept auf Anfrage."""
    from openai import OpenAI
    client = OpenAI()

    profile = mp.load(modul_name) or {}
    exam_profile = mp.load_exam_profile(profile) if profile else ""

    prompt = f"""Erkläre das Konzept "{konzept}" aus dem Modul "{modul_name}" sehr ausführlich.

Vorlesungsinhalt als Kontext:
{original_text[:3000]}

Prüfungsprofil:
{exam_profile or "nicht vorhanden"}

Stil: {profile.get("stil", "mixed")}
{f"Hinweis: {profile.get('prompt_hint', '')}" if profile.get("prompt_hint") else ""}

Gehe tief ins Detail:
1. Intuition und Motivation (warum brauchen wir das?)
2. Formale Definition / vollständiger Algorithmus
3. Schritt-für-Schritt Beispiel (vollständig durchgerechnet)
4. Typische Prüfungsaufgabe + Musterlösung
5. Häufige Fehler und wie man sie vermeidet
6. Verbindung zu anderen Konzepten des Moduls
"""

    response = client.chat.completions.create(
        model="gpt-4o",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    print(f"\n{'='*60}")
    print(f"🔍 Deep Dive: {konzept}")
    print('='*60)
    print(response.choices[0].message.content)
