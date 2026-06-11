"""Central LLM client construction.

DeepSeek is called *through* the Anthropic SDK (its Messages API is
Anthropic-compatible), so it needs its OWN key + base_url — distinct from the
real Anthropic key. Both cannot share ANTHROPIC_API_KEY: a bare `Anthropic()`
reads a single global key + base_url from the environment, so the chat path
(DeepSeek) and the file-generation path (real Claude) would collide on one
endpoint. We therefore build DeepSeek clients with explicit credentials and
leave the real-Claude clients to the SDK's default env lookup.

Env vars:
  ANTHROPIC_API_KEY   real Claude key   — file generation (exam/roadmap/quiz/…),
                                          router, solver, summaries
  DEEPSEEK_API_KEY    DeepSeek key      — chat orchestrator + RAG synthesis
  DEEPSEEK_BASE_URL   DeepSeek's Anthropic-compatible endpoint

If the DeepSeek vars are unset the client falls back to the SDK defaults
(ANTHROPIC_API_KEY / api.anthropic.com), which makes a misconfiguration fail
loudly — a DeepSeek model name sent to the real Anthropic endpoint errors out
clearly rather than silently billing the wrong provider.
"""
from __future__ import annotations

import os

from anthropic import Anthropic, AsyncAnthropic


def _deepseek_kwargs() -> dict:
    kwargs: dict = {}
    key = os.getenv("DEEPSEEK_API_KEY")
    base_url = os.getenv("DEEPSEEK_BASE_URL")
    if key:
        kwargs["api_key"] = key
    if base_url:
        kwargs["base_url"] = base_url
    return kwargs


def make_deepseek_client() -> Anthropic:
    """Sync Anthropic-SDK client pointed at DeepSeek's compatible endpoint."""
    return Anthropic(**_deepseek_kwargs())


def make_async_deepseek_client() -> AsyncAnthropic:
    """Async variant for the streaming RAG synthesis path."""
    return AsyncAnthropic(**_deepseek_kwargs())
