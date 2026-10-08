"""Search index port - **placeholder for Phase 3**.

Phase 3 adds Elasticsearch projections for orders, products and audit records.
Phase 1 intentionally implements none of it: search must never be a source of
truth for transactional decisions, and there is nothing to project from until the
transactional outbox (Phase 2) exists.

The port is declared now so that the dependency direction is fixed in advance:
application code will depend on ``SearchIndexer``, and the Elasticsearch adapter
will implement it in Phase 3 - the domain will never know.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SearchDocument:
    """A denormalised document awaiting indexing (Phase 3)."""

    index_alias: str
    document_id: str
    tenant_id: str
    version: int
    body: dict[str, object]


@runtime_checkable
class SearchIndexer(Protocol):
    """Outbound port: write denormalised documents to the search engine."""

    async def index(self, documents: tuple[SearchDocument, ...]) -> None:
        """Index one or more documents into the target search index."""
        ...

    async def delete(self, *, index_alias: str, document_ids: tuple[str, ...]) -> None:
        """Delete one or more documents by ID from the search index."""
        ...

    async def get_document(
        self, *, index_alias: str, document_id: str
    ) -> dict[str, object] | None:
        """Retrieve a raw document by its identifier, or None if not found."""
        ...

    async def ensure_index(
        self,
        *,
        index_name: str,
        mappings: dict[str, object],
        aliases: tuple[str, ...] = (),
    ) -> bool:
        """Idempotently initialize an index with mappings and aliases if not present.

        Returns True if created, False if already existed.
        """
        ...

    async def ping(self) -> bool:
        """Verify search engine connectivity."""
        ...

    async def close(self) -> None:
        """Cleanly close connection pools."""
        ...


PLANNED_PHASE = 3

__all__ = ["PLANNED_PHASE", "SearchDocument", "SearchIndexer"]
