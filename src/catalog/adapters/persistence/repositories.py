"""SQLAlchemy implementations of the catalog repository ports."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Select, insert, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from catalog.adapters.persistence.mappers import (
    product_insert_values,
    product_to_domain,
    product_update_values,
    sku_insert_values,
    sku_to_domain,
    sku_update_values,
)
from catalog.adapters.persistence.tables import products, skus
from catalog.domain.product import Product
from catalog.domain.sku import Sku
from catalog.domain.value_objects import ProductSlug, ProductStatus
from shared.application.pagination import (
    Page,
    build_page,
    cursor_datetime,
    cursor_uuid,
    decode_cursor,
)
from shared.domain.identifiers import ProductId, SkuId, TenantId
from shared.infrastructure.db.execution import execute, execute_guarded_update

_PRODUCT_DUPLICATES: Mapping[str, tuple[str, str]] = {
    "uq_products_tenant_id_slug": ("product", "slug"),
}
_SKU_DUPLICATES: Mapping[str, tuple[str, str]] = {
    "uq_skus_tenant_id_code": ("sku", "code"),
}


class SqlAlchemyProductRepository:
    """Product persistence adapter."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, product: Product) -> None:
        await execute(
            self._session,
            insert(products).values(**product_insert_values(product)),
            duplicates=_PRODUCT_DUPLICATES,
        )

    async def get(
        self, *, tenant_id: TenantId, product_id: ProductId, for_update: bool = False
    ) -> Product | None:
        statement = select(products).where(
            products.c.tenant_id == tenant_id, products.c.id == product_id
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).mappings().first()
        return product_to_domain(row) if row is not None else None

    async def get_by_slug(self, *, tenant_id: TenantId, slug: ProductSlug) -> Product | None:
        statement = select(products).where(
            products.c.tenant_id == tenant_id, products.c.slug == slug.value
        )
        row = (await self._session.execute(statement)).mappings().first()
        return product_to_domain(row) if row is not None else None

    async def save(self, product: Product, *, expected_version: int) -> None:
        statement = (
            update(products)
            .where(
                products.c.id == product.id,
                products.c.tenant_id == product.tenant_id,
                products.c.version == expected_version,
            )
            .values(**product_update_values(product), version=expected_version + 1)
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="product",
            resource_id=product.id,
            expected_version=expected_version,
        )

    async def page(
        self,
        *,
        tenant_id: TenantId,
        limit: int,
        cursor: str | None = None,
        status: ProductStatus | None = None,
    ) -> Page[Product]:
        statement: Select[Any] = select(products).where(products.c.tenant_id == tenant_id)
        if status is not None:
            statement = statement.where(products.c.status == status.value)
        statement = _apply_keyset(statement, cursor)
        statement = statement.order_by(products.c.created_at.desc(), products.c.id.desc())
        statement = statement.limit(limit + 1)
        rows = (await self._session.execute(statement)).mappings().all()
        return build_page(
            [product_to_domain(row) for row in rows],
            limit=limit,
            cursor_of=lambda product: {"created_at": product.created_at, "id": product.id},
        )


class SqlAlchemySkuRepository:
    """SKU persistence adapter."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, sku: Sku) -> None:
        await execute(
            self._session,
            insert(skus).values(**sku_insert_values(sku)),
            duplicates=_SKU_DUPLICATES,
        )

    async def get(
        self, *, tenant_id: TenantId, sku_id: SkuId, for_update: bool = False
    ) -> Sku | None:
        statement = select(skus).where(skus.c.tenant_id == tenant_id, skus.c.id == sku_id)
        if for_update:
            statement = statement.with_for_update()
        row = (await self._session.execute(statement)).mappings().first()
        return sku_to_domain(row) if row is not None else None

    async def list_by_ids(
        self, *, tenant_id: TenantId, sku_ids: tuple[SkuId, ...]
    ) -> tuple[Sku, ...]:
        if not sku_ids:
            return ()
        statement = select(skus).where(skus.c.tenant_id == tenant_id, skus.c.id.in_(tuple(sku_ids)))
        rows = (await self._session.execute(statement)).mappings().all()
        return tuple(sku_to_domain(row) for row in rows)

    async def list_for_products(
        self, *, tenant_id: TenantId, product_ids: tuple[ProductId, ...]
    ) -> dict[ProductId, tuple[Sku, ...]]:
        if not product_ids:
            return {}
        statement = (
            select(skus)
            .where(skus.c.tenant_id == tenant_id, skus.c.product_id.in_(tuple(product_ids)))
            .order_by(skus.c.code.asc())
        )
        rows = (await self._session.execute(statement)).mappings().all()
        grouped: dict[ProductId, list[Sku]] = {}
        for row in rows:
            sku = sku_to_domain(row)
            grouped.setdefault(sku.product_id, []).append(sku)
        return {product_id: tuple(items) for product_id, items in grouped.items()}

    async def save(self, sku: Sku, *, expected_version: int) -> None:
        statement = (
            update(skus)
            .where(
                skus.c.id == sku.id,
                skus.c.tenant_id == sku.tenant_id,
                skus.c.version == expected_version,
            )
            .values(**sku_update_values(sku), version=expected_version + 1)
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="sku",
            resource_id=sku.id,
            expected_version=expected_version,
        )


def _apply_keyset(statement: Select[Any], cursor: str | None) -> Select[Any]:
    """Add the keyset predicate ``(created_at, id) < (cursor_created_at, cursor_id)``.

    Row-value comparison maps directly onto the composite index; ``OFFSET`` would
    both skip and repeat rows under concurrent inserts.
    """
    if cursor is None:
        return statement
    values = decode_cursor(cursor)
    created_at = cursor_datetime(values, "created_at")
    row_id = cursor_uuid(values, "id")
    return statement.where(
        tuple_(products.c.created_at, products.c.id) < tuple_(created_at, row_id)
    )


__all__ = ["SqlAlchemyProductRepository", "SqlAlchemySkuRepository"]
