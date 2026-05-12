"""
Credit tracking for model usage.
"""

CREDIT_RATES = {
    "gpt-4o-mini": {
        "simple": 1,
    },
    "gpt-4o": {
        "complex": 5,
        "exercise_sheet": 15,
        "roadmap": 20,
    },
}


def calculate_credits(tokens: int, model: str) -> int:
    """Return credit cost for a given token count and model."""
    if model == "gpt-4o-mini":
        return max(1, tokens // 500)
    if model == "gpt-4o":
        return max(5, tokens // 100)
    return max(1, tokens // 500)
