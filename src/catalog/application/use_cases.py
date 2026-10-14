"""Catalog use cases.

Each use case is a thin orchestration over the domain:

    load (with a lock when it is a read-modify-write) -> call domain behaviour ->
    persist -> collect events -> commit

All invariants live in :mod:`catalog.domain`; all persistence lives behind the
ports. Nothing here imports SQLAlchemy, FastAPI or any other framework.
"""

from __future__ import annotations

from datetime import datetime

from catalog.application.commands import (
    ChangeProductStateCommand,
    ChangeSkuPriceCommand,
    ChangeSkuStateCommand,
    CreateProductCommand,
    CreateSkuCommand,
    ProductView,
    ReviseProductCommand,
    SkuView,
    slug_of,
)
from catalog.domain.errors import InvalidProductTransition
from catalog.domain.product import Product
from catalog.domain.sku import Sku
from catalog.domain.value_objects import SkuCode, normalise_attribute_input
from catalog.ports.unit_of_work import CatalogUnitOfWork
from shared.application.errors import ConcurrencyConflict, NotFound
from shared.application.idempotency import IdempotencyRecord, completed_resource_id
from shared.application.use_case import Executed, UseCase, pull_events
from shared.domain.identifiers import ProductId, SkuId, TenantId
from shared.domain.value_objects import Money


class CreateProduct(UseCase[CreateProductCommand, ProductView, CatalogUnitOfWork]):
    """Create a product in ``DRAFT`` and return its representation."""

    operation = "catalog.create_product"
    permission = "catalog.products.manage"
    resource_type = "products"
    success_status = 201

    async def _execute(
        self, uow: CatalogUnitOfWork, command: CreateProductCommand
    ) -> Executed[ProductView]:
        await self._authorize(
            command.context,
            action="catalog.products.manage",
            resource_type="product",
            resource_tenant_id=command.tenant_id,
            resource_attributes={"slug": str(command.slug), "name": command.name},
        )
        product = Product.create(
            tenant_id=command.tenant_id,
            slug=slug_of(command.slug),
            name=command.name,
            description=command.description,
            attributes=normalise_attribute_input(command.attributes),
            now=self._clock.now(),
        )
        await uow.products.add(product)
        return Executed(
            view=ProductView.of(product),
            resource_type="product",
            resource_id=product.id,
            events=pull_events(product),
        )

    async def _reload(
        self, uow: CatalogUnitOfWork, record: IdempotencyRecord, command: CreateProductCommand
    ) -> ProductView:
        product_id = completed_resource_id(record)
        product = await uow.products.get(
            tenant_id=command.tenant_id, product_id=ProductId(product_id)
        )
        if product is None:  # pragma: no cover - products are never deleted in Phase 1
            raise NotFound("product", product_id)
        skus = await uow.skus.list_for_products(
            tenant_id=command.tenant_id, product_ids=(product.id,)
        )
        return ProductView.of(product, skus.get(product.id, ()))


class ReviseProduct(UseCase[ReviseProductCommand, ProductView, CatalogUnitOfWork]):
    """Change descriptive metadata of a non-archived product."""

    operation = "catalog.revise_product"
    permission = "catalog.products.manage"
    resource_type = "products"
    success_status = 200

    async def _execute(
        self, uow: CatalogUnitOfWork, command: ReviseProductCommand
    ) -> Executed[ProductView]:
        await self._authorize(
            command.context,
            action="catalog.products.manage",
            resource_type="product",
            resource_id=str(command.product_id),
            resource_tenant_id=command.tenant_id,
        )
        product = await _require_product(
            uow, command.tenant_id, command.product_id, for_update=True
        )
        loaded_version = product.version
        if command.expected_version is not None and command.expected_version != loaded_version:
            raise ConcurrencyConflict(
                "the product was modified by another request",
                details={
                    "expected_version": command.expected_version,
                    "actual_version": loaded_version,
                },
            )
        product.revise(
            name=command.name,
            description=command.description,
            attributes=(
                None
                if command.attributes is None
                else normalise_attribute_input(command.attributes)
            ),
            now=self._clock.now(),
        )
        await uow.products.save(product, expected_version=loaded_version)
        return Executed(
            view=ProductView.of(product),
            resource_type="product",
            resource_id=product.id,
            events=pull_events(product),
        )

    async def _reload(
        self, uow: CatalogUnitOfWork, record: IdempotencyRecord, command: ReviseProductCommand
    ) -> ProductView:
        product = await _require_product(
            uow, command.tenant_id, ProductId(completed_resource_id(record))
        )
        return ProductView.of(product)


