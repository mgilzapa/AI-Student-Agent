import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.lecture import topic_worksheet as tw


def _fake_gemini(content):
    """Minimal stand-in for the OpenAI-compatible Gemini client used by generate()."""
    message = SimpleNamespace(content=content)
    completion = SimpleNamespace(choices=[SimpleNamespace(message=message)])
    completions = SimpleNamespace(create=lambda **kwargs: completion)
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def test_seq_of():
    assert tw._seq_of("t3_001") == 1
    assert tw._seq_of("t3_042") == 42
    assert tw._seq_of("t3") == 0
    assert tw._seq_of("") == 0


def test_extract_title_from_json():
    raw = '{"title": "Übungsblatt: Normalformen", "exercises": []}'
    assert tw._extract_title(raw, "fallback") == "Übungsblatt: Normalformen"


def test_extract_title_falls_back():
    assert tw._extract_title('{"exercises": []}', "Standard") == "Standard"
    assert tw._extract_title('{"title": ""}', "Standard") == "Standard"


def test_generate_assembles_and_persists(monkeypatch):
    raw = (
        '{"title": "Übungsblatt: Mengenlehre", "exercises": ['
        '{"type": "open", "question": "Zeige A∪B.", "solution": "..."},'
        '{"type": "multiple_choice", "question": "Welche gilt?",'
        ' "options": ["A", "B", "C", "D"], "correct": 1, "solution": "B"}'
        ']}'
    )

    monkeypatch.setattr(tw, "_get_client", lambda: _fake_gemini(raw))
    monkeypatch.setattr(tw, "_next_seq", lambda module_name, topic_id: 3)
    saved = {}
    monkeypatch.setattr(tw, "save_worksheet", lambda module_name, ws: saved.update(ws))

    out = tw.generate("Mathe", {"id": "t7", "name": "Mengenlehre"}, rag_context="…")

    assert out["worksheet_id"] == "t7_003"
    assert out["topic_id"] == "t7"
    assert out["title"] == "Übungsblatt: Mengenlehre"
    assert len(out["exercises"]) == 2
    assert out["exercises"][1]["type"] == "multiple_choice"
    assert out["exercises"][1]["correct"] == 1
    # Persisted exactly what was returned.
    assert saved["worksheet_id"] == "t7_003"


def test_salvage_recovers_truncated_response():
    # Outer object + array never close (response cut off mid-third exercise).
    raw = (
        '```json\n{\n "title": "Übungsblatt: X",\n "exercises": [\n'
        '  {"type": "open", "question": "Aufgabe 1", "solution": "L1"},\n'
        '  {"type": "open", "question": "Aufgabe 2", "solution": "L2"},\n'
        '  {"type": "open", "question": "Aufgabe 3 unvoll'
    )
    assert tw._parse_quiz(raw) == []  # whole-object parse fails
    salvaged = tw._salvage_exercises(raw)
    assert len(salvaged) == 2
    assert salvaged[0]["question"] == "Aufgabe 1"
    assert salvaged[1]["question"] == "Aufgabe 2"


def test_salvage_handles_braces_in_latex_strings():
    # Braces inside LaTeX strings must not break the object boundary detection.
    raw = (
        '{"exercises": [{"type": "open", "question": "Berechne $\\\\frac{a}{b}$",'
        ' "solution": "$P(A \\\\mid B) = \\\\frac{P(A \\\\cap B)}{P(B)}$"},'
        '{"type": "open", "question": "Zweite", "solution": "x"}'
    )  # array left unterminated
    salvaged = tw._salvage_exercises(raw)
    assert len(salvaged) == 2
    assert "frac" in salvaged[0]["question"]


def test_generate_raises_on_empty_response(monkeypatch):
    monkeypatch.setattr(tw, "_get_client", lambda: _fake_gemini("kein json"))
    monkeypatch.setattr(tw, "_next_seq", lambda m, t: 1)
    monkeypatch.setattr(tw, "save_worksheet", lambda m, ws: None)

    try:
        tw.generate("Mathe", {"id": "t1", "name": "X"})
        assert False, "expected ValueError"
    except ValueError:
        pass
