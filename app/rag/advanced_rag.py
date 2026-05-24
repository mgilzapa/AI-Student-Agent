import asyncio
import json
import logging
from typing import AsyncIterator

from app.rag.router import route
from app.rag.multi_query import decompose
from app.rag.advanced_pipeline import run as pipeline_run, run_simple

logger = logging.getLogger(__name__)


async def ask_advanced(
    question: str,
    module_name: str | None = None,
    rag_service=None,
    chat_history: list[dict] | None = None,
) -> AsyncIterator[str]:
    chat_history = chat_history or []
    sub_queries_task = asyncio.create_task(decompose(question))
    initial_hits_task = asyncio.create_task(
        asyncio.to_thread(rag_service.retrieve, question, None, module_name)
    )
    sub_queries, initial_hits = await asyncio.gather(sub_queries_task, initial_hits_task)

    path = await route(question, initial_hits)
    logger.info("ask_advanced route=%s question=%r", path, question[:60])

    if path == "simple":
        async for chunk in run_simple(question, initial_hits, chat_history):
            yield chunk
    else:
        async for chunk in pipeline_run(question, sub_queries, initial_hits, module_name, rag_service, chat_history):
            yield chunk
