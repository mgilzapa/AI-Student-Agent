"""
lecture/onboarding.py
Interaktiver Onboarding-Flow für neue Module.
Läuft im Terminal – kann leicht an ein Web-Frontend angepasst werden.
"""

from . import module_profile as mp


def run(modul_hint: str | None = None) -> dict:
    """
    Führt das Onboarding durch und gibt das gespeicherte Profil zurück.
    modul_hint: vorausgefüllter Modulname (aus Dateiname-Erkennung)
    """
    print("\n📚 Neues Modul einrichten\n")

    # Frage 1: Modulname
    if modul_hint:
        name_input = input(f'1. Modulname (Enter für "{modul_hint}"): ').strip()
        name = name_input or modul_hint
    else:
        name = _ask("1. Wie heißt das Modul genau?")

    # Prüfen ob schon vorhanden
    existing = mp.load(name)
    if existing:
        print(f'✅ Modul "{name}" bereits bekannt. Nutze bestehendes Profil.')
        return existing

    # Frage 2: Schwerpunkte
    raw = _ask("2. Was sind die wichtigsten Themen / Schwerpunkte? (kommagetrennt)")
    schwerpunkte = [s.strip() for s in raw.split(",") if s.strip()]

    # Frage 3: Stil
    print("3. Stil des Moduls:")
    print("   [1] Mathe/Naturwissenschaft  [2] Informatik  [3] Geisteswissenschaft  [4] BWL  [5] Mixed")
    stil_map = {"1": "mathe", "2": "informatik", "3": "geistes", "4": "bwl", "5": "mixed"}
    stil_input = input("   Auswahl (1-5, Enter = mixed): ").strip()
    stil = stil_map.get(stil_input, "mixed")

    # Frage 4: Prüfungsrelevanz (initial, wird später durch Klausur-Analyse verfeinert)
    raw = _ask("4. Was ist typischerweise prüfungsrelevant? (kommagetrennt, oder Enter überspringen)")
    pruefungsrelevant = [s.strip() for s in raw.split(",") if s.strip()]

    # Profil anlegen
    profile = mp.create_from_onboarding({
        "name": name,
        "schwerpunkte": schwerpunkte,
        "stil": stil,
        "pruefungsrelevant": pruefungsrelevant,
    })

    print(f'\n✅ Modul "{name}" gespeichert unter data/modules/{profile["slug"]}.json')

    # Optional: Altklausuren
    has_exams = input("\n📄 Hast du Altklausuren zum Hochladen? (j/n): ").strip().lower()
    if has_exams == "j":
        print("→ Lege Klausuren in data/exams/<modulname>/ ab und führe aus:")
        print(f"  python -m app.lecture.exam_analyzer {profile['slug']}")

    return profile


def _ask(question: str) -> str:
    return input(f"{question}\n   > ").strip()
