"""Search index port - search queries, projections, and index operations.

Phase 3 adds Elasticsearch projections for products and read models,
alongside search queries, reindexing, and reconciliation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SearchDocument:
    """A denormalised document awaiting indexing."""

    index_alias: str
    document_id: str
    tenant_id: str
    version: int
    body: dict[str, object]


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """Validated, tenant-scoped search query request."""

    tenant_id: str
    q: str | None = None
    status: str | None = None
    sku: str | None = None
    customer: str | None = None
    warehouse: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    page: int = 1
    page_size: int = 20
    sort_by: str = "created_at"
    sort_order: str = "desc"
    index_alias: str = "products"
    cursor: str | None = None
    search_after: tuple[Any, ...] | None = None


@dataclass(frozen=True, slots=True)
class SearchResultItem:
    """Decoupled search item representation."""

    id: str
    tenant_id: str
    slug: str = ""
    name: str = ""
    description: str | None = None
    status: str = "DRAFT"
    attributes: dict[str, Any] = field(default_factory=dict)
    skus: tuple[dict[str, Any], ...] = ()
    created_at: str = ""
    updated_at: str = ""
    version: int = 1
    score: float | None = None
    raw_source: dict[str, Any] = field(default_factory=dict)

    @property
    def order_id(self) -> str:
        if "order_id" in self.raw_source:
            return str(self.raw_source["order_id"])
        if ":" in self.id:
            return self.id.split(":", 1)[1]
        return self.id

    @property
    def customer_reference(self) -> str | None:
        if "customer_reference" in self.raw_source:
            return str(self.raw_source["customer_reference"])
        return None


@dataclass(frozen=True, slots=True)
class SearchPageResult:
    """Tenant-scoped, paginated search results."""

    items: tuple[SearchResultItem, ...]
    total: int
    page: int
    page_size: int
    total_pages: int
    next_cursor: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary for caching."""
        return {
            "items": [
                {
                    "id": item.id,
                    "tenant_id": item.tenant_id,
                    "slug": item.slug,
                    "name": item.name,
                    "description": item.description,
                    "status": item.status,
                    "attributes": item.attributes,
                    "skus": list(item.skus),
                    "created_at": item.created_at,
                    "updated_at": item.updated_at,
                    "version": item.version,
                    "score": item.score,
                    "raw_source": item.raw_source,
                }
                for item in self.items
            ],
            "total": self.total,
            "page": self.page,
            "page_size": self.page_size,
            "total_pages": self.total_pages,
            "next_cursor": self.next_cursor,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SearchPageResult:
        """Deserialize from a cached dictionary."""
        items = tuple(
            SearchResultItem(
                id=str(it.get("id", "")),
                tenant_id=str(it.get("tenant_id", "")),
                slug=str(it.get("slug", "")),
                name=str(it.get("name", "")),
                description=it.get("description"),
                status=str(it.get("status", "DRAFT")),
                attributes=dict(it.get("attributes", {})),
                skus=tuple(dict(s) for s in it.get("skus", ())),
                created_at=str(it.get("created_at", "")),
                updated_at=str(it.get("updated_at", "")),
                version=int(it.get("version", 1)),
                score=it.get("score"),
                raw_source=dict(it.get("raw_source", {})),
            )
            for it in data.get("items", [])
        )
        return cls(
            items=items,
            total=int(data.get("total", 0)),
            page=int(data.get("page", 1)),
            page_size=int(data.get("page_size", 20)),
            total_pages=int(data.get("total_pages", 1)),
            next_cursor=data.get("next_cursor"),
        )


@dataclass(frozen=True, slots=True)
class BulkIndexResult:
    """Detailed outcome of a bulk indexing operation."""

    successful_count: int
    failed_count: int = 0
    retry_count: int = 0
    permanent_failure_count: int = 0
    errors: tuple[dict[str, Any], ...] = ()

    @property
    def is_success(self) -> bool:
        return self.failed_count == 0

    def __int__(self) -> int:
        return self.successful_count


@dataclass(frozen=True, slots=True)
class ReindexResult:
    """Outcome of a reindexing operation."""

    index_name: str
    alias: str
    total_indexed: int
    status: str


@dataclass(frozen=True, slots=True)
class ReconciliationDiscrepancy:
    """Record of a discrepancy found during reconciliation."""

    document_id: str
    tenant_id: str
    discrepancy_type: (
        str  # missing_in_elasticsearch, missing_in_postgresql, version_mismatch, tenant_mismatch
    )
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Outcome of a reconciliation check."""

    total_authoritative: int
    total_search: int
    matched: int
    discrepancies: tuple[ReconciliationDiscrepancy, ...]
    repaired_count: int = 0


@runtime_checkable
class SearchIndexer(Protocol):
    """Outbound port: search queries, projections, and index maintenance."""

    async def index(
        self,
        documents: tuple[SearchDocument, ...],
        *,
        force: bool = False,
        refresh: bool | str = False,
    ) -> None:
        """Index one or more documents into the target search index."""
        ...

    async def bulk_index(
        self,
        documents: tuple[SearchDocument, ...],
        *,
        force: bool = False,
        refresh: bool | str = False,
    ) -> BulkIndexResult:
        """Bulk index documents in a batch, returning structured result."""
        ...

    async def delete(
        self,
        *,
        index_alias: str,
        document_ids: tuple[str, ...],
        refresh: bool | str = False,
    ) -> None:
        """Delete one or more documents by ID from the search index."""
        ...

    async def get_document(self, *, index_alias: str, document_id: str) -> dict[str, object] | None:
        """Retrieve a raw document by its identifier, or None if not found."""
        ...

    async def search(self, query: SearchQuery) -> SearchPageResult:
        """Execute a tenant-safe search query."""
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

    async def refresh_index(self, *, index_name: str) -> None:
        """Explicitly refresh an index so newly indexed documents become searchable."""
        ...

    async def switch_alias(
        self,
        *,
        alias: str,
        new_index: str,
        old_indices: tuple[str, ...] = (),
    ) -> None:
        """Atomically switch an alias to point to new_index."""
        ...

    async def list_indices_for_alias(self, *, alias: str) -> tuple[str, ...]:
        """Return the physical index names currently assigned to an alias."""
        ...

    async def delete_index(self, *, index_name: str) -> bool:
        """Delete an index by name."""
        ...

    async def count_documents(self, *, index_or_alias: str, tenant_id: str | None = None) -> int:
        """Count total documents in an index, optionally scoped to a tenant."""
        ...

    async def get_document_keys(
        self, *, index_or_alias: str, tenant_id: str | None = None
    ) -> dict[str, tuple[str, int]]:
        """Return mapping of document_id -> (tenant_id, version) for reconciliation."""
        ...

    async def get_document_keys_chunk(
        self,
        *,
        index_or_alias: str,
        tenant_id: str | None = None,
        search_after: tuple[Any, ...] | None = None,
        limit: int = 500,
    ) -> tuple[dict[str, tuple[str, int]], tuple[Any, ...] | None]:
        """Return a chunk of doc_id -> (tenant_id, version) with next search_after token."""
        ...

    async def ping(self) -> bool:
        """Verify search engine connectivity."""
        ...

    async def close(self) -> None:
        """Cleanly close connection pools."""
        ...


PLANNED_PHASE = 3

__all__ = [
    "PLANNED_PHASE",
    "BulkIndexResult",
    "ReconciliationDiscrepancy",
    "ReconciliationResult",
    "ReindexResult",
    "SearchDocument",
    "SearchIndexer",
    "SearchPageResult",
    "SearchQuery",
    "SearchResultItem",
]
