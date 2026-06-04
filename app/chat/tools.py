"""Tool definitions, classification and parameter normalization for the chat agent.

This module is intentionally dependency-free (no FastAPI / Supabase / Anthropic
imports) so the routing logic can be unit-tested in isolation. The orchestrator
and the API layer inject the actual data accessors.

Three tool classes:
  * MUTATING — create artifacts. The orchestrator turns these into a *proposal*
    and never executes them; execution happens only after an explicit click in
    the frontend via the existing, rate-limited generator endpoints.
  * READ     — fetch data. Executed immediately; result streamed as a `data`
    event and fed back to Claude.
  * CLIENT   — UI navigation the server cannot perform; forwarded to the
    frontend as a `client_action` event.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

MUTATING = "mutating"
READ = "read"
CLIENT = "client"

# name -> class
TOOL_CLASS: Dict[str, str] = {
    "erstelle_klausur":          MUTATING,
    "erstelle_roadmap":          MUTATING,
    "erstelle_zusammenfassung":  MUTATING,
    "erstelle_loesungsblatt":    MUTATING,
    "erstelle_tagesplan":        MUTATING,
    "erstelle_quiz":             MUTATING,
    "zeige_klausuren":           READ,
    "zeige_zusammenfassungen":   READ,
    "zeige_dateien":             READ,
    "zeige_lernfortschritt":     READ,
    "wechsle_modul":             CLIENT,
    "oeffne_roadmap":            CLIENT,
    "oeffne_datei":              CLIENT,
}

# Clamping ranges — mirror the validation in the generator endpoints.
EXAM_TASKS_MIN, EXAM_TASKS_MAX = 1, 20
EXAM_POINTS_MIN, EXAM_POINTS_MAX = 1, 500
DAILY_HOURS_MIN, DAILY_HOURS_MAX = 0.5, 12.0

MAX_TEXT = 50_000  # mirrors MAX_TEXT_CHARS for pasted Loesungsblatt text


def classify(tool_name: str) -> Optional[str]:
    """Return MUTATING / READ / CLIENT, or None for an unknown tool."""
    return TOOL_CLASS.get(tool_name)


# ── Sanitizing helpers (mirrors api.sanitize_module_name, kept local on purpose) ─

def sanitize_module_name(name: str) -> str:
    name = (name or "").strip()
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", name)
    return name.strip()


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _clamp_float(value: Any, lo: float, hi: float, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def _module(raw: dict, active_module: str) -> str:
    clean = sanitize_module_name(raw.get("modul") or "")
    return clean or sanitize_module_name(active_module or "")


# ── Anthropic tool schemas ────────────────────────────────────────────────────

def tool_definitions() -> List[dict]:
    """All tools in the Anthropic Messages API tool format."""
    return [
        {
            "name": "erstelle_klausur",
            "description": (
                "Schlage dem Nutzer vor, eine neue Probeklausur zu erstellen. "
                "Verwende dieses Tool, wenn der Nutzer eine (Probe-)Klausur, einen Test "
                "oder Übungsaufgaben generieren möchte. Es wird NICHT sofort erstellt — "
                "der Nutzer muss zuerst bestätigen."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "modul": {"type": "string", "description": "Modulname (Standard: aktives Modul)."},
                    "anzahl_aufgaben": {"type": "integer", "description": "Anzahl der Aufgaben (Standard 6)."},
                    "punkte": {"type": "integer", "description": "Gesamtpunktzahl (Standard 60)."},
                },
            },
        },
        {
            "name": "erstelle_roadmap",
            "description": (
                "Schlage vor, einen Lernfahrplan (Roadmap) für ein Modul zu erstellen. "
                "Verwende dies, wenn der Nutzer einen Lernplan, eine Roadmap oder eine "
                "Themenübersicht für die Prüfungsvorbereitung möchte."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "modul": {"type": "string", "description": "Modulname (Standard: aktives Modul)."},
                    "klausur_datum": {"type": "string", "description": "Optionales Prüfungsdatum (YYYY-MM-DD)."},
                    "fokus": {"type": "string", "description": "Optionaler thematischer Schwerpunkt."},
                },
            },
        },
        {
            "name": "erstelle_zusammenfassung",
            "description": (
                "Schlage vor, eine Zusammenfassung einer Vorlesungsdatei zu erstellen. "
                "Wenn keine konkrete Datei genannt ist, nutze zuerst zeige_dateien und "
                "frage, welche Datei zusammengefasst werden soll."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "modul": {"type": "string", "description": "Modulname (Standard: aktives Modul)."},
                    "datei": {"type": "string", "description": "Dateiname der zusammenzufassenden Vorlesung."},
                },
            },
        },
        {
            "name": "erstelle_loesungsblatt",
            "description": (
                "Schlage vor, ein Übungsblatt lösen zu lassen. Quelle ist entweder ein "
                "Dateiname (Übungsblatt im Modul) oder eingefügter Aufgabentext."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "modul": {"type": "string", "description": "Modulname (Standard: aktives Modul)."},
                    "quelle": {"type": "string", "description": "Dateiname des Übungsblatts ODER der eingefügte Aufgabentext."},
                },
            },
        },
        {
            "name": "erstelle_tagesplan",
            "description": (
                "Schlage vor, einen Tagesplan (tägliche Lernaufgaben) zu erstellen. "
                "Setzt eine vorhandene Roadmap voraus — fehlt sie, schlage zuerst "
                "erstelle_roadmap vor."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "modul": {"type": "string", "description": "Modulname (Standard: aktives Modul)."},
                    "taegliche_stunden": {"type": "number", "description": "Tägliche Lernzeit in Stunden (0.5–12, Standard 2)."},
                },
            },
        },
        {
            "name": "erstelle_quiz",
            "description": (
                "Schlage vor, ein Abschluss-Quiz für ein konkretes Roadmap-Thema zu erstellen. "
                "Verwende dieses Tool, wenn der Nutzer ein Quiz, eine Wissensabfrage oder einen "
                "Self-Test zu einem bestimmten Thema möchte. Setzt eine vorhandene Roadmap voraus. "
                "Es wird NICHT sofort erstellt — der Nutzer muss zuerst bestätigen."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "modul": {"type": "string", "description": "Modulname (Standard: aktives Modul)."},
                    "thema": {"type": "string", "description": "Name des Roadmap-Themas, für das das Quiz erstellt werden soll."},
                },
                "required": ["thema"],
            },
        },
        {
            "name": "zeige_klausuren",
            "description": "Liste die vorhandenen Probeklausuren eines Moduls auf.",
            "input_schema": {
                "type": "object",
                "properties": {"modul": {"type": "string", "description": "Modulname (Standard: aktives Modul)."}},
            },
        },
        {
            "name": "zeige_zusammenfassungen",
            "description": "Liste die gespeicherten Zusammenfassungen eines Moduls auf.",
            "input_schema": {
                "type": "object",
                "properties": {"modul": {"type": "string", "description": "Modulname (Standard: aktives Modul)."}},
            },
        },
        {
            "name": "zeige_dateien",
            "description": "Liste die Dateien (Vorlesungen, Übungsblätter, Altklausuren) eines Moduls auf.",
            "input_schema": {
                "type": "object",
                "properties": {"modul": {"type": "string", "description": "Modulname (Standard: aktives Modul)."}},
            },
        },
        {
            "name": "zeige_lernfortschritt",
            "description": "Zeige den Lernfortschritt (erledigte Aufgaben) eines Moduls.",
            "input_schema": {
                "type": "object",
                "properties": {"modul": {"type": "string", "description": "Modulname (Standard: aktives Modul)."}},
            },
        },
        {
            "name": "wechsle_modul",
            "description": "Wechsle das aktive Modul in der Oberfläche.",
            "input_schema": {
                "type": "object",
                "properties": {"modul": {"type": "string", "description": "Zielmodul."}},
                "required": ["modul"],
            },
        },
        {
            "name": "oeffne_roadmap",
            "description": "Öffne den Roadmap-Tab des aktiven Moduls.",
            "input_schema": {"type": "object", "properties": {}},
        },
        {
            "name": "oeffne_datei",
            "description": "Öffne eine Datei im Datei-Viewer.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "modul": {"type": "string", "description": "Modulname (Standard: aktives Modul)."},
                    "datei": {"type": "string", "description": "Dateiname."},
                },
                "required": ["datei"],
            },
        },
    ]


# ── Mutation parameter normalization ──────────────────────────────────────────

def normalize_mutation(tool_name: str, raw: dict, active_module: str) -> dict:
    """Clamp + sanitize the parameters of a mutating tool. Keys stay German
    (the proposal card shows/edits them; the frontend maps them to the
    generator endpoint on confirm)."""
    raw = raw or {}
    modul = _module(raw, active_module)

    if tool_name == "erstelle_klausur":
        return {
            "modul": modul,
            "anzahl_aufgaben": _clamp_int(raw.get("anzahl_aufgaben"), EXAM_TASKS_MIN, EXAM_TASKS_MAX, 6),
            "punkte": _clamp_int(raw.get("punkte"), EXAM_POINTS_MIN, EXAM_POINTS_MAX, 60),
        }
    if tool_name == "erstelle_roadmap":
        out = {"modul": modul}
        if raw.get("klausur_datum"):
            out["klausur_datum"] = str(raw["klausur_datum"])[:50]
        if raw.get("fokus"):
            out["fokus"] = str(raw["fokus"])[:2000]
        return out
    if tool_name == "erstelle_zusammenfassung":
        out = {"modul": modul}
        if raw.get("datei"):
            out["datei"] = str(raw["datei"])
        return out
    if tool_name == "erstelle_loesungsblatt":
        out = {"modul": modul}
        if raw.get("quelle"):
            out["quelle"] = str(raw["quelle"])[:MAX_TEXT]
        return out
    if tool_name == "erstelle_tagesplan":
        return {
            "modul": modul,
            "taegliche_stunden": _clamp_float(raw.get("taegliche_stunden"), DAILY_HOURS_MIN, DAILY_HOURS_MAX, 2.0),
        }
    if tool_name == "erstelle_quiz":
        return {
            "modul": modul,
            "thema": str(raw.get("thema") or "")[:200].strip(),
        }
    # Unknown mutating tool — return sanitized module only.
    return {"modul": modul}


def normalize_client(tool_name: str, raw: dict, active_module: str) -> dict:
    """Normalize the args of a client/navigation tool."""
    raw = raw or {}
    if tool_name == "wechsle_modul":
        return {"modul": sanitize_module_name(raw.get("modul") or "")}
    if tool_name == "oeffne_roadmap":
        return {"modul": _module(raw, active_module)}
    if tool_name == "oeffne_datei":
        return {"modul": _module(raw, active_module), "datei": str(raw.get("datei") or "")}
    return {}


# ── German plain-text proposal summaries ──────────────────────────────────────

def build_summary(tool_name: str, params: dict) -> str:
    modul = params.get("modul", "")
    if tool_name == "erstelle_klausur":
        return (
            'Ich erstelle eine Probeklausur fuer "' + modul + '" mit '
            + str(params["anzahl_aufgaben"]) + " Aufgaben und "
            + str(params["punkte"]) + " Punkten im Stil deiner Altklausuren."
        )
    if tool_name == "erstelle_roadmap":
        extra = []
        if params.get("klausur_datum"):
            extra.append("Pruefung am " + str(params["klausur_datum"]))
        if params.get("fokus"):
            extra.append("Fokus: " + str(params["fokus"]))
        tail = (" (" + ", ".join(extra) + ")") if extra else ""
        return 'Ich erstelle eine Lern-Roadmap fuer "' + modul + '"' + tail + "."
    if tool_name == "erstelle_zusammenfassung":
        datei = params.get("datei")
        if datei:
            return 'Ich fasse die Datei "' + str(datei) + '" aus "' + modul + '" zusammen.'
        return 'Ich erstelle eine Zusammenfassung fuer "' + modul + '".'
    if tool_name == "erstelle_loesungsblatt":
        quelle = params.get("quelle", "")
        kurz = quelle if len(quelle) <= 40 else quelle[:40] + "..."
        return 'Ich loese das Uebungsblatt fuer "' + modul + '" (' + kurz + ")."
    if tool_name == "erstelle_tagesplan":
        return (
            'Ich erstelle einen Tagesplan fuer "' + modul + '" mit '
            + str(params["taegliche_stunden"]) + " Stunden pro Tag."
        )
    if tool_name == "erstelle_quiz":
        thema = params.get("thema", "")
        return 'Ich erstelle ein Abschluss-Quiz zum Thema "' + thema + '" in "' + modul + '".'
    return 'Vorschlag fuer "' + modul + '".'
