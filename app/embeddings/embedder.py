"""
Embedding generation using OpenAI API.

Provides a simple interface for converting text chunks into
dense vector embeddings suitable for semantic search.
"""
import logging
from typing import List
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

logger = logging.getLogger(__name__)


class Embedder:
    """
    Embedding generator using OpenAI API.

    Uses the text-embedding-3-small model by default:
    - Fast inference
    - 1536-dimensional embeddings
    - High quality for semantic search
    """

    def __init__(self, model_name: str = "text-embedding-3-small"):
        """
        Initialize the embedding model.

        Args:
            model_name: Name of the OpenAI embedding model
        """
        self.model_name = model_name
        self._client = None

    @property
    def client(self) -> OpenAI:
        """Lazy-load the OpenAI client on first use."""
        if self._client is None:
            logger.info(f"Initializing OpenAI client for model: {self.model_name}")
            self._client = OpenAI()
            logger.info("OpenAI client ready")
        return self._client

    def embed(self, text: str) -> List[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Text to embed

        Returns:
            List of floats (embedding vector)
        """
        if not text or not text.strip():
            logger.warning("Empty text passed for embedding")
            return []

        response = self.client.embeddings.create(
            input=[text.strip()],
            model=self.model_name
        )
        embedding = response.data[0].embedding
        return embedding

    def embed_batch(self, texts: List[str], batch_size: int = 100) -> List[List[float]]:
        """
        Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed
            batch_size: Batch size for encoding

        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        # Filter empty texts
        valid_texts = [t for t in texts if t and t.strip()]
        if not valid_texts:
            return [[] for _ in texts]

        logger.info(f"Embedding {len(valid_texts)} texts in batches of {batch_size}")
        all_embeddings = []

        for i in range(0, len(valid_texts), batch_size):
            batch = valid_texts[i : i + batch_size]
            response = self.client.embeddings.create(
                input=batch,
                model=self.model_name
            )
            all_embeddings.extend([e.embedding for e in response.data])
            logger.info(f"  embedded {min(i + batch_size, len(valid_texts))}/{len(valid_texts)}")

        return all_embeddings


# Global embeder instance for convenience
_default_embedder = None


def get_embedder(model_name: str = "text-embedding-3-small") -> Embedder:
    """Get or create the default embedder instance."""
    global _default_embedder
    if _default_embedder is None or _default_embedder.model_name != model_name:
        _default_embedder = Embedder(model_name)
    return _default_embedder


def embed_text(text: str, model_name: str = "text-embedding-3-small") -> List[float]:
    """
    Convenience function to embed a single text.

    Args:
        text: Text to embed
        model_name: Model to use

    Returns:
        Embedding vector as list of floats
    """
    embedder = get_embedder(model_name)
    return embedder.embed(text)
