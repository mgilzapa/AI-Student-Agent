import json
import logging
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_client: AsyncOpenAI | None = None

_SYSTEM_PROMPT = (
    "Du erhältst eine Frage eines Studenten über Lernmaterialien.\n"
    "Generiere 2-3 semantisch verschiedene Suchanfragen, die zusammen die Frage vollständig abdecken.\n"
    'Antworte NUR als JSON: {"queries": ["...", "...", "..."]}\n'
    "Keine anderen Keys, keine Erklärungen."
)


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI()
    return _client


async def decompose(question: str) -> list[str]:
    try:
        resp = await _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=256,
        )
        data = json.loads(resp.choices[0].message.content)
        queries = data.get("queries", [])
        if isinstance(queries, list) and queries:
            return [q for q in queries[:3] if isinstance(q, str) and q.strip()]
    except Exception as exc:
        logger.warning("multi_query decompose failed: %s", exc)
    return [question]
