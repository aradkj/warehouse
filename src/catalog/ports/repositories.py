"""Catalog repository ports (driven side of the hexagon).

Every method that touches tenant-owned data takes ``tenant_id`` explicitly.
That is the single most important isolation mechanism in Phase 1: a repository
call without a tenant simply does not compile, and an implementation cannot
"forget" to filter because the parameter is required.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from catalog.domain.product import Product
from catalog.domain.sku import Sku
from catalog.domain.value_objects import ProductSlug, ProductStatus
from shared.application.pagination import Page
from shared.domain.identifiers import ProductId, SkuId, TenantId


@runtime_checkable
class ProductRepository(Protocol):
    """Persistence port for the :class:`catalog.domain.product.Product` aggregate."""

    async def add(self, product: Product) -> None:
        """Insert a new product (raises ``DuplicateResource`` on a duplicate slug)."""
        ...

    async def get(
        self, *, tenant_id: TenantId, product_id: ProductId, for_update: bool = False
    ) -> Product | None:
        """Load one product inside the tenant.

        ``for_update`` issues ``SELECT ... FOR UPDATE``; only the caller knows
        whether the read is part of a read-modify-write cycle, so locking is
        requested explicitly rather than applied by default.
        """
        ...

    async def get_by_slug(self, *, tenant_id: TenantId, slug: ProductSlug) -> Product | None:
        """Load one product by its tenant-unique slug."""
        ...

    async def save(self, product: Product, *, expected_version: int) -> None:
        """Update a product, guarding with an optimistic-concurrency check.

        Implementations must raise ``ConcurrencyConflict`` when no row matches
        ``(id, tenant_id, version)``.
        """
        ...

    async def page(
        self,
        *,
        tenant_id: TenantId,
        limit: int,
        cursor: str | None = None,
        status: ProductStatus | None = None,
    ) -> Page[Product]:
        """Keyset-paginated product list, newest first."""
        ...


@runtime_checkable
class SkuRepository(Protocol):
    """Persistence port for the :class:`catalog.domain.sku.Sku` aggregate."""

    async def add(self, sku: Sku) -> None:
        """Insert a new SKU (raises ``DuplicateResource`` on a duplicate code)."""
        ...

    async def get(
        self, *, tenant_id: TenantId, sku_id: SkuId, for_update: bool = False
    ) -> Sku | None:
        """Load one SKU inside the tenant."""
        ...

    async def list_by_ids(
        self, *, tenant_id: TenantId, sku_ids: tuple[SkuId, ...]
    ) -> tuple[Sku, ...]:
        """Load several SKUs at once (batch read for cross-context callers)."""
        ...

    async def list_for_products(
        self, *, tenant_id: TenantId, product_ids: tuple[ProductId, ...]
    ) -> dict[ProductId, tuple[Sku, ...]]:
        """Batch-load the SKUs of several products (avoids N+1 reads)."""
        ...

    async def save(self, sku: Sku, *, expected_version: int) -> None:
        """Update a SKU with an optimistic-concurrency guard."""
        ...


__all__ = ["ProductRepository", "SkuRepository"]
