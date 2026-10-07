"""Catalog commands and their canonical views.

Commands are plain, immutable data: they carry *what* the caller wants, never
how to do it. They also carry the :class:`RequestContext`, so a use case can be
invoked identically from HTTP, a worker or a test.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from catalog.domain.product import Product
from catalog.domain.sku import Sku
from catalog.domain.value_objects import AttributeValue, ProductSlug, ProductStatus, SkuStatus
from shared.application.context import RequestContext
from shared.domain.identifiers import ProductId, SkuId, TenantId


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CreateProductCommand:
    tenant_id: TenantId
    context: RequestContext
    slug: str
    name: str
    description: str | None = None
    attributes: Mapping[str, AttributeValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReviseProductCommand:
    tenant_id: TenantId
    context: RequestContext
    product_id: ProductId
    name: str | None = None
    description: str | None = None
    attributes: Mapping[str, AttributeValue] | None = None
    #: Optional expectation from an ``If-Match`` header (optimistic concurrency).
    expected_version: int | None = None


@dataclass(frozen=True, slots=True)
class ChangeProductStateCommand:
    tenant_id: TenantId
    context: RequestContext
    product_id: ProductId


@dataclass(frozen=True, slots=True)
class CreateSkuCommand:
    tenant_id: TenantId
    context: RequestContext
    product_id: ProductId
    code: str
    name: str
    unit_price_minor: int
    currency: str
    attributes: Mapping[str, AttributeValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ChangeSkuPriceCommand:
    tenant_id: TenantId
    context: RequestContext
    sku_id: SkuId
    unit_price_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class ChangeSkuStateCommand:
    tenant_id: TenantId
    context: RequestContext
    sku_id: SkuId


@dataclass(frozen=True, slots=True)
class GetProductQuery:
    tenant_id: TenantId
    context: RequestContext
    product_id: ProductId


@dataclass(frozen=True, slots=True)
class ListProductsQuery:
    tenant_id: TenantId
    context: RequestContext
    limit: int
    cursor: str | None = None
    status: ProductStatus | None = None


@dataclass(frozen=True, slots=True)
class GetSkuQuery:
    tenant_id: TenantId
    context: RequestContext
    sku_id: SkuId


# ---------------------------------------------------------------------------
# Views (application-layer representations; no HTTP or SQLAlchemy types)
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SkuView:
    id: SkuId
    product_id: ProductId
    code: str
    name: str
    status: SkuStatus
    unit_price_minor: int
    currency: str
    attributes: Mapping[str, AttributeValue]
    created_at: datetime
    updated_at: datetime
    version: int

    @classmethod
    def of(cls, sku: Sku) -> SkuView:
        return cls(
            id=sku.id,
            product_id=sku.product_id,
            code=sku.code.value,
            name=sku.name,
            status=sku.status,
            unit_price_minor=sku.unit_price.amount_minor,
            currency=sku.unit_price.currency,
            attributes=sku.attributes.as_dict(),
            created_at=sku.created_at,
            updated_at=sku.updated_at,
            version=sku.version,
        )


@dataclass(frozen=True, slots=True)
class ProductView:
    id: ProductId
    slug: str
    name: str
    description: str | None
    status: ProductStatus
    attributes: Mapping[str, AttributeValue]
    created_at: datetime
    updated_at: datetime
    version: int
    skus: tuple[SkuView, ...] = ()

    @classmethod
    def of(cls, product: Product, skus: tuple[Sku, ...] = ()) -> ProductView:
        return cls(
            id=product.id,
            slug=product.slug.value,
            name=product.name,
            description=product.description,
            status=product.status,
            attributes=product.attributes.as_dict(),
            created_at=product.created_at,
            updated_at=product.updated_at,
            version=product.version,
            skus=tuple(SkuView.of(sku) for sku in skus),
        )


def slug_of(value: str) -> ProductSlug:
    """Adapter helper: turn untyped input into the slug value object."""
    return ProductSlug(value.strip().lower())


__all__ = [
    "ChangeProductStateCommand",
    "ChangeSkuPriceCommand",
    "ChangeSkuStateCommand",
    "CreateProductCommand",
    "CreateSkuCommand",
    "GetProductQuery",
    "GetSkuQuery",
    "ListProductsQuery",
    "ProductView",
    "ReviseProductCommand",
    "SkuView",
    "slug_of",
]
