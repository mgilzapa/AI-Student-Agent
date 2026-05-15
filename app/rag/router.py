from typing import Literal

_COMPLEX_KEYWORDS = {
    "zusammenhang", "unterschied", "vergleiche", "warum", "beziehung",
    "erkläre", "wie hängt", "inwiefern", "gemeinsam", "verbindung",
    "gemeinsamkeit", "gegenüber",
}

_SCORE_THRESHOLD = 0.72


async def route(question: str, initial_hits: list[dict]) -> Literal["simple", "complex"]:
    q_lower = question.lower()
    for kw in _COMPLEX_KEYWORDS:
        if kw in q_lower:
            return "complex"

    if initial_hits:
        dist = initial_hits[0].get("distance")
        if dist is not None:
            score = max(0.0, 1.0 - (dist / 2.0))
            return "simple" if score >= _SCORE_THRESHOLD else "complex"

    return "complex"
