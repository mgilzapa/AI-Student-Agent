"""
lecture/module_profile.py
Lädt, speichert und verwaltet Modul-Profile (modules/<slug>.json).
Kein API-Call – reine Dateisystem-Operationen.
"""

import json
import re
from datetime import date
from pathlib import Path
from typing import Optional

# Relativ zum Projekt-Root
MODULES_DIR = Path("data/modules")


# ── Schema ────────────────────────────────────────────────────────────────────

def _empty_profile(name: str) -> dict:
    slug = _slugify(name)
    return {
        "name": name,
        "slug": slug,
        "aliases": [],
        "schwerpunkte": [],
        "pruefungsrelevant": [],
        "stil": "mixed",
        "prompt_hint": "",
        "extra": "",
        "exam_profile": str(MODULES_DIR / f"{slug}-exam-profile.md"),
        "history": str(MODULES_DIR / f"{slug}-history.md"),
        "created_at": str(date.today()),
        "updated_at": str(date.today()),
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def load(modul_name: str) -> Optional[dict]:
    """Lädt Profil anhand von Name oder Alias. None wenn nicht gefunden."""
    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    slug = _find_slug(modul_name)
    if slug is None:
        return None
    path = MODULES_DIR / f"{slug}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def save(profile: dict) -> Path:
    """Speichert Profil. Gibt Pfad zurück."""
    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    profile["updated_at"] = str(date.today())
    path = MODULES_DIR / f"{profile['slug']}.json"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def create_from_onboarding(answers: dict) -> dict:
    """
    answers = {
        "name": str,
        "schwerpunkte": list[str],
        "stil": str,
        "pruefungsrelevant": list[str],
    }
    """
    profile = _empty_profile(answers["name"])
    profile["schwerpunkte"] = answers.get("schwerpunkte", [])
    profile["stil"] = answers.get("stil", "mixed")
    profile["pruefungsrelevant"] = answers.get("pruefungsrelevant", [])
    save(profile)
    return profile


def update_exam_topics(slug: str, top_topics: list[str]) -> None:
    """Aktualisiert pruefungsrelevant nach Klausur-Analyse."""
    path = MODULES_DIR / f"{slug}.json"
    if not path.exists():
        return
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile["pruefungsrelevant"] = top_topics
    save(profile)


def append_history(profile: dict, lecture_title: str, concepts: list[str], builds_on: str = "") -> None:
    """Hängt neue Vorlesung an History-Datei an."""
    history_path = Path(profile["history"])
    history_path.parent.mkdir(parents=True, exist_ok=True)

    entry = f"\n## {lecture_title}\nKernkonzepte: {', '.join(concepts)}\n"
    if builds_on:
        entry += f"Baut auf: {builds_on}\n"

    with history_path.open("a", encoding="utf-8") as f:
        if not history_path.stat().st_size if history_path.exists() else True:
            f.write(f"# Vorlesungs-History: {profile['name']}\n")
        f.write(entry)


def load_history(profile: dict) -> str:
    """Gibt History als String zurück (leer wenn keine vorhanden)."""
    path = Path(profile["history"])
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_exam_profile(profile: dict) -> str:
    """Gibt Prüfungsprofil als String zurück."""
    path = Path(profile["exam_profile"])
    return path.read_text(encoding="utf-8") if path.exists() else ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[äöüß]", lambda m: {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}[m.group()], s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _find_slug(name: str) -> Optional[str]:
    """Sucht Profil per Name, Slug oder Alias."""
    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    target = name.lower().strip()

    for path in MODULES_DIR.glob("*.json"):
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if (
            profile.get("name", "").lower() == target
            or profile.get("slug", "") == _slugify(name)
            or target in [a.lower() for a in profile.get("aliases", [])]
        ):
            return profile["slug"]
    return None
