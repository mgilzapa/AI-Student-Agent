"""
Hybrid Router: routes student questions to claude-sonnet-4-6
based on ChromaDB similarity scores.
"""
import logging
from dataclasses import dataclass

from anthropic import Anthropic

logger = logging.getLogger(__name__)

MODEL_SIMPLE = "claude-sonnet-4-6"
MODEL_COMPLEX = "claude-sonnet-4-6"

SIMILARITY_SIMPLE_THRESHOLD = 0.75
SIMILARITY_COMPLEX_THRESHOLD = 0.60

_LLM_ROUTER_PROMPT = """Classify this student question as 'simple' or 'complex'.

SIMPLE: Definition lookups, factual questions covered in context,
        basic concept explanations, yes/no questions.
COMPLEX: Multi-step reasoning, proofs, applying multiple concepts,
         exam problems, questions NOT covered in the context.

Context snippet: {rag_context_snippet}
Question: {user_question}

Reply with only: simple or complex"""


@dataclass
class RouteResult:
    model: str
    route: str
    similarity_score: float
    rag_context: str


class HybridRouter:
    def __init__(self, vector_store, embedder, client: Anthropic, top_k: int = 5):
        self.vector_store = vector_store
        self.embedder = embedder
        self.client = client
        self.top_k = top_k

    def route(self, question: str, module_id: str) -> RouteResult:
        query_embedding = self.embedder.embed(question)

        if not query_embedding:
            logger.warning("Failed to embed question, defaulting to complex")
            return RouteResult(model=MODEL_COMPLEX, route="complex", similarity_score=0.0, rag_context="")

        try:
            results = self.vector_store.search(
                query_embedding=query_embedding,
                n_results=self.top_k,
                where={"module_name": module_id},
            )
        except Exception as exc:
            logger.warning("ChromaDB search failed (%s), defaulting to complex", exc)
            return RouteResult(model=MODEL_COMPLEX, route="complex", similarity_score=0.0, rag_context="")

        distances = results.get("distances", [])
        documents = results.get("documents", [])

        if not distances:
            logger.warning("No ChromaDB results for module=%s, defaulting to complex", module_id)
            return RouteResult(model=MODEL_COMPLEX, route="complex", similarity_score=0.0, rag_context="")

        # Convert L2 distance to similarity score (same formula as RAGQueryService)
        similarity_score = max(0.0, 1.0 - (distances[0] / 2.0))
        rag_context = "\n\n".join(doc.strip() for doc in documents if doc)

        if similarity_score >= SIMILARITY_SIMPLE_THRESHOLD:
            route, model = "simple", MODEL_SIMPLE
        elif similarity_score < SIMILARITY_COMPLEX_THRESHOLD:
            route, model = "complex", MODEL_COMPLEX
        else:
            route, model = self._llm_route(question, rag_context[:500])

        logger.info("Routed question to %s (route=%s, score=%.3f)", model, route, similarity_score)
        return RouteResult(model=model, route=route, similarity_score=similarity_score, rag_context=rag_context)

    def _llm_route(self, question: str, rag_context_snippet: str) -> tuple:
        prompt = _LLM_ROUTER_PROMPT.format(
            rag_context_snippet=rag_context_snippet,
            user_question=question,
        )
        response = self.client.messages.create(
            model=MODEL_SIMPLE,
            max_tokens=5,
            messages=[{"role": "user", "content": prompt}],
        )
        classification = response.content[0].text.strip().lower()
        model = MODEL_SIMPLE if "simple" in classification else MODEL_COMPLEX
        return "grauzone", model
