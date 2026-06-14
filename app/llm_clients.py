"""Central LLM client construction.

Both the chat orchestrator and RAG synthesis use Google Gemini via the
OpenAI-compatible endpoint, so a single openai.OpenAI / openai.AsyncOpenAI
client (pointed at Google's base URL) handles everything.

Env vars:
  GEMINI_API_KEY    Google AI Studio key — chat orchestrator + RAG synthesis
"""
from __future__ import annotations

import os

import openai

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def _gemini_kwargs() -> dict:
    kwargs: dict = {"base_url": _GEMINI_BASE_URL}
    key = os.getenv("GEMINI_API_KEY")
    if key:
        kwargs["api_key"] = key
    return kwargs


def make_gemini_client() -> openai.OpenAI:
    """Sync OpenAI-compat client pointed at Gemini (used by the chat orchestrator)."""
    return openai.OpenAI(**_gemini_kwargs())


def make_async_gemini_client() -> openai.AsyncOpenAI:
    """Async variant for the streaming RAG synthesis path."""
    return openai.AsyncOpenAI(**_gemini_kwargs())
