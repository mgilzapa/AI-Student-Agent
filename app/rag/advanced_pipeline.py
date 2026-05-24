import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncIterator

from anthropic import AsyncAnthropic

from app.rag.reranker import rerank

logger = logging.getLogger(__name__)

_anthropic: AsyncAnthropic | None = None

_SYNTH_SYSTEM_PROMPT = (
    "Du bist ein Lernassistent. Dir werden Textausschnitte aus Studienunterlagen gegeben.\n"
    "Beantworte die Frage des Studenten basierend auf diesen Ausschnitten.\n"
    "Du darfst Zusammenhänge und Verbindungen zwischen Konzepten ableiten, wenn sie aus\n"
    "dem Kontext logisch folgen. Erfinde keine Fakten. Wenn du etwas ableitest, mache das\n"
    'transparent ("Aus dem Kontext lässt sich schließen, dass...").\n'
    "Schreibe auf Deutsch. Mathematische Formeln in LaTeX."
)

_DISTANCE_THRESHOLD = 0.6
_TOP_RERANK = 8


def _get_anthropic() -> AsyncAnthropic:
    global _anthropic
    if _anthropic is None:
        _anthropic = AsyncAnthropic()
    return _anthropic


def _dist_to_score(dist: float | None) -> float:
    if dist is None:
        return 0.0
    return round(max(0.0, 1.0 - (dist / 2.0)), 3)


async def run_simple(
    question: str,
    hits: list[dict],
    chat_history: list[dict] | None = None,
) -> AsyncIterator[str]:
    top_chunks = hits[:5] if hits else []
    if not top_chunks:
        yield json.dumps({"type": "token", "content": "Ich konnte keine relevanten Informationen finden."})
        yield json.dumps({"type": "done", "sources": [], "path": "simple"})
        return

    context_block = "\n\n".join(
        f"[Quelle {i + 1}: {Path(hit['source']).name}]\n{hit['text'].strip()}"
        for i, hit in enumerate(top_chunks)
    )
    user_message = (
        f"Frage des Studenten:\n{question.strip()}\n\n"
        f"Lernmaterial-Kontext:\n{context_block}"
    )
    sources = [
        {"source": Path(h["source"]).name, "score": _dist_to_score(h.get("distance"))}
        for h in top_chunks
    ]

    messages = [*(chat_history or []), {"role": "user", "content": user_message}]

    async with _get_anthropic().messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=_SYNTH_SYSTEM_PROMPT,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield json.dumps({"type": "token", "content": text})

    yield json.dumps({"type": "done", "sources": sources, "path": "simple"})


async def run(
    question: str,
    sub_queries: list[str],
    initial_hits: list[dict],
    module_name: str | None,
    rag_service,
    chat_history: list[dict] | None = None,
) -> AsyncIterator[str]:
    async def fetch(q: str) -> list[dict]:
        return await asyncio.to_thread(rag_service.retrieve, q, None, module_name)

    extra_results = await asyncio.gather(*[fetch(q) for q in sub_queries])

    all_hits: list[dict] = list(initial_hits)
    for batch in extra_results:
        all_hits.extend(batch)

    seen: set[str] = set()
    deduped: list[dict] = []
    for hit in all_hits:
        key = hit.get("document_id") or hit.get("text", "")[:80]
        if key not in seen:
            seen.add(key)
            deduped.append(hit)

    filtered = [h for h in deduped if h.get("distance") is not None and h.get("distance") <= _DISTANCE_THRESHOLD]
    if not filtered:
        filtered = deduped[:_TOP_RERANK]

    top_chunks = await rerank(question, filtered, top_n=_TOP_RERANK)

    if not top_chunks:
        yield json.dumps({"type": "token", "content": "Ich konnte keine relevanten Informationen finden."})
        yield json.dumps({"type": "done", "sources": [], "path": "complex"})
        return

    context_block = "\n\n".join(
        f"[Quelle {i + 1}: {Path(hit['source']).name}]\n{hit['text'].strip()}"
        for i, hit in enumerate(top_chunks)
    )
    user_message = (
        f"Frage des Studenten:\n{question.strip()}\n\n"
        f"Lernmaterial-Kontext:\n{context_block}"
    )
    sources = [
        {"source": Path(h["source"]).name, "score": _dist_to_score(h.get("distance"))}
        for h in top_chunks
    ]

    messages = [*(chat_history or []), {"role": "user", "content": user_message}]

    async with _get_anthropic().messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=_SYNTH_SYSTEM_PROMPT,
        messages=messages,
    ) as stream:
        async for text in stream.text_stream:
            yield json.dumps({"type": "token", "content": text})

    yield json.dumps({"type": "done", "sources": sources, "path": "complex"})
