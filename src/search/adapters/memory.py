"""In-memory implementation of the SearchIndexer port for unit tests."""

from __future__ import annotations

import copy
from typing import Any

from search.ports.indexer import SearchDocument, SearchIndexer


class InMemorySearchIndexer(SearchIndexer):
    """In-memory search indexer for fast, isolated unit tests."""

    def __init__(self) -> None:
        # storage: {index_alias: {doc_id: (doc_body, version)}}
        self._indices: dict[str, dict[str, tuple[dict[str, Any], int]]] = {}
        self._connected: bool = True

    async def index(self, documents: tuple[SearchDocument, ...]) -> None:
        """Store or update documents in memory with optimistic version check."""
        if not self._connected:
            raise RuntimeError("InMemorySearchIndexer is closed")

        for doc in documents:
            alias = doc.index_alias
            if alias not in self._indices:
                self._indices[alias] = {}

            current = self._indices[alias].get(doc.document_id)
            if current is not None:
                _, current_ver = current
                # external_gte semantics: ignore if incoming version is lower than stored version
                if doc.version < current_ver:
                    continue

            self._indices[alias][doc.document_id] = (copy.deepcopy(doc.body), doc.version)

    async def delete(self, *, index_alias: str, document_ids: tuple[str, ...]) -> None:
        """Idempotently delete documents by ID from the in-memory index."""
        if not self._connected:
            raise RuntimeError("InMemorySearchIndexer is closed")

        if index_alias in self._indices:
            for doc_id in document_ids:
                self._indices[index_alias].pop(doc_id, None)

    async def get_document(
        self,
        *,
        index_alias: str,
        document_id: str,
    ) -> dict[str, object] | None:
        """Retrieve a stored document by id."""
        if not self._connected:
            raise RuntimeError("InMemorySearchIndexer is closed")

        entry = self._indices.get(index_alias, {}).get(document_id)
        if entry is None:
            return None
        return copy.deepcopy(entry[0])

    async def ensure_index(
        self,
        *,
        index_name: str,
        mappings: dict[str, object],
        aliases: tuple[str, ...] = (),
    ) -> bool:
        """Ensure an in-memory index and aliases exist."""
        if not self._connected:
            raise RuntimeError("InMemorySearchIndexer is closed")

        created = index_name not in self._indices
        if created:
            self._indices[index_name] = {}
        for alias in aliases:
            if alias not in self._indices:
                self._indices[alias] = self._indices[index_name]
        return created

    async def ping(self) -> bool:
        """Health check."""
        return self._connected

    async def close(self) -> None:
        """Clean up resources."""
        self._connected = False

    def clear(self) -> None:
        """Helper for test cleanup."""
        self._indices.clear()
