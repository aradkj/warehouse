"""Search request and response schemas."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from search.ports.indexer import (
    ReconciliationResult,
    ReindexResult,
    SearchPageResult,
    SearchResultItem,
)


class ProductSearchSkuResponse(BaseModel):
    """Denormalized SKU representation within a search result."""

    model_config = ConfigDict(frozen=True)

    sku_id: str
    sku_code: str
    unit_price_minor: int
    currency: str
    status: str


class ProductSearchResultResponse(BaseModel):
    """Search item response model (decoupled from Elasticsearch internal DSL)."""

    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    slug: str
    name: str
    description: str | None = None
    status: str
    attributes: dict[str, Any] = Field(default_factory=dict)
    skus: list[ProductSearchSkuResponse] = Field(default_factory=list)
    created_at: str
    updated_at: str
    version: int
    score: float | None = None

    @classmethod
    def from_item(cls, item: SearchResultItem) -> ProductSearchResultResponse:
        sku_models = [
            ProductSearchSkuResponse(
                sku_id=str(s.get("sku_id", "")),
                sku_code=str(s.get("sku_code", "")),
                unit_price_minor=int(s.get("unit_price_minor", 0)),
                currency=str(s.get("currency", "")),
                status=str(s.get("status", "")),
            )
            for s in item.skus
        ]
        return cls(
            id=item.id,
            tenant_id=item.tenant_id,
            slug=item.slug,
            name=item.name,
            description=item.description,
            status=item.status,
            attributes=dict(item.attributes),
            skus=sku_models,
            created_at=item.created_at,
            updated_at=item.updated_at,
            version=item.version,
            score=item.score,
        )


class ProductSearchPageResponse(BaseModel):
    """Paginated search response with explicit metadata."""

    model_config = ConfigDict(frozen=True)

    items: list[ProductSearchResultResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    next_cursor: str | None = None

    @classmethod
    def from_page_result(cls, result: SearchPageResult) -> ProductSearchPageResponse:
        return cls(
            items=[ProductSearchResultResponse.from_item(it) for it in result.items],
            total=result.total,
            page=result.page,
            page_size=result.page_size,
            total_pages=result.total_pages,
            next_cursor=result.next_cursor,
        )


class ReindexRequest(BaseModel):
    """Administrative request to trigger search index rebuild."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID | None = None
    retain_old_indices: bool = False
    async_mode: bool = False


class ReindexResponse(BaseModel):
    """Outcome of an administrative reindex operation."""

    model_config = ConfigDict(frozen=True)

    index_name: str | None = None
    alias: str | None = None
    total_indexed: int = 0
    status: str
    task_id: str | None = None

    @classmethod
    def from_result(cls, result: ReindexResult) -> ReindexResponse:
        return cls(
            index_name=result.index_name,
            alias=result.alias,
            total_indexed=result.total_indexed,
            status=result.status,
            task_id=None,
        )


class ReconcileRequest(BaseModel):
    """Administrative request to trigger consistency check between PG and ES."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID | None = None
    auto_repair: bool = False
    async_mode: bool = False


class ReconciliationDiscrepancyResponse(BaseModel):
    """Details of a discovered inconsistency."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    tenant_id: str
    discrepancy_type: str
    details: dict[str, Any] = Field(default_factory=dict)


class ReconcileResponse(BaseModel):
    """Outcome of a reconciliation check."""

    model_config = ConfigDict(frozen=True)

    total_authoritative: int = 0
    total_search: int = 0
    matched: int = 0
    discrepancies: list[ReconciliationDiscrepancyResponse] = Field(default_factory=list)
    repaired_count: int = 0
    status: str = "COMPLETED"
    task_id: str | None = None

    @classmethod
    def from_result(cls, result: ReconciliationResult) -> ReconcileResponse:
        return cls(
            total_authoritative=result.total_authoritative,
            total_search=result.total_search,
            matched=result.matched,
            discrepancies=[
                ReconciliationDiscrepancyResponse(
                    document_id=d.document_id,
                    tenant_id=d.tenant_id,
                    discrepancy_type=d.discrepancy_type,
                    details=d.details,
                )
                for d in result.discrepancies
            ],
            repaired_count=result.repaired_count,
            status="COMPLETED",
            task_id=None,
        )


class OrderSearchSkuResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    sku_id: str
    sku_code: str
    quantity: int = 1
    unit_price_minor: int = 0


class OrderSearchResultResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    order_id: str
    customer_id: str | None = None
    customer_reference: str | None = None
    status: str
    currency: str = "GBP"
    total_minor: int = 0
    warehouse_ids: list[str] = Field(default_factory=list)
    skus: list[OrderSearchSkuResponse] = Field(default_factory=list)
    created_at: str
    updated_at: str
    version: int = 1
    score: float | None = None

    @classmethod
    def from_item(cls, item: SearchResultItem) -> OrderSearchResultResponse:
        src = item.raw_source
        skus_data = src.get("skus") or item.skus
        sku_models = [
            OrderSearchSkuResponse(
                sku_id=str(s.get("sku_id", "")),
                sku_code=str(s.get("sku_code", "")),
                quantity=int(s.get("quantity", 1)),
                unit_price_minor=int(s.get("unit_price_minor", 0)),
            )
            for s in skus_data
        ]
        return cls(
            id=item.id,
            tenant_id=item.tenant_id,
            order_id=str(src.get("order_id") or item.id),
            customer_id=src.get("customer_id"),
            customer_reference=src.get("customer_reference"),
            status=str(src.get("status") or item.status),
            currency=str(src.get("currency", "GBP")),
            total_minor=int(src.get("total_minor", 0)),
            warehouse_ids=list(src.get("warehouse_ids", [])),
            skus=sku_models,
            created_at=item.created_at,
            updated_at=item.updated_at,
            version=item.version,
            score=item.score,
        )


class OrderSearchPageResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    items: list[OrderSearchResultResponse]
    total: int
    page: int
    page_size: int
    total_pages: int
    next_cursor: str | None = None

    @classmethod
    def from_page_result(cls, result: SearchPageResult) -> OrderSearchPageResponse:
        return cls(
            items=[OrderSearchResultResponse.from_item(it) for it in result.items],
            total=result.total,
            page=result.page,
            page_size=result.page_size,
            total_pages=result.total_pages,
            next_cursor=result.next_cursor,
        )


__all__ = [
    "OrderSearchPageResponse",
    "OrderSearchResultResponse",
    "OrderSearchSkuResponse",
    "ProductSearchPageResponse",
    "ProductSearchResultResponse",
    "ProductSearchSkuResponse",
    "ReconcileRequest",
    "ReconcileResponse",
    "ReconciliationDiscrepancyResponse",
    "ReindexRequest",
    "ReindexResponse",
]
