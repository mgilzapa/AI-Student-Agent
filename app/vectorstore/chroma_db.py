"""
ChromaDB vector storage for semantic search.

Provides persistent storage and retrieval of document embeddings.
"""
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)


class ChromaVectorStore:
    """
    ChromaDB-backed vector store for document embeddings.

    Supports:
    - Persistent storage to disk
    - Metadata filtering
    - Similarity search
    - Batch operations
    """

    def __init__(self, persist_dir: Optional[Path] = None, collection_name: str = "study_docs"):
        """
        Initialize the vector store.

        Args:
            persist_dir: Directory for persistent storage (None = in-memory)
            collection_name: Name of the Chroma collection
        """
        self.collection_name = collection_name
        self._client = None
        self._collection = None
        self._persist_dir = persist_dir

    @property
    def client(self) -> chromadb.ClientAPI:
        """Get or create Chroma client."""
        if self._client is None:
            if self._persist_dir:
                logger.info(f"Using persistent ChromaDB at: {self._persist_dir}")
                settings = Settings(
                    persist_directory=str(self._persist_dir),
                    anonymized_telemetry=False
                )
                self._client = chromadb.PersistentClient(settings=settings)
            else:
                logger.info("Using in-memory ChromaDB")
                self._client = chromadb.Client()
        return self._client

    @property
    def collection(self):
        """Get or create the collection."""
        if self._collection is None:
            # Get or create collection
            self._collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"description": "Study document embeddings for RAG"}
            )
            logger.info(f"Collection '{self.collection_name}' ready")
        return self._collection

    def add(
        self,
        ids: List[str],
        embeddings: List[List[float]],
        documents: List[str],
        metadatas: Optional[List[Dict[str, Any]]] = None
    ) -> None:
        """
        Add embeddings to the vector store.

        Args:
            ids: Unique IDs for each embedding
            embeddings: List of embedding vectors
            documents: Original text for each embedding
            metadatas: Optional metadata for each embedding
        """
        if not ids or not embeddings:
            logger.warning("No embeddings to add")
            return

        if metadatas is None:
            metadatas = [{} for _ in ids]

        # Ensure all lists are same length
        min_len = min(len(ids), len(embeddings), len(documents), len(metadatas))
        ids = ids[:min_len]
        embeddings = embeddings[:min_len]
        documents = documents[:min_len]
        metadatas = metadatas[:min_len]

        logger.info(f"Adding {len(ids)} embeddings to vector store")
        self.collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas
        )
        logger.info(f"Vector store now has {self.collection.count()} items")

    def search(
        self,
        query_embedding: List[float],
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Search for similar embeddings.

        Args:
            query_embedding: Query vector
            n_results: Number of results to return
            where: Optional metadata filter

        Returns:
            Dictionary with ids, distances, documents, metadatas
        """
        if not query_embedding:
            logger.warning("Empty query embedding")
            return {"ids": [], "distances": [], "documents": [], "metadatas": []}

        results = self.collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where=where,
            include=["documents", "distances", "metadatas"]
        )

        # Flatten results (query returns nested lists)
        return {
            "ids": results["ids"][0] if results["ids"] else [],
            "distances": results["distances"][0] if results["distances"] else [],
            "documents": results["documents"][0] if results["documents"] else [],
            "metadatas": results["metadatas"][0] if results["metadatas"] else [],
        }

    def count(self) -> int:
        """Return total number of items in the store."""
        return self.collection.count()

    def delete(self, ids: Optional[List[str]] = None, where: Optional[Dict[str, Any]] = None) -> None:
        """
        Delete items from the store.

        Args:
            ids: Specific IDs to delete
            where: Metadata filter for bulk deletion
        """
        if ids:
            logger.info(f"Deleting {len(ids)} items by ID")
            self.collection.delete(ids=ids)
        elif where:
            logger.info(f"Deleting items by filter: {where}")
            self.collection.delete(where=where)

    def clear(self) -> None:
        """Delete all items from the collection."""
        logger.info("Clearing vector store")
        self.client.delete_collection(self.collection_name)
        self._collection = None


# Global vector store instance
_default_store = None


def get_vectorstore(persist_dir: Optional[Path] = None, collection_name: str = "study_docs") -> ChromaVectorStore:
    """Get or create the default vector store."""
    global _default_store
    if _default_store is None:
        _default_store = ChromaVectorStore(persist_dir, collection_name)
    return _default_store
