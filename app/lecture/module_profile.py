"""
lecture/module_profile.py
Lädt, speichert und verwaltet Modul-Profile in der Supabase modules-Tabelle.
"""

import re
from datetime import date
from typing import Optional

from app.storage.supabase_client import get_client, get_user_id


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    s = name.lower().strip()
    s = re.sub(r"[äöüß]", lambda m: {"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}[m.group()], s)
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _row_to_profile(row: dict) -> dict:
    return {
        "id":                    row.get("id", ""),
        "name":                  row.get("name", ""),
        "slug":                  row.get("slug", ""),
        "aliases":               row.get("aliases") or [],
        "schwerpunkte":          row.get("schwerpunkte") or [],
        "pruefungsrelevant":     row.get("pruefungsrelevant") or [],
        "stil":                  row.get("stil", "mixed"),
        "prompt_hint":           row.get("prompt_hint", ""),
        "extra":                 row.get("extra", ""),
        "exam_profile":          row.get("exam_profile_md", ""),
        "history":               row.get("history_md", ""),
        "manual_exam_files":     row.get("manual_exam_files") or [],
        "manual_not_exam_files": row.get("manual_not_exam_files") or [],
        "file_types":            row.get("file_types") or {},
        "created_at":            str(row.get("created_at", "")),
        "updated_at":            str(row.get("updated_at", "")),
    }


# ── Public API ─────────────────────────────────────────────────────────────────

def load(modul_name: str) -> Optional[dict]:
    """Load module profile by name or slug. Returns None if not found."""
    uid = get_user_id()
    slug = _slugify(modul_name)
    try:
        rows = (
            get_client()
            .table("modules")
            .select("*")
            .eq("user_id", uid)
            .execute()
        ).data or []
    except Exception:
        return None

    target = modul_name.lower().strip()
    for row in rows:
        if (
            row.get("name", "").lower() == target
            or row.get("slug", "") == slug
            or target in [a.lower() for a in (row.get("aliases") or [])]
        ):
            return _row_to_profile(row)
    return None


def save(profile: dict) -> None:
    """Upsert module profile to Supabase."""
    uid = get_user_id()
    profile["updated_at"] = str(date.today())
    data = {
        "user_id":               uid,
        "name":                  profile.get("name", ""),
        "slug":                  profile.get("slug", ""),
        "aliases":               profile.get("aliases", []),
        "schwerpunkte":          profile.get("schwerpunkte", []),
        "pruefungsrelevant":     profile.get("pruefungsrelevant", []),
        "stil":                  profile.get("stil", "mixed"),
        "prompt_hint":           profile.get("prompt_hint", ""),
        "extra":                 profile.get("extra", ""),
        "exam_profile_md":       profile.get("exam_profile", ""),
        "history_md":            profile.get("history", ""),
        "manual_exam_files":     profile.get("manual_exam_files", []),
        "manual_not_exam_files": profile.get("manual_not_exam_files", []),
        "file_types":            profile.get("file_types", {}),
        "updated_at":            str(date.today()),
    }
    try:
        existing_id = profile.get("id")
        if existing_id:
            get_client().table("modules").update(data).eq("id", existing_id).execute()
        else:
            get_client().table("modules").upsert(
                {**data, "created_at": str(date.today())},
                on_conflict="user_id,slug",
            ).execute()
    except Exception as exc:
        raise RuntimeError(f"Failed to save module profile: {exc}") from exc


def create_from_onboarding(answers: dict) -> dict:
    name = answers["name"]
    profile = {
        "name":              name,
        "slug":              _slugify(name),
        "aliases":           [],
        "schwerpunkte":      answers.get("schwerpunkte", []),
        "stil":              answers.get("stil", "mixed"),
        "pruefungsrelevant": answers.get("pruefungsrelevant", []),
        "prompt_hint":       "",
        "extra":             "",
        "exam_profile":      "",
        "history":           "",
        "manual_exam_files":     [],
        "manual_not_exam_files": [],
        "file_types":            {},
    }
    save(profile)
    return load(name) or profile


def update_exam_topics(slug: str, top_topics: list) -> None:
    uid = get_user_id()
    try:
        get_client().table("modules").update(
            {"pruefungsrelevant": top_topics, "updated_at": str(date.today())}
        ).eq("user_id", uid).eq("slug", slug).execute()
    except Exception as exc:
        raise RuntimeError(f"Failed to update exam topics: {exc}") from exc


def append_history(profile: dict, lecture_title: str, concepts: list, builds_on: str = "") -> None:
    """Append lecture entry to history_md in the modules table."""
    history = profile.get("history", "") or ""
    if not history:
        history = f"# Vorlesungs-History: {profile['name']}\n"
    entry = f"\n## {lecture_title}\nKernkonzepte: {', '.join(concepts)}\n"
    if builds_on:
        entry += f"Baut auf: {builds_on}\n"
    profile["history"] = history + entry
    save(profile)


def load_history(profile: dict) -> str:
    return profile.get("history", "") or ""


def load_exam_profile(profile: dict) -> str:
    return profile.get("exam_profile", "") or ""