class ActivateProduct(UseCase[ChangeProductStateCommand, ProductView, CatalogUnitOfWork]):
    """Move a product from ``DRAFT`` to ``ACTIVE``."""

    operation = "catalog.activate_product"
    permission = "catalog.products.manage"
    resource_type = "products"
    success_status = 200

    async def _execute(
        self, uow: CatalogUnitOfWork, command: ChangeProductStateCommand
    ) -> Executed[ProductView]:
        await self._authorize(
            command.context,
            action="catalog.products.manage",
            resource_type="product",
            resource_id=str(command.product_id),
            resource_tenant_id=command.tenant_id,
        )
        product = await _transition_product(
            uow,
            command.tenant_id,
            command.product_id,
            activate=True,
            now=self._clock.now(),
        )
        return Executed(
            view=ProductView.of(product),
            resource_type="product",
            resource_id=product.id,
            events=pull_events(product),
        )

    async def _reload(
        self, uow: CatalogUnitOfWork, record: IdempotencyRecord, command: ChangeProductStateCommand
    ) -> ProductView:
        product = await _require_product(
            uow, command.tenant_id, ProductId(completed_resource_id(record))
        )
        return ProductView.of(product)


class ArchiveProduct(UseCase[ChangeProductStateCommand, ProductView, CatalogUnitOfWork]):
    """Retire a product permanently (terminal transition)."""

    operation = "catalog.archive_product"
    permission = "catalog.products.manage"
    resource_type = "products"
    success_status = 200

    async def _execute(
        self, uow: CatalogUnitOfWork, command: ChangeProductStateCommand
    ) -> Executed[ProductView]:
        await self._authorize(
            command.context,
            action="catalog.products.manage",
            resource_type="product",
            resource_id=str(command.product_id),
            resource_tenant_id=command.tenant_id,
        )
        product = await _transition_product(
            uow,
            command.tenant_id,
            command.product_id,
            activate=False,
            now=self._clock.now(),
        )
        return Executed(
            view=ProductView.of(product),
            resource_type="product",
            resource_id=product.id,
            events=pull_events(product),
        )

    async def _reload(
        self, uow: CatalogUnitOfWork, record: IdempotencyRecord, command: ChangeProductStateCommand
    ) -> ProductView:
        product = await _require_product(
            uow, command.tenant_id, ProductId(completed_resource_id(record))
        )
        return ProductView.of(product)


class CreateSku(UseCase[CreateSkuCommand, SkuView, CatalogUnitOfWork]):
    """Add a SKU to an existing, non-archived product."""

    operation = "catalog.create_sku"
    permission = "catalog.products.manage"
    resource_type = "sku"
    success_status = 201

    async def _execute(
        self, uow: CatalogUnitOfWork, command: CreateSkuCommand
    ) -> Executed[SkuView]:
        await self._authorize(
            command.context,
            action="catalog.products.manage",
            resource_type="sku",
            resource_tenant_id=command.tenant_id,
            resource_attributes={"product_id": str(command.product_id), "code": str(command.code)},
        )
        # The product row is locked so that "add a SKU" and "archive the product"
        # cannot interleave.
        product = await _require_product(
            uow, command.tenant_id, command.product_id, for_update=True
        )
        if product.is_archived:
            raise InvalidProductTransition(product.status.value, "ADD_SKU")
        sku = Sku.create(
            tenant_id=command.tenant_id,
            product_id=product.id,
            code=SkuCode(command.code.strip().upper()),
            name=command.name,
            attributes=normalise_attribute_input(command.attributes),
            unit_price=Money(command.unit_price_minor, command.currency.upper()),
            now=self._clock.now(),
        )
        await uow.skus.add(sku)
        return Executed(
            view=SkuView.of(sku),
            resource_type="sku",
            resource_id=sku.id,
            events=pull_events(sku),
        )

    async def _reload(
        self, uow: CatalogUnitOfWork, record: IdempotencyRecord, command: CreateSkuCommand
    ) -> SkuView:
        sku = await _require_sku(uow, command.tenant_id, SkuId(completed_resource_id(record)))
        return SkuView.of(sku)


class ChangeSkuPrice(UseCase[ChangeSkuPriceCommand, SkuView, CatalogUnitOfWork]):
    """Set a new list price for a SKU.

    Only future orders are affected: order lines keep their pricing snapshot.
    """

    operation = "catalog.change_sku_price"
    permission = "catalog.products.manage"
    resource_type = "sku"
    success_status = 200

    async def _execute(
        self, uow: CatalogUnitOfWork, command: ChangeSkuPriceCommand
    ) -> Executed[SkuView]:
        await self._authorize(
            command.context,
            action="catalog.products.manage",
            resource_type="sku",
            resource_id=str(command.sku_id),
            resource_tenant_id=command.tenant_id,
            resource_attributes={"price_amount": command.unit_price_minor, "currency": command.currency},
        )
        sku = await _require_sku(uow, command.tenant_id, command.sku_id, for_update=True)
        expected_version = sku.version
        sku.change_price(
            new_price=Money(command.unit_price_minor, command.currency.upper()),
            now=self._clock.now(),
        )
        await uow.skus.save(sku, expected_version=expected_version)
        return Executed(
            view=SkuView.of(sku),
            resource_type="sku",
            resource_id=sku.id,
            events=pull_events(sku),
        )

    async def _reload(
        self, uow: CatalogUnitOfWork, record: IdempotencyRecord, command: ChangeSkuPriceCommand
    ) -> SkuView:
        sku = await _require_sku(uow, command.tenant_id, SkuId(completed_resource_id(record)))
        return SkuView.of(sku)


