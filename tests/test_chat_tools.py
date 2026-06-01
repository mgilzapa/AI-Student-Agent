"""Tests for app/chat/tools.py — tool classification + parameter normalization."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.chat import tools as T


# ── Classification ───────────────────────────────────────────────────────────

def test_classify_mutating_tools():
    for name in ("erstelle_klausur", "erstelle_roadmap", "erstelle_zusammenfassung",
                 "erstelle_loesungsblatt", "erstelle_tagesplan"):
        assert T.classify(name) == T.MUTATING, name


def test_classify_read_tools():
    for name in ("zeige_klausuren", "zeige_zusammenfassungen", "zeige_dateien",
                 "zeige_lernfortschritt"):
        assert T.classify(name) == T.READ, name


def test_classify_client_tools():
    for name in ("wechsle_modul", "oeffne_roadmap", "oeffne_datei"):
        assert T.classify(name) == T.CLIENT, name


def test_classify_unknown_tool_returns_none():
    assert T.classify("loesche_alles") is None


# ── Tool definitions (Anthropic format) ──────────────────────────────────────

def test_tool_definitions_cover_every_classified_tool():
    defs = T.tool_definitions()
    names = {d["name"] for d in defs}
    assert names == set(T.TOOL_CLASS.keys())


def test_tool_definitions_have_valid_anthropic_schema():
    for d in T.tool_definitions():
        assert isinstance(d["name"], str) and d["name"]
        assert isinstance(d["description"], str) and d["description"]
        schema = d["input_schema"]
        assert schema["type"] == "object"
        assert "properties" in schema


# ── Mutation parameter normalization & clamping ───────────────────────────────

def test_klausur_defaults_when_unspecified():
    p = T.normalize_mutation("erstelle_klausur", {}, "Analysis I")
    assert p["modul"] == "Analysis I"
    assert p["anzahl_aufgaben"] == 6
    assert p["punkte"] == 60


def test_klausur_clamps_out_of_range_values():
    p = T.normalize_mutation("erstelle_klausur",
                             {"anzahl_aufgaben": 999, "punkte": -5}, "M")
    assert p["anzahl_aufgaben"] == T.EXAM_TASKS_MAX
    assert p["punkte"] == T.EXAM_POINTS_MIN


def test_klausur_explicit_module_overrides_active():
    p = T.normalize_mutation("erstelle_klausur", {"modul": "Lineare Algebra"}, "Analysis I")
    assert p["modul"] == "Lineare Algebra"


def test_tagesplan_clamps_hours_and_defaults():
    assert T.normalize_mutation("erstelle_tagesplan", {}, "M")["taegliche_stunden"] == 2.0
    assert T.normalize_mutation("erstelle_tagesplan", {"taegliche_stunden": 99}, "M")["taegliche_stunden"] == T.DAILY_HOURS_MAX
    assert T.normalize_mutation("erstelle_tagesplan", {"taegliche_stunden": 0.1}, "M")["taegliche_stunden"] == T.DAILY_HOURS_MIN


def test_module_name_is_sanitized():
    p = T.normalize_mutation("erstelle_klausur", {"modul": "  Analy/sis<>  "}, "X")
    assert p["modul"] == "Analysis"


def test_roadmap_passes_optional_fields():
    p = T.normalize_mutation("erstelle_roadmap",
                             {"modul": "M", "klausur_datum": "2026-07-01", "fokus": "Integrale"}, "X")
    assert p["modul"] == "M"
    assert p["klausur_datum"] == "2026-07-01"
    assert p["fokus"] == "Integrale"


# ── Client argument normalization ─────────────────────────────────────────────

def test_client_wechsle_modul_sanitizes():
    a = T.normalize_client("wechsle_modul", {"modul": "Lineare/Algebra"}, "X")
    assert a["modul"] == "LineareAlgebra"


def test_client_oeffne_datei_carries_file_and_module():
    a = T.normalize_client("oeffne_datei", {"datei": "vl01.pdf"}, "Analysis I")
    assert a["modul"] == "Analysis I"
    assert a["datei"] == "vl01.pdf"


# ── Summaries ─────────────────────────────────────────────────────────────────

def test_build_summary_klausur_mentions_counts_and_module():
    s = T.build_summary("erstelle_klausur",
                        {"modul": "Analysis I", "anzahl_aufgaben": 6, "punkte": 60})
    assert "Analysis I" in s
    assert "6" in s
    assert "60" in s


def test_build_summary_tagesplan_mentions_hours():
    s = T.build_summary("erstelle_tagesplan", {"modul": "M", "taegliche_stunden": 2.0})
    assert "2" in s
