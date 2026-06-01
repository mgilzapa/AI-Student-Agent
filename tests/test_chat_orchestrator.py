"""Tests for app/chat/orchestrator.py — routing with a mocked Anthropic client."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.chat import orchestrator as O


# ── Fakes ─────────────────────────────────────────────────────────────────────

def _text(s):
    return SimpleNamespace(type="text", text=s)


def _tool(name, inp, id="tu_1"):
    return SimpleNamespace(type="tool_use", name=name, input=inp, id=id)


def _resp(content, stop_reason):
    return SimpleNamespace(content=content, stop_reason=stop_reason)


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


async def _fake_rag(message, module_name, chat_history):
    yield {"type": "token", "content": "RAG-Antwort"}
    yield {"type": "done", "sources": []}


def _run(client, *, message="hi", module_name="Analysis I", chat_history=None,
         pending_proposal=None, read_executor=None, rag_streamer=_fake_rag):
    async def go():
        events = []
        async for ev in O.run_chat(
            message=message,
            module_name=module_name,
            chat_history=chat_history or [],
            pending_proposal=pending_proposal,
            client=client,
            model="claude-haiku-4-5",
            system_prompt="SYS",
            read_executor=read_executor or (lambda *a, **k: {"kind": "x", "items": [], "result_text": ""}),
            rag_streamer=rag_streamer,
        ):
            events.append(ev)
        return events
    return asyncio.run(go())


# ── Cases ───────────────────────────────────────────────────────────────────

def test_mutating_tool_emits_proposal_and_never_executes():
    calls = []
    def spy_exec(*a, **k):
        calls.append(a)
        return {"kind": "x", "items": [], "result_text": ""}

    client = _FakeClient([_resp([_tool("erstelle_klausur", {"anzahl_aufgaben": 8})], "tool_use")])
    events = _run(client, read_executor=spy_exec)

    types = [e["type"] for e in events]
    assert types == ["proposal", "done"]
    prop = events[0]
    assert prop["action"] == "erstelle_klausur"
    assert prop["params"]["anzahl_aufgaben"] == 8
    assert prop["params"]["punkte"] == 60
    assert prop["params"]["modul"] == "Analysis I"
    assert prop["summary"]
    assert calls == []                       # gate: nothing executed
    assert len(client.messages.calls) == 1   # no agentic loop for the gate


def test_no_tool_falls_back_to_rag():
    client = _FakeClient([_resp([_text("ignored direct answer")], "end_turn")])
    events = _run(client)
    assert events == [
        {"type": "token", "content": "RAG-Antwort"},
        {"type": "done", "sources": []},
    ]


def test_read_tool_emits_data_then_streams_intro():
    def reader(tool_name, raw, module_name):
        assert tool_name == "zeige_klausuren"
        return {"kind": "klausuren", "items": [{"n": 1}, {"n": 2}], "result_text": "2 Klausuren"}

    client = _FakeClient([
        _resp([_tool("zeige_klausuren", {})], "tool_use"),
        _resp([_text("Du hast 2 Klausuren:")], "end_turn"),
    ])
    events = _run(client, read_executor=reader)

    types = [e["type"] for e in events]
    assert types == ["data", "token", "done"]
    assert events[0]["kind"] == "klausuren"
    assert len(events[0]["items"]) == 2
    assert events[1]["content"] == "Du hast 2 Klausuren:"
    assert len(client.messages.calls) == 2


def test_client_tool_emits_client_action():
    client = _FakeClient([
        _resp([_tool("wechsle_modul", {"modul": "Lineare/Algebra"})], "tool_use"),
        _resp([_text("Erledigt.")], "end_turn"),
    ])
    events = _run(client)
    types = [e["type"] for e in events]
    assert "client_action" in types
    ca = next(e for e in events if e["type"] == "client_action")
    assert ca["action"] == "wechsle_modul"
    assert ca["args"]["modul"] == "LineareAlgebra"
    assert types[-1] == "done"


def test_pending_proposal_is_passed_to_model():
    client = _FakeClient([_resp([_tool("erstelle_klausur", {"anzahl_aufgaben": 10})], "tool_use")])
    pending = {"action": "erstelle_klausur", "params": {"modul": "Analysis I", "anzahl_aufgaben": 6, "punkte": 60},
               "summary": "alter Vorschlag"}
    events = _run(client, message="mach 10 aufgaben draus", pending_proposal=pending)

    # the model must have seen the pending proposal in its messages
    sent = json.dumps(client.messages.calls[0]["messages"], ensure_ascii=False)
    assert "erstelle_klausur" in sent and "alter Vorschlag" in sent
    # and the revised proposal reflects the new value
    assert events[0]["type"] == "proposal"
    assert events[0]["params"]["anzahl_aufgaben"] == 10


def test_model_exception_yields_error_event():
    class Boom:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("kaputt")
    events = _run(Boom())
    types = [e["type"] for e in events]
    assert "error" in types
    assert types[-1] == "done"