class DeactivateSku(UseCase[ChangeSkuStateCommand, SkuView, CatalogUnitOfWork]):
    """Withdraw a SKU from sale."""

    operation = "catalog.deactivate_sku"
    permission = "catalog.products.manage"
    resource_type = "sku"
    success_status = 200

    async def _execute(
        self, uow: CatalogUnitOfWork, command: ChangeSkuStateCommand
    ) -> Executed[SkuView]:
        await self._authorize(
            command.context,
            action="catalog.products.manage",
            resource_type="sku",
            resource_id=str(command.sku_id),
            resource_tenant_id=command.tenant_id,
        )
        return await _transition_sku(uow, command, activate=False, now=self._clock.now())

    async def _reload(
        self, uow: CatalogUnitOfWork, record: IdempotencyRecord, command: ChangeSkuStateCommand
    ) -> SkuView:
        sku = await _require_sku(uow, command.tenant_id, SkuId(completed_resource_id(record)))
        return SkuView.of(sku)


class ActivateSku(UseCase[ChangeSkuStateCommand, SkuView, CatalogUnitOfWork]):
    """Return a previously deactivated SKU to sale."""

    operation = "catalog.activate_sku"
    permission = "catalog.products.manage"
    resource_type = "sku"
    success_status = 200

    async def _execute(
        self, uow: CatalogUnitOfWork, command: ChangeSkuStateCommand
    ) -> Executed[SkuView]:
        await self._authorize(
            command.context,
            action="catalog.products.manage",
            resource_type="sku",
            resource_id=str(command.sku_id),
            resource_tenant_id=command.tenant_id,
        )
        return await _transition_sku(uow, command, activate=True, now=self._clock.now())

    async def _reload(
        self, uow: CatalogUnitOfWork, record: IdempotencyRecord, command: ChangeSkuStateCommand
    ) -> SkuView:
        sku = await _require_sku(uow, command.tenant_id, SkuId(completed_resource_id(record)))
        return SkuView.of(sku)


# ---------------------------------------------------------------------------
# helpers shared by the use cases above (still free of infrastructure)
# ---------------------------------------------------------------------------
async def _require_product(
    uow: CatalogUnitOfWork,
    tenant_id: TenantId,
    product_id: ProductId,
    *,
    for_update: bool = False,
) -> Product:
    product = await uow.products.get(
        tenant_id=tenant_id, product_id=product_id, for_update=for_update
    )
    if product is None:
        raise NotFound("product", product_id)
    return product


async def _require_sku(
    uow: CatalogUnitOfWork, tenant_id: TenantId, sku_id: SkuId, *, for_update: bool = False
) -> Sku:
    sku = await uow.skus.get(tenant_id=tenant_id, sku_id=sku_id, for_update=for_update)
    if sku is None:
        raise NotFound("sku", sku_id)
    return sku


async def _transition_product(
    uow: CatalogUnitOfWork,
    tenant_id: TenantId,
    product_id: ProductId,
    *,
    activate: bool,
    now: datetime,
) -> Product:
    product = await _require_product(uow, tenant_id, product_id, for_update=True)
    expected_version = product.version
    if activate:
        product.activate(now=now)
    else:
        product.archive(now=now)
    await uow.products.save(product, expected_version=expected_version)
    return product


async def _transition_sku(
    uow: CatalogUnitOfWork,
    command: ChangeSkuStateCommand,
    *,
    activate: bool,
    now: datetime,
) -> Executed[SkuView]:
    sku = await _require_sku(uow, command.tenant_id, command.sku_id, for_update=True)
    expected_version = sku.version
    if activate:
        sku.activate(now=now)
    else:
        sku.deactivate(now=now)
    await uow.skus.save(sku, expected_version=expected_version)
    return Executed(
        view=SkuView.of(sku),
        resource_type="sku",
        resource_id=sku.id,
        events=pull_events(sku),
    )


__all__ = [
    "ActivateProduct",
    "ActivateSku",
    "ArchiveProduct",
    "ChangeSkuPrice",
    "CreateProduct",
    "CreateSku",
    "DeactivateSku",
    "ReviseProduct",
]
