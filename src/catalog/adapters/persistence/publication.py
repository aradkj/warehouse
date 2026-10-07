"""Read-only projection of the catalog for other bounded contexts.

This adapter implements :class:`catalog.ports.publication.CatalogQuery`. It is a
single query against the catalog tables (a join, not an N+1) and it is always
tenant-scoped, so ordering cannot read another tenant's prices even by mistake.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.adapters.persistence.tables import products, skus
from catalog.domain.value_objects import ProductStatus, SkuStatus
from catalog.ports.publication import CatalogQuery, PurchasableSku
from shared.domain.identifiers import ProductId, SkuId, TenantId
from shared.domain.value_objects import Money


class SqlAlchemyCatalogQuery(CatalogQuery):
    """``CatalogQuery`` port implementation backed by PostgreSQL."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def purchasable_skus(
        self, *, tenant_id: TenantId, sku_ids: tuple[SkuId, ...]
    ) -> dict[SkuId, PurchasableSku]:
        if not sku_ids:
            return {}
        statement = (
            select(
                skus.c.id,
                skus.c.product_id,
                skus.c.code,
                skus.c.name.label("sku_name"),
                skus.c.status.label("sku_status"),
                skus.c.unit_price_minor,
                skus.c.currency,
                products.c.name.label("product_name"),
                products.c.slug.label("product_slug"),
                products.c.status.label("product_status"),
            )
            .join(products, products.c.id == skus.c.product_id)
            .where(skus.c.tenant_id == tenant_id, skus.c.id.in_(tuple(sku_ids)))
        )
        rows = (await self._session.execute(statement)).mappings().all()
        return {
            SkuId(row["id"]): PurchasableSku(
                sku_id=SkuId(row["id"]),
                product_id=ProductId(row["product_id"]),
                sku_code=row["code"],
                sku_name=row["sku_name"],
                product_name=row["product_name"],
                product_slug=row["product_slug"],
                product_status=ProductStatus(row["product_status"]),
                sku_status=SkuStatus(row["sku_status"]),
                unit_price=Money(row["unit_price_minor"], row["currency"]),
            )
            for row in rows
        }


__all__ = ["SqlAlchemyCatalogQuery"]
