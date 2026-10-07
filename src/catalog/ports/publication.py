"""Catalog's *published* read contract - what other contexts may depend on.

Ordering needs a price and a purchasability decision when it places an order.
It must not (and does not) read catalog tables: it depends on this module only.

When catalog is extracted into a microservice, this port is what an HTTP client
adapter implements; the ordering side keeps the same types and imports. That is
the whole point of declaring the DTO here rather than in the consumer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from catalog.domain.errors import ProductNotPurchasable
from catalog.domain.value_objects import ProductStatus, SkuStatus
from shared.domain.identifiers import ProductId, SkuId, TenantId
from shared.domain.value_objects import Money


@dataclass(frozen=True, slots=True)
class PurchasableSku:
    """Cross-context read model: everything ordering needs to price a line."""

    sku_id: SkuId
    product_id: ProductId
    sku_code: str
    sku_name: str
    product_name: str
    product_slug: str
    product_status: ProductStatus
    sku_status: SkuStatus
    unit_price: Money

    @property
    def is_purchasable(self) -> bool:
        return self.product_status is ProductStatus.ACTIVE and self.sku_status is SkuStatus.ACTIVE

    @property
    def not_purchasable_reason(self) -> str | None:
        if self.product_status is not ProductStatus.ACTIVE:
            return f"product_{self.product_status.value.lower()}"
        if self.sku_status is not SkuStatus.ACTIVE:
            return "sku_inactive"
        return None


@runtime_checkable
class CatalogQuery(Protocol):
    """Read-only projection of the catalog for other bounded contexts."""

    async def purchasable_skus(
        self, *, tenant_id: TenantId, sku_ids: tuple[SkuId, ...]
    ) -> dict[SkuId, PurchasableSku]:
        """Return the sku lookup for ``sku_ids`` (missing ids are simply absent).

        Implementations must scope by ``tenant_id``: a SKU belonging to another
        tenant is reported as missing, never as forbidden.
        """
        ...


__all__ = ["CatalogQuery", "PurchasableSku"]


__all__ = ["CatalogQuery", "ProductNotPurchasable", "PurchasableSku"]
