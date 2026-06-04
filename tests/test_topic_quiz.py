import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from app.lecture import topic_quiz as tq


def test_parse_quiz_object_with_questions():
    raw = """{"questions": [
        {"type": "open", "question": "Erkläre 3NF.", "solution": "..."},
        {"type": "multiple_choice", "question": "Was gilt?",
         "options": ["A", "B", "C", "D"], "correct": 2, "solution": "C"}
    ]}"""
    qs = tq._parse_quiz(raw)
    assert len(qs) == 2
    assert qs[0]["type"] == "open"
    assert qs[1]["type"] == "multiple_choice"
    assert qs[1]["options"] == ["A", "B", "C", "D"]
    assert qs[1]["correct"] == 2


def test_parse_quiz_bare_array():
    raw = '[{"type": "open", "question": "Q?", "solution": "S"}]'
    qs = tq._parse_quiz(raw)
    assert len(qs) == 1
    assert qs[0]["question"] == "Q?"


def test_parse_quiz_strips_code_fences():
    raw = '```json\n{"questions": [{"type": "open", "question": "Q?", "solution": "S"}]}\n```'
    qs = tq._parse_quiz(raw)
    assert len(qs) == 1


def test_parse_quiz_filters_questions_without_text():
    raw = """{"questions": [
        {"type": "open", "question": "", "solution": "x"},
        {"type": "open", "question": "Valid?", "solution": "y"}
    ]}"""
    qs = tq._parse_quiz(raw)
    assert len(qs) == 1
    assert qs[0]["question"] == "Valid?"


def test_parse_quiz_defaults_type_to_open():
    raw = '{"questions": [{"question": "Q?", "solution": "S"}]}'
    qs = tq._parse_quiz(raw)
    assert qs[0]["type"] == "open"


def test_parse_quiz_mc_without_options_falls_back_to_open():
    raw = '{"questions": [{"type": "multiple_choice", "question": "Q?", "solution": "S"}]}'
    qs = tq._parse_quiz(raw)
    # No options → cannot be a valid MC; treat as open rather than emit broken MC.
    assert qs[0]["type"] == "open"


def test_parse_quiz_invalid_json_returns_empty():
    assert tq._parse_quiz("not json at all") == []
