"""
RAG Query Service for grounded Q&A.

Uses OpenAI embeddings for retrieval and GPT-4o-mini for response generation.
"""
import logging
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
Du bist ein intelligenter Lernassistent fuer Studenten.
Beantworte Fragen ausschliesslich auf Basis des bereitgestellten Kontexts.
Bei Klausurfragen: Liste alle relevanten Themen, Definitionen und Aufgabentypen auf, die du im Kontext findest.
Sei so vollstaendig wie moeglich, damit der Student optimal vorbereitet ist.
Wenn der Kontext die Frage nicht vollstaendig beantwortet, sage das explizit.

Formatiere jede Antwort klar und gut lesbar:
- Nutze kurze Absaetze mit sichtbaren Zeilenumbruechen.
- Wenn sinnvoll, nutze Abschnitte wie "Kurzantwort", "Wichtige Punkte", "Pruefungsrelevant" und "Quellen".
- Verwende fuer Aufzaehlungen Bindestriche.
- Vermeide lange Textbloecke.
- Gib lieber mehrere kurze Punkte als einen einzigen langen Absatz.

Schreibe auf Deutsch und bleibe sachlich, hilfreich und uebersichtlich.
""".strip()

    def __init__(
        self,
        vector_store,
        embedder,
        chat_model: str = "gpt-4o-mini",
        embed_model: str = "text-embedding-3-small",
        top_k: int = 5
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

    def retrieve(self, query: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Retrieve relevant chunks for a query.

        Args:
            query: Search query
            top_k: Number of results (overrides default if provided)

        Returns:
            List of dicts with text, source, and score
        """
        top_k = top_k or self.top_k

        query_embedding = self.embedder.embed(query)

        if not query_embedding:
            logger.warning("Failed to generate query embedding")
            return []

        results = self.vector_store.search(
            query_embedding=query_embedding,
            n_results=top_k
        )

        hits = []
        for doc, dist, meta in zip(
            results.get("documents", []),
            results.get("distances", []),
            results.get("metadatas", [])
        ):
            hits.append({
                "text": doc,
                "source": meta.get("source", "unknown") if meta else "unknown",
                "score": round(1 - dist, 3) if dist is not None else 0.0,
                "document_id": meta.get("document_id", "") if meta else "",
            })

        logger.info(f"Retrieved {len(hits)} chunks for query: {query[:50]}...")
        return hits

    def ask(self, question: str, top_k: Optional[int] = None) -> Dict[str, Any]:
        """
        Answer a question using retrieval-augmented generation.

        Args:
            question: User question
            top_k: Number of chunks to retrieve

        Returns:
            Dict with answer and sources
        """
        hits = self.retrieve(question, top_k=top_k)

        if not hits:
            return {
                "answer": "Ich konnte keine relevanten Informationen in den Lernmaterialien finden.",
                "sources": []
            }

        context_block = "\n\n".join(
            f"[Quelle {i + 1}: {hit['source']}]\n{hit['text']}"
            for i, hit in enumerate(hits)
        )

        user_message = f"Kontext:\n{context_block}\n\nFrage: {question}"

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
            "sources": [{"source": hit["source"], "score": hit["score"]} for hit in hits],
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
    top_k: int = 5
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
