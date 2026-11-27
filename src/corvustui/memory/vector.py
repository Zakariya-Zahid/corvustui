"""Vector memory interface for RAG (ChromaDB implementation)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class MemoryEntry:
    """A single memory entry."""

    id: str
    content: str
    metadata: dict[str, Any]
    embedding: list[float] | None = None


class VectorMemory(ABC):
    """Abstract vector memory interface."""

    @abstractmethod
    async def add(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        """Add a memory entry. Returns the entry ID."""
        pass

    @abstractmethod
    async def search(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.7,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[MemoryEntry]:
        """Search for relevant memories."""
        pass

    @abstractmethod
    async def delete(self, entry_id: str) -> bool:
        """Delete a memory entry."""
        pass

    @abstractmethod
    async def clear(self) -> None:
        """Clear all memories."""
        pass

    @abstractmethod
    async def get_stats(self) -> dict[str, Any]:
        """Get memory statistics."""
        pass


class ChromaMemory(VectorMemory):
    """ChromaDB implementation of vector memory.

    ``client`` and ``embedder`` can be injected for tests or for hosts that
    already own those resources.  Without an embedder, ChromaDB's built-in
    embedding function embeds documents and queries, so the optional ``rag``
    extra only needs ``chromadb``.
    """

    def __init__(
        self,
        persist_path: str | None = None,
        collection_name: str = "corvustui_memory",
        embedding_model: str = "all-MiniLM-L6-v2",
        *,
        client: Any | None = None,
        embedder: Any | None = None,
    ):
        self.persist_path = persist_path
        self.collection_name = collection_name
        self.embedding_model = embedding_model
        self._client = client
        self._collection = None
        self._embedder = embedder

    async def _initialize(self) -> None:
        """Lazy initialization."""
        if self._collection is not None:
            return

        if self._client is None:
            try:
                import chromadb
            except ImportError as exc:
                raise RuntimeError(
                    "ChromaDB is not installed. "
                    "Install with: pip install corvustui[rag]"
                ) from exc
            self._client = chromadb.PersistentClient(path=self.persist_path)

        self._collection = self._client.get_or_create_collection(
            self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def _encode(self, text: str) -> list[float]:
        if self._embedder is None:
            raise RuntimeError("ChromaDB embedder was not initialized.")
        if hasattr(self._embedder, "encode"):
            vector = self._embedder.encode(text)
        else:
            vector = self._embedder(text)
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        return list(vector)

    async def add(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        await self._initialize()
        import uuid

        entry_id = str(uuid.uuid4())
        kwargs: dict[str, Any] = {
            "ids": [entry_id],
            "documents": [content],
            "metadatas": [metadata or {}],
        }
        if self._embedder is not None:
            kwargs["embeddings"] = [self._encode(content)]

        self._collection.add(**kwargs)
        return entry_id

    async def search(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.7,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[MemoryEntry]:
        await self._initialize()

        kwargs: dict[str, Any] = {
            "n_results": top_k,
            "include": ["documents", "metadatas", "distances"],
        }
        if self._embedder is not None:
            kwargs["query_embeddings"] = [self._encode(query)]
        else:
            kwargs["query_texts"] = [query]
        if filter_metadata:
            kwargs["where"] = filter_metadata
        results = self._collection.query(**kwargs)

        entries = []
        if results["ids"] and results["ids"][0]:
            for i, entry_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i]
                similarity = 1 - distance  # ChromaDB uses cosine distance

                if similarity >= threshold:
                    entries.append(MemoryEntry(
                        id=entry_id,
                        content=results["documents"][0][i],
                        metadata=results["metadatas"][0][i] or {},
                    ))

        return entries

    async def delete(self, entry_id: str) -> bool:
        await self._initialize()
        try:
            self._collection.delete(ids=[entry_id])
            return True
        except Exception:
            return False

    async def clear(self) -> None:
        await self._initialize()
        self._client.delete_collection(self.collection_name)
        self._collection = self._client.get_or_create_collection(self.collection_name)

    async def get_stats(self) -> dict[str, Any]:
        await self._initialize()
        count = self._collection.count()
        return {
            "total_entries": count,
            "collection_name": self.collection_name,
            "embedding_model": self.embedding_model,
        }


class InMemoryVectorMemory(VectorMemory):
    """In-memory vector memory for testing (no ChromaDB required)."""

    def __init__(self):
        self._entries: dict[str, MemoryEntry] = {}

    async def add(self, content: str, metadata: dict[str, Any] | None = None) -> str:
        import uuid
        entry_id = str(uuid.uuid4())
        self._entries[entry_id] = MemoryEntry(
            id=entry_id,
            content=content,
            metadata=metadata or {},
        )
        return entry_id

    async def search(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.7,
        filter_metadata: dict[str, Any] | None = None,
    ) -> list[MemoryEntry]:
        # Simple keyword-based search for in-memory
        query_words = set(query.lower().split())
        results = []

        for entry in self._entries.values():
            if filter_metadata:
                if not all(entry.metadata.get(k) == v for k, v in filter_metadata.items()):
                    continue

            content_words = set(entry.content.lower().split())
            overlap = len(query_words & content_words)
            if overlap > 0:
                similarity = overlap / len(query_words | content_words)
                if similarity >= threshold:
                    results.append((similarity, entry))

        results.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in results[:top_k]]

    async def delete(self, entry_id: str) -> bool:
        if entry_id in self._entries:
            del self._entries[entry_id]
            return True
        return False

    async def clear(self) -> None:
        self._entries.clear()

    async def get_stats(self) -> dict[str, Any]:
        return {"total_entries": len(self._entries), "type": "in_memory"}