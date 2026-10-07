"""Catalog read use cases.

Reads are use cases too: they validate input, enforce tenant scoping through the
repositories and return application views. Keeping them out of the HTTP layer is
what allows the same reads to serve a worker, a CLI or the future search
projector.
"""

from __future__ import annotations

from catalog.application.commands import (
    GetProductQuery,
    GetSkuQuery,
    ListProductsQuery,
    ProductView,
    SkuView,
)
from catalog.ports.unit_of_work import CatalogUnitOfWork
from shared.application.errors import NotFound
from shared.application.pagination import Page, normalise_limit
from shared.application.use_case import QueryUseCase


class GetProduct(QueryUseCase[GetProductQuery, ProductView, CatalogUnitOfWork]):
    """Fetch one product together with its SKUs."""

    async def _execute(self, uow: CatalogUnitOfWork, command: GetProductQuery) -> ProductView:
        tenant_id = command.tenant_id
        product = await uow.products.get(tenant_id=tenant_id, product_id=command.product_id)
        if product is None:
            raise NotFound("product", command.product_id)
        skus = await uow.skus.list_for_products(tenant_id=tenant_id, product_ids=(product.id,))
        return ProductView.of(product, skus.get(product.id, ()))


class GetSku(QueryUseCase[GetSkuQuery, SkuView, CatalogUnitOfWork]):
    """Fetch one SKU."""

    async def _execute(self, uow: CatalogUnitOfWork, command: GetSkuQuery) -> SkuView:
        sku = await uow.skus.get(tenant_id=command.tenant_id, sku_id=command.sku_id)
        if sku is None:
            raise NotFound("sku", command.sku_id)
        return SkuView.of(sku)


class ListProducts(QueryUseCase[ListProductsQuery, Page[ProductView], CatalogUnitOfWork]):
    """Keyset-paginated product list, optionally filtered by lifecycle state."""

    async def _execute(
        self, uow: CatalogUnitOfWork, command: ListProductsQuery
    ) -> Page[ProductView]:
        tenant_id = command.tenant_id
        limit = normalise_limit(command.limit)
        page = await uow.products.page(
            tenant_id=tenant_id, limit=limit, cursor=command.cursor, status=command.status
        )
        product_ids = tuple(product.id for product in page.items)
        skus_by_product = (
            await uow.skus.list_for_products(tenant_id=tenant_id, product_ids=product_ids)
            if product_ids
            else {}
        )
        views = tuple(
            ProductView.of(product, skus_by_product.get(product.id, ())) for product in page.items
        )
        return Page(items=views, next_cursor=page.next_cursor)


__all__ = ["GetProduct", "GetSku", "ListProducts"]
