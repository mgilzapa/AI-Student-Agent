import asyncio
import json
import logging
import os
from pathlib import Path
from typing import AsyncIterator

import openai

from app.llm_clients import make_async_gemini_client
from app.rag.reranker import rerank

logger = logging.getLogger(__name__)

_gemini: openai.AsyncOpenAI | None = None

# Separate env var so RAG synthesis can use a cheaper model than the orchestrator.
_RAG_MODEL = os.getenv("RAG_MODEL", "gemini-2.5-flash-lite")

_SYNTH_SYSTEM_PROMPT = """Du bist ein Lernassistent. Dir werden Textausschnitte aus Studienunterlagen gegeben.
Beantworte die Frage des Studenten basierend auf diesen Ausschnitten.
Du darfst Zusammenhänge ableiten, wenn sie logisch folgen. Erfinde keine Fakten.
Schreibe auf Deutsch.

OBERSTES ZIEL: Maximale Übersichtlichkeit. Der Student soll die Antwort auf einen Blick erfassen können.

VERBOTEN — keine Ausnahmen:
- Kein einleitender Füllsatz. Beginne sofort mit dem Inhalt.
- Kein Fließtext wenn mehr als ein Punkt erklärt wird. Dann immer Aufzählungsliste.
- Keine Wiederholung der Frage.
- Nicht mehr erklären als nötig.

STRUKTUR — strikte Regeln:
- 1 klarer Punkt → 1-2 Sätze Fließtext.
- 2+ Punkte / Merkmale / Schritte / Eigenschaften → Aufzählungsliste (- ...).
- Mehrteilige Antworten → ## Überschrift pro Abschnitt.
- Vergleiche → Markdown-Tabelle.
- **Fettschrift** für Fachbegriffe beim ersten Auftreten.

LÖSUNGEN & ERGEBNISSE — besonders hervorheben:
- Endergebnis / finale Antwort immer in einer eigenen Zeile, abgesetzt und fett: **Ergebnis: ...**
- Rechenschritte als nummerierte Liste (1. 2. 3. ...), nicht als Fließtext.
- Jede Formel abgesetzt als Display-Math: $$...$$
- Nach der Herleitung das Ergebnis nochmal explizit wiederholen: **→ Ergebnis: $$...$$**

MATHEMATIK — strikte Regeln, keine Ausnahmen:
- Jede Formel, jede Variable, jedes Symbol wird AUSSCHLIESSLICH in LaTeX geschrieben.
- Inline: $x$, $\Omega$, $P(\{\\omega\})$
- Abgesetzt (zentriert, eigene Zeile mit Leerzeile davor und danach):

$$P(\{\omega\}) = \frac{1}{|\Omega|}$$

- NIEMALS dieselbe Formel doppelt schreiben (nie LaTeX + Klartext-Kopie danach).
- NIEMALS Unicode-Mathesymbole direkt schreiben: nicht Ω, ∈, ≠, ∣, ⋅ — immer $\Omega$, $\in$, $\neq$, $\mid$, $\cdot$.
- Schreibe "für" nicht als "fu¨r" — nutze korrekte deutsche Umlaute (ä, ö, ü).
- Jede Formel erscheint genau einmal, entweder inline $...$ oder abgesetzt $$...$$, nie beides."""

_DISTANCE_THRESHOLD = 0.6
_TOP_RERANK = 8


def _get_gemini() -> openai.AsyncOpenAI:
    global _gemini
    if _gemini is None:
        _gemini = make_async_gemini_client()
    return _gemini


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

    messages = [
        {"role": "system", "content": _SYNTH_SYSTEM_PROMPT},
        *(chat_history or []),
        {"role": "user", "content": user_message},
    ]

    stream = await _get_gemini().chat.completions.create(
        model=_RAG_MODEL,
        max_tokens=8192,
        messages=messages,
        stream=True,
    )
    async for chunk in stream:
        text = chunk.choices[0].delta.content or ""
        if text:
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

    messages = [
        {"role": "system", "content": _SYNTH_SYSTEM_PROMPT},
        *(chat_history or []),
        {"role": "user", "content": user_message},
    ]

    stream = await _get_gemini().chat.completions.create(
        model=_RAG_MODEL,
        max_tokens=8192,
        messages=messages,
        stream=True,
    )
    async for chunk in stream:
        text = chunk.choices[0].delta.content or ""
        if text:
            yield json.dumps({"type": "token", "content": text})

    yield json.dumps({"type": "done", "sources": sources, "path": "complex"})
