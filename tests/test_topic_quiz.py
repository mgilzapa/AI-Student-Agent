import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_model_is_gemini_lite():
    # Quiz generation runs on Gemini 2.5 Flash Lite (migrated from claude-sonnet).
    assert tq.MODEL == "gemini-2.5-flash-lite"


def test_generate_uses_gemini_chat_completions(monkeypatch):
    """generate() must call the OpenAI-compatible chat.completions endpoint
    (Gemini) with the configured model, and persist the parsed questions."""
    captured = {}

    def fake_create(*, model, max_tokens, messages):
        captured["model"] = model
        captured["max_tokens"] = max_tokens
        captured["messages"] = messages
        content = (
            '{"questions": [{"type": "open", "question": "Was ist 3NF?",'
            ' "solution": "..."}]}'
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    monkeypatch.setattr(tq, "_get_client", lambda: fake_client)

    saved = {}
    monkeypatch.setattr(
        tq, "save_quiz", lambda m, t, q: saved.update({"module": m, "topic": t, "quiz": q})
    )

    quiz = tq.generate(
        "Datenbanken",
        {"id": "t3", "name": "Normalformen", "subtopics": ["3NF", "BCNF"]},
        rag_context="Eine Relation ist in 3NF, wenn ...",
    )

    assert captured["model"] == "gemini-2.5-flash-lite"
    assert captured["messages"][0]["role"] == "user"
    assert quiz["topic_id"] == "t3"
    assert quiz["topic_name"] == "Normalformen"
    assert len(quiz["questions"]) == 1
    assert quiz["questions"][0]["question"] == "Was ist 3NF?"
    assert saved["quiz"] is quiz
