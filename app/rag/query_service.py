"""
RAG Query Service for grounded Q&A.

Uses OpenAI embeddings for retrieval and GPT-4o-mini for response generation.
"""
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
from openai import OpenAI

logger = logging.getLogger(__name__)


class RAGQueryService:
    """
    Retrieval-Augmented Generation service for study materials.

    Features:
    - Semantic search using OpenAI embeddings
    - Grounded Q&A with source attribution
    - Configurable context window (top_k chunks)
    """

    SYSTEM_PROMPT = """
Du bist ein praeziser Lernassistent fuer Studenten.
Beantworte die Frage des Studenten direkt auf Basis des unten gelieferten Kontexts.

Regeln:
- Beantworte zuerst genau das, was gefragt wurde. Schweife nicht ab.
- Nutze ausschliesslich Informationen aus dem Kontext. Erfinde nichts.
- Wenn der Kontext die Frage nicht beantwortet, sage das explizit ("Im Lernmaterial finde ich dazu nichts").
- Bei Ueberblicks- oder Klausurfragen ("Welche Themen...", "Was kommt dran...") liste die im Kontext gefundenen Punkte sauber auf.
- Bei spezifischen Fragen (Definition, Beispiel, Berechnung): kurze, fokussierte Antwort, kein Abschweifen in andere Themen.

Format:
- Schreibe auf Deutsch, sachlich.
- Verwende kurze Absaetze und Bindestrich-Listen.
- Verweise auf Quellen nur ueber den Dateinamen (z.B. "siehe Exercise 2.pdf"). Niemals vollstaendige Dateipfade ausgeben.
- Mathematische Formeln IMMER in LaTeX: $...$ inline, $$...$$ fuer eigenstaendige Formeln. Kein Pseudo-ASCII (kein `sum`, kein `^2` ohne Klammern, kein `->`); nutze \\sum, \\frac, \\sqrt, \\alpha, \\to usw.
""".strip()

    def __init__(
        self,
        vector_store,
        embedder,
        chat_model: str = "gpt-4o-mini",
        embed_model: str = "text-embedding-3-small",
        top_k: int = 8
    ):
        """
        Initialize the RAG service.

        Args:
            vector_store: ChromaVectorStore instance
            embedder: Embedder instance for query embeddings
            chat_model: OpenAI model for response generation
            embed_model: OpenAI model for embeddings
            top_k: Number of chunks to retrieve per query
        """
        self.vector_store = vector_store
        self.embedder = embedder
        self.chat_model = chat_model
        self.embed_model = embed_model
        self.top_k = top_k
        self._client = None

    @property
    def client(self) -> OpenAI:
        """Lazy-load OpenAI client."""
        if self._client is None:
            self._client = OpenAI()
        return self._client

    def retrieve(
        self,
        query: str,
        top_k: Optional[int] = None,
        module_name: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve relevant chunks for a query.

        Module filter is forgiving: exact match first, then case-insensitive
        fallback over a broader candidate set, then a source-path containment
        fallback so legacy chunks (indexed before module_name was tracked) are
        still reachable.
        """
        top_k = top_k or self.top_k

        query_embedding = self.embedder.embed(query)
        if not query_embedding:
            logger.warning("Failed to generate query embedding")
            return []

        def _format(results: Dict[str, Any]) -> List[Dict[str, Any]]:
            out = []
            for doc, dist, meta in zip(
                results.get("documents", []),
                results.get("distances", []),
                results.get("metadatas", []),
            ):
                meta = meta or {}
                out.append({
                    "text": doc,
                    "source": meta.get("source", "unknown"),
                    "module_name": meta.get("module_name", ""),
                    "distance": float(dist) if dist is not None else None,
                    "document_id": meta.get("document_id", ""),
                })
            return out

        hits: List[Dict[str, Any]] = []

        if module_name:
            # 1) Strict exact-match filter (fast path).
            strict = _format(self.vector_store.search(
                query_embedding=query_embedding,
                n_results=top_k,
                where={"module_name": module_name},
            ))
            hits = strict

            # 2) Fallback: broaden the candidate pool and match by either
            #    case-insensitive module_name OR module name appearing in the
            #    source path (covers legacy chunks with empty module_name).
            if not hits:
                mod_norm = module_name.lower()
                broad = _format(self.vector_store.search(
                    query_embedding=query_embedding,
                    n_results=top_k * 4,
                    where=None,
                ))
                hits = [
                    h for h in broad
                    if h["module_name"].lower() == mod_norm
                    or mod_norm in (h["source"] or "").lower()
                ][:top_k]
        else:
            hits = _format(self.vector_store.search(
                query_embedding=query_embedding,
                n_results=top_k,
                where=None,
            ))

        logger.info(
            "Retrieved %d chunks for query=%r module=%r",
            len(hits), query[:60], module_name,
        )
        return hits

    def ask(
        self,
        question: str,
        top_k: Optional[int] = None,
        module_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Answer a question using retrieval-augmented generation.

        Args:
            question: User question
            top_k: Number of chunks to retrieve

        Returns:
            Dict with answer and sources
        """
        hits = self.retrieve(question, top_k=top_k, module_name=module_name)

        if not hits:
            return {
                "answer": "Ich konnte keine relevanten Informationen in den Lernmaterialien finden.",
                "sources": []
            }

        context_block = "\n\n".join(
            f"[Quelle {i + 1}: {Path(hit['source']).name or 'unbekannt'}]\n{hit['text'].strip()}"
            for i, hit in enumerate(hits)
        )

        user_message = (
            f"Frage des Studenten:\n{question.strip()}\n\n"
            f"Lernmaterial-Kontext (nur diese Quellen verwenden):\n"
            f"{context_block}\n\n"
            f"Aufgabe: Beantworte die obige Frage praezise und ausschliesslich auf Basis des Kontexts. "
            f"Wenn die Frage eine Auflistung verlangt, liste auf; sonst gib eine fokussierte Antwort. "
            f"Wenn der Kontext nicht reicht, sage das ehrlich. "
            f"Verweise auf Quellen nur ueber den Dateinamen, nicht ueber Pfade."
        )

        response = self.client.chat.completions.create(
            model=self.chat_model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.2,
        )

        return {
            "answer": response.choices[0].message.content,
            "sources": [
                {
                    "source": Path(hit["source"]).name or "unbekannt",
                    "score": (
                        round(max(0.0, 1.0 - (hit["distance"] / 2.0)), 3)
                        if hit["distance"] is not None else 0.0
                    ),
                }
                for hit in hits
            ],
        }

    def evaluate(
        self,
        questions: List[str],
        top_k: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Evaluate RAG service on multiple questions.

        Args:
            questions: List of test questions
            top_k: Number of chunks to retrieve per question

        Returns:
            List of results with question, answer, and sources
        """
        results = []
        for question in questions:
            logger.info(f"Evaluating: {question}")
            result = self.ask(question, top_k=top_k)
            result["question"] = question
            results.append(result)
        return results


def create_query_service(
    vector_store,
    embedder,
    top_k: int = 8
) -> RAGQueryService:
    """
    Factory function to create a RAG query service.

    Args:
        vector_store: ChromaVectorStore instance
        embedder: Embedder instance
        top_k: Default number of chunks to retrieve

    Returns:
        Configured RAGQueryService
    """
    return RAGQueryService(
        vector_store=vector_store,
        embedder=embedder,
        top_k=top_k
    )
