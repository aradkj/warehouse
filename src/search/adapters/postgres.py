"""PostgreSQL implementation of CatalogSearchReader port.

Streams authoritative product and SKU records from PostgreSQL to build
SearchDocuments for reindexing and reconciliation.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import func, select, text

from catalog.adapters.persistence.tables import products, skus
from search.application.models import build_product_document_id
from search.ports.indexer import SearchDocument
from search.ports.reader import CatalogSearchReader
from shared.infrastructure.db.session import Database


class SqlAlchemyCatalogSearchReader(CatalogSearchReader):
    """Authoritative reader sourcing catalog data directly from PostgreSQL."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def _row_to_search_document(
        self,
        prod_row: Any,
        sku_rows: list[Any],
        target_alias: str,
    ) -> SearchDocument:
        """Convert PostgreSQL rows to SearchDocument."""
        tenant_str = str(prod_row.tenant_id)
        prod_id_str = str(prod_row.id)
        doc_id = build_product_document_id(tenant_str, prod_id_str)

        sku_list: list[dict[str, Any]] = [
            {
                "sku_id": str(s.id),
                "sku_code": str(s.code),
                "unit_price_minor": int(s.unit_price_minor),
                "currency": str(s.currency),
                "status": str(s.status),
            }
            for s in sku_rows
        ]

        created_at_str = (
            prod_row.created_at.isoformat()
            if hasattr(prod_row.created_at, "isoformat")
            else str(prod_row.created_at)
        )
        updated_at_str = (
            prod_row.updated_at.isoformat()
            if hasattr(prod_row.updated_at, "isoformat")
            else str(prod_row.updated_at)
        )

        body: dict[str, object] = {
            "id": prod_id_str,
            "tenant_id": tenant_str,
            "slug": str(prod_row.slug),
            "name": str(prod_row.name),
            "description": prod_row.description,
            "status": str(prod_row.status),
            "attributes": dict(prod_row.attributes or {}),
            "skus": sku_list,
            "created_at": created_at_str,
            "updated_at": updated_at_str,
            "version": int(prod_row.version),
        }

        return SearchDocument(
            index_alias=target_alias,
            document_id=doc_id,
            tenant_id=tenant_str,
            version=int(prod_row.version),
            body=body,
        )

    async def get_search_document(
        self, *, tenant_id: str, product_id: str, target_alias: str = "products"
    ) -> SearchDocument | None:
        """Fetch a single complete product as a SearchDocument."""
        try:
            t_uuid = uuid.UUID(tenant_id)
            p_uuid = uuid.UUID(product_id)
        except (ValueError, TypeError):
            return None

        scope = self._database.new_scope(tenant_id=str(t_uuid))
        async with scope:
            session = scope.session
            stmt = select(products).where(
                products.c.tenant_id == t_uuid,
                products.c.id == p_uuid,
            )
            result = await session.execute(stmt)
            p = result.fetchone()
            if p is None:
                return None

            skus_stmt = select(skus).where(
                skus.c.tenant_id == t_uuid,
                skus.c.product_id == p_uuid,
            )
            skus_res = await session.execute(skus_stmt)
            sku_rows = list(skus_res.fetchall())

            return self._row_to_search_document(p, sku_rows, target_alias)

    async def _get_all_tenant_ids(self) -> list[str]:
        async with self._database.new_scope() as scope:
            stmt = text("SELECT id FROM tenants WHERE status = 'ACTIVE'")
            res = await scope.session.execute(stmt)
            return [str(r[0]) for r in res.fetchall()]

    async def stream_search_documents(
        self,
        *,
        tenant_id: str | None = None,
        target_alias: str = "products",
        batch_size: int = 200,
    ) -> AsyncIterator[tuple[SearchDocument, ...]]:
        """Asynchronously stream batches of SearchDocuments from PostgreSQL."""
        if tenant_id is None:
            tenant_ids = await self._get_all_tenant_ids()
            for tid in tenant_ids:
                async for batch in self.stream_search_documents(
                    tenant_id=tid,
                    target_alias=target_alias,
                    batch_size=batch_size,
                ):
                    yield batch
            return

        try:
            t_uuid = uuid.UUID(tenant_id)
        except (ValueError, TypeError):
            return

        last_id: uuid.UUID | None = None
        while True:
            scope = self._database.new_scope(tenant_id=str(t_uuid))
            async with scope:
                session = scope.session
                stmt = select(products).where(products.c.tenant_id == t_uuid)
                if last_id is not None:
                    stmt = stmt.where(products.c.id > last_id)
                stmt = stmt.order_by(products.c.id.asc()).limit(batch_size)

                result = await session.execute(stmt)
                prod_rows = list(result.fetchall())
                if not prod_rows:
                    break

                last_id = prod_rows[-1].id
                prod_ids = [p.id for p in prod_rows]
                skus_stmt = (
                    select(skus)
                    .where(skus.c.product_id.in_(prod_ids))
                    .where(skus.c.tenant_id == t_uuid)
                )

                skus_res = await session.execute(skus_stmt)
                skus_rows = list(skus_res.fetchall())

                skus_by_product: dict[uuid.UUID, list[Any]] = {}
                for s in skus_rows:
                    skus_by_product.setdefault(s.product_id, []).append(s)

                batch_docs: list[SearchDocument] = [
                    self._row_to_search_document(p, skus_by_product.get(p.id, []), target_alias)
                    for p in prod_rows
                ]

                yield tuple(batch_docs)
                if len(prod_rows) < batch_size:
                    break

    async def count_authoritative_products(self, *, tenant_id: str | None = None) -> int:
        """Return total count of products in authoritative database."""
        if tenant_id is None:
            tenant_ids = await self._get_all_tenant_ids()
            total = 0
            for tid in tenant_ids:
                total += await self.count_authoritative_products(tenant_id=tid)
            return total

        try:
            t_uuid = uuid.UUID(tenant_id)
        except (ValueError, TypeError):
            return 0

        scope = self._database.new_scope(tenant_id=str(t_uuid))
        async with scope:
            session = scope.session
            stmt = select(func.count(products.c.id)).where(products.c.tenant_id == t_uuid)
            result = await session.execute(stmt)
            val = result.scalar_one()
            return int(val or 0)

    async def get_authoritative_keys(
        self, *, tenant_id: str | None = None
    ) -> dict[str, tuple[str, int]]:
        """Fetch mapping of doc_id -> (tenant_id, version) from database."""
        if tenant_id is None:
            tenant_ids = await self._get_all_tenant_ids()
            combined: dict[str, tuple[str, int]] = {}
            for tid in tenant_ids:
                combined.update(await self.get_authoritative_keys(tenant_id=tid))
            return combined

        try:
            t_uuid = uuid.UUID(tenant_id)
        except (ValueError, TypeError):
            return {}

        scope = self._database.new_scope(tenant_id=str(t_uuid))
        async with scope:
            session = scope.session
            stmt = select(products.c.id, products.c.tenant_id, products.c.version).where(
                products.c.tenant_id == t_uuid
            )
            result = await session.execute(stmt)
            rows = result.fetchall()
            return {
                f"{row.tenant_id}:{row.id}": (str(row.tenant_id), int(row.version)) for row in rows
            }

    async def stream_authoritative_keys(
        self,
        *,
        tenant_id: str | None = None,
        batch_size: int = 500,
    ) -> AsyncIterator[dict[str, tuple[str, int]]]:
        """Stream chunks of doc_id -> (tenant_id, version) using keyset pagination."""
        if tenant_id is None:
            tenant_ids = await self._get_all_tenant_ids()
            for tid in tenant_ids:
                async for chunk in self.stream_authoritative_keys(
                    tenant_id=tid,
                    batch_size=batch_size,
                ):
                    yield chunk
            return

        try:
            t_uuid = uuid.UUID(tenant_id)
        except (ValueError, TypeError):
            return

        last_id: uuid.UUID | None = None
        while True:
            scope = self._database.new_scope(tenant_id=str(t_uuid))
            async with scope:
                session = scope.session
                stmt = (
                    select(products.c.id, products.c.tenant_id, products.c.version)
                    .where(products.c.tenant_id == t_uuid)
                )
                if last_id is not None:
                    stmt = stmt.where(products.c.id > last_id)
                stmt = stmt.order_by(products.c.id.asc()).limit(batch_size)

                result = await session.execute(stmt)
                rows = list(result.fetchall())
                if not rows:
                    break

                last_id = rows[-1].id
                chunk = {
                    f"{row.tenant_id}:{row.id}": (str(row.tenant_id), int(row.version))
                    for row in rows
                }
                yield chunk
                if len(rows) < batch_size:
                    break


__all__ = ["SqlAlchemyCatalogSearchReader"]
