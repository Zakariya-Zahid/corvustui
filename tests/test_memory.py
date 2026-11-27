"""Tests for vector memory backends, including the Chroma adapter."""

from __future__ import annotations

import pytest

from corvustui.memory.vector import ChromaMemory, InMemoryVectorMemory


class FakeVector:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return self._values


class FakeEmbedder:
    def __init__(self) -> None:
        self.encoded: list[str] = []

    def encode(self, text: str) -> FakeVector:
        self.encoded.append(text)
        lowered = text.lower()
        return FakeVector(
            [
                float(lowered.count("python")),
                float(lowered.count("javascript")),
                1.0,
            ]
        )


class FakeCollection:
    def __init__(self) -> None:
        self.rows: dict[str, tuple[str, dict, list[float]]] = {}
        self.last_query: dict | None = None

    def add(self, ids, documents, embeddings, metadatas):
        for entry_id, document, embedding, metadata in zip(
            ids, documents, embeddings, metadatas
        ):
            self.rows[entry_id] = (document, metadata, embedding)

    def query(self, **kwargs):
        self.last_query = kwargs
        query = kwargs["query_embeddings"][0]
        scored = []
        for entry_id, (document, metadata, embedding) in self.rows.items():
            where = kwargs.get("where")
            if where and any(metadata.get(key) != value for key, value in where.items()):
                continue
            distance = sum(
                abs(left - right) for left, right in zip(query, embedding)
            )
            scored.append((distance, entry_id, document, metadata))
        scored.sort(key=lambda item: item[0])
        top = scored[: kwargs["n_results"]]
        return {
            "ids": [[item[1] for item in top]],
            "documents": [[item[2] for item in top]],
            "metadatas": [[item[3] for item in top]],
            "distances": [[item[0] for item in top]],
        }

    def delete(self, ids):
        for entry_id in ids:
            self.rows.pop(entry_id, None)

    def count(self) -> int:
        return len(self.rows)


class FakeClient:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def get_or_create_collection(self, name, **kwargs):
        return self.collections.setdefault(name, FakeCollection())

    def delete_collection(self, name):
        self.collections.pop(name, None)


def make_memory() -> tuple[ChromaMemory, FakeClient, FakeEmbedder]:
    client = FakeClient()
    embedder = FakeEmbedder()
    memory = ChromaMemory(
        collection_name="test_memory",
        client=client,
        embedder=embedder,
    )
    return memory, client, embedder


@pytest.mark.asyncio
async def test_chroma_add_search_delete_and_stats():
    memory, client, embedder = make_memory()

    python_id = await memory.add("Python is a programming language", {"topic": "python"})
    await memory.add("JavaScript is for web development", {"topic": "javascript"})

    assert embedder.encoded == [
        "Python is a programming language",
        "JavaScript is for web development",
    ]

    results = await memory.search("python language", top_k=2, threshold=0.0)
    assert [entry.content for entry in results] == [
        "Python is a programming language"
    ]
    assert results[0].metadata == {"topic": "python"}

    javascript = await memory.search("javascript web", top_k=2, threshold=0.0)
    assert [entry.content for entry in javascript] == [
        "JavaScript is for web development"
    ]

    filtered = await memory.search(
        "python language",
        top_k=2,
        threshold=0.0,
        filter_metadata={"topic": "python"},
    )
    assert [entry.id for entry in filtered] == [python_id]
    assert client.collections["test_memory"].last_query["where"] == {"topic": "python"}

    stats = await memory.get_stats()
    assert stats == {
        "total_entries": 2,
        "collection_name": "test_memory",
        "embedding_model": "all-MiniLM-L6-v2",
    }

    assert await memory.delete(python_id) is True
    assert (await memory.get_stats())["total_entries"] == 1


@pytest.mark.asyncio
async def test_chroma_threshold_filters_and_clear_recreates_collection():
    memory, client, _ = make_memory()
    await memory.add("Python is a programming language", {"topic": "python"})

    high = await memory.search("language", threshold=0.99)
    assert high == []

    await memory.clear()
    assert (await memory.get_stats())["total_entries"] == 0
    assert "test_memory" in client.collections


@pytest.mark.asyncio
async def test_chroma_accepts_plain_callable_embedder():
    client = FakeClient()
    memory = ChromaMemory(
        collection_name="callable_memory",
        client=client,
        embedder=lambda text: [1.0, 0.0],
    )

    await memory.add("anything")
    results = await memory.search("anything", threshold=0.0)

    assert len(results) == 1


@pytest.mark.asyncio
async def test_chroma_reports_missing_dependencies(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name in {"chromadb", "sentence_transformers"}:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blocked_import)
    memory = ChromaMemory()

    with pytest.raises(RuntimeError, match="ChromaDB is not installed"):
        await memory.get_stats()


@pytest.mark.asyncio
async def test_chroma_persists_with_real_client(tmp_path):
    chromadb = pytest.importorskip("chromadb")

    path = str(tmp_path / "chroma")
    embedder = FakeEmbedder()
    first = ChromaMemory(
        persist_path=path,
        collection_name="persisted",
        client=chromadb.PersistentClient(path=path),
        embedder=embedder,
    )
    entry_id = await first.add("Python is a programming language", {"topic": "python"})

    second = ChromaMemory(
        persist_path=path,
        collection_name="persisted",
        client=chromadb.PersistentClient(path=path),
        embedder=embedder,
    )
    results = await second.search("python language", threshold=0.0)
    stats = await second.get_stats()

    assert stats["total_entries"] == 1
    assert [entry.id for entry in results] == [entry_id]
    assert results[0].content == "Python is a programming language"
    assert results[0].metadata == {"topic": "python"}


@pytest.mark.asyncio
async def test_in_memory_metadata_filter_and_delete():
    memory = InMemoryVectorMemory()
    entry_id = await memory.add("python programming", {"kind": "note"})
    await memory.add("python snake", {"kind": "animal"})

    results = await memory.search(
        "python programming",
        threshold=0.1,
        filter_metadata={"kind": "note"},
    )
    assert [entry.id for entry in results] == [entry_id]

    assert await memory.delete(entry_id) is True
    assert await memory.delete("missing") is False
    await memory.clear()
    assert (await memory.get_stats())["total_entries"] == 0
