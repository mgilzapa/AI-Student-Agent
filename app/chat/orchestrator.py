"""Chat orchestrator: a Claude tool-use loop that routes a chat message to one of

  (a) a *proposal* for a mutating action (hard confirmation gate — nothing runs),
  (b) immediate read/navigation tools (data / client_action events), or
  (c) the existing RAG Q&A pipeline (fallback when no tool is chosen).

The orchestrator yields SSE-shaped event dicts; the API layer serializes them.
It is deliberately decoupled from FastAPI/Supabase via injected callables
(`read_executor`, `rag_streamer`) so the routing can be unit-tested with a mocked
Anthropic client.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from app.chat import tools as T

logger = logging.getLogger(__name__)

MAX_ITERS = 4          # agentic loop ceiling (read-tool round trips)
ROUTING_MAX_TOKENS = 1000


def _blocks_to_params(content: List[Any]) -> List[dict]:
    """Convert response content blocks to plain dicts so the assistant turn is
    JSON-serializable and safe to send back to the API on the next iteration."""
    out: List[dict] = []
    for b in content:
        btype = getattr(b, "type", None)
        if btype == "text":
            out.append({"type": "text", "text": getattr(b, "text", "")})
        elif btype == "tool_use":
            out.append({
                "type": "tool_use",
                "id": getattr(b, "id", ""),
                "name": getattr(b, "name", ""),
                "input": getattr(b, "input", {}) or {},
            })
    return out


def _build_messages(chat_history: List[dict], message: str,
                    pending_proposal: Optional[dict]) -> List[dict]:
    messages: List[dict] = []
    for turn in (chat_history or [])[-6:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})

    user_text = message
    if pending_proposal:
        # The frontend holds the open proposal; on a text tweak it is sent back so
        # Claude can revise it. Surface it as context appended to the user's message.
        try:
            ctx = json.dumps(pending_proposal, ensure_ascii=False)
        except (TypeError, ValueError):
            ctx = str(pending_proposal)
        user_text = (
            f"{message}\n\n[OFFENER VORSCHLAG, den du anpassen sollst: {ctx}. "
            f"Rufe dasselbe Tool erneut mit den angepassten Parametern auf.]"
        )
    messages.append({"role": "user", "content": user_text})
    return messages


async def run_chat(
    *,
    message: str,
    module_name: str,
    chat_history: List[dict],
    pending_proposal: Optional[dict],
    client: Any,
    model: str,
    system_prompt: str,
    read_executor: Callable[[str, dict, str], Dict[str, Any]],
    rag_streamer: Callable[[str, str, List[dict]], AsyncIterator[dict]],
    max_iters: int = MAX_ITERS,
) -> AsyncIterator[dict]:
    """Drive the routing loop and yield SSE event dicts."""
    messages = _build_messages(chat_history, message, pending_proposal)
    tool_defs = T.tool_definitions()
    used_tool = False

    try:
        for _ in range(max_iters):
            response = await asyncio.to_thread(
                client.messages.create,
                model="deepseek-v4-flash",
                max_tokens=ROUTING_MAX_TOKENS,
                system=system_prompt,
                tools=tool_defs,
                messages=messages,
            )

            tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
            texts = [getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"]

            if not tool_uses:
                if not used_tool:
                    # (c) No tool on the first turn → normal question → RAG.
                    async for ev in rag_streamer(message, module_name, chat_history):
                        yield ev
                    return
                # (b) follow-up after read tools → stream Claude's short intro.
                intro = "".join(texts).strip()
                if intro:
                    yield {"type": "token", "content": intro}
                yield {"type": "done"}
                return

            # (a) Mutating tool → propose the first one and STOP (the hard gate).
            mutating = [b for b in tool_uses if T.classify(b.name) == T.MUTATING]
            if mutating:
                b = mutating[0]
                params = T.normalize_mutation(b.name, b.input or {}, module_name)
                yield {
                    "type": "proposal",
                    "action": b.name,
                    "params": params,
                    "summary": T.build_summary(b.name, params),
                }
                yield {"type": "done"}
                return

            # (b) read / client tools → execute, emit events, feed results back.
            used_tool = True
            messages.append({"role": "assistant", "content": _blocks_to_params(response.content)})
            tool_results: List[dict] = []
            for b in tool_uses:
                cls = T.classify(b.name)
                if cls == T.READ:
                    try:
                        data = read_executor(b.name, b.input or {}, module_name)
                    except Exception as exc:                       # noqa: BLE001
                        logger.warning("read_executor failed for %s: %s", b.name, exc)
                        tool_results.append({"type": "tool_result", "tool_use_id": b.id,
                                             "content": "Daten konnten nicht geladen werden.",
                                             "is_error": True})
                        continue
                    yield {"type": "data", "kind": data.get("kind", ""), "items": data.get("items", [])}
                    tool_results.append({"type": "tool_result", "tool_use_id": b.id,
                                         "content": data.get("result_text", "")})
                elif cls == T.CLIENT:
                    args = T.normalize_client(b.name, b.input or {}, module_name)
                    yield {"type": "client_action", "action": b.name, "args": args}
                    tool_results.append({"type": "tool_result", "tool_use_id": b.id,
                                         "content": "Aktion an die Oberfläche gesendet."})
                else:
                    tool_results.append({"type": "tool_result", "tool_use_id": b.id,
                                         "content": "Unbekanntes Tool.", "is_error": True})
            messages.append({"role": "user", "content": tool_results})

        # Loop ceiling reached without a terminal answer.
        yield {"type": "done"}

    except Exception as exc:                                       # noqa: BLE001
        logger.exception("chat orchestrator failed")
        yield {"type": "error", "detail": str(exc)}
        yield {"type": "done"}
