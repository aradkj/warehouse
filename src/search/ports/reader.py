"""Authoritative read port for search indexing and reconciliation."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from search.ports.indexer import SearchDocument


@runtime_checkable
class CatalogSearchReader(Protocol):
    """Port for streaming catalog products from authoritative PostgreSQL persistence."""

    async def get_search_document(
        self, *, tenant_id: str, product_id: str, target_alias: str = "products"
    ) -> SearchDocument | None:
        """Fetch a single complete product as a SearchDocument from storage."""
        ...

    def stream_search_documents(
        self,
        *,
        tenant_id: str | None = None,
        target_alias: str = "products",
        batch_size: int = 200,
    ) -> AsyncIterator[tuple[SearchDocument, ...]]:
        """Asynchronously stream batches of SearchDocuments from PostgreSQL."""
        ...

    async def count_authoritative_products(self, *, tenant_id: str | None = None) -> int:
        """Return total count of products in authoritative database."""
        ...

    async def get_authoritative_keys(
        self, *, tenant_id: str | None = None
    ) -> dict[str, tuple[str, int]]:
        """Fetch mapping of doc_id -> (tenant_id, version) from database."""
        ...

    def stream_authoritative_keys(
        self,
        *,
        tenant_id: str | None = None,
        batch_size: int = 500,
    ) -> AsyncIterator[dict[str, tuple[str, int]]]:
        """Stream chunks of doc_id -> (tenant_id, version) using keyset pagination."""
        ...


__all__ = ["CatalogSearchReader"]
