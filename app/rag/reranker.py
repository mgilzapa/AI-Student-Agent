import json
import logging
from pathlib import Path
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI()
    return _client


async def rerank(question: str, chunks: list[dict], top_n: int = 5) -> list[dict]:
    if not chunks:
        return []
    if len(chunks) <= top_n:
        return chunks

    chunks_text = "\n\n".join(
        f"[Chunk {i}] {Path(c.get('source', '?')).name}\n{c['text'][:300]}"
        for i, c in enumerate(chunks)
    )
    prompt = (
        f"Frage: {question}\n\n"
        "Bewerte jeden Chunk auf Relevanz für die Frage (Score 1-10).\n"
        'Antworte NUR als JSON: {"scores": [<int>, ...]}\n'
        "Reihenfolge muss exakt der Chunk-Reihenfolge entsprechen.\n\n"
        f"{chunks_text}"
    )

    try:
        resp = await _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            max_tokens=256,
        )
        data = json.loads(resp.choices[0].message.content)
        scores = data.get("scores", [])
        if isinstance(scores, list) and len(scores) == len(chunks):
            ranked = sorted(zip(scores, chunks), key=lambda x: x[0], reverse=True)
            return [c for _, c in ranked[:top_n]]
    except Exception as exc:
        logger.warning("reranker failed: %s", exc)

    return chunks[:top_n]
