"""Fixtures for application-layer tests.

These tests run the *real* use cases over in-memory ports: no PostgreSQL, no
HTTP, but the actual orchestration - transaction boundaries, idempotency claims,
the in-process cross-context call from ordering into inventory.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from tests.fakes.application import FakeApplication, build_fake_application

from catalog.application.commands import (
    ChangeProductStateCommand,
    CreateProductCommand,
    CreateSkuCommand,
)
from identity.application.commands import RegisterTenantCommand
from inventory.application.commands import AdjustInventoryCommand, RegisterWarehouseCommand
from inventory.domain.value_objects import AdjustmentReason
from ordering.domain.policies import UnavailableStockPolicy
from shared.application.context import ActorType, RequestContext
from shared.domain.identifiers import ProductId, SkuId, TenantId, WarehouseId
from shared.domain.value_objects import Quantity


@dataclass(slots=True)
class Shop:
    """A tenant with one purchasable SKU and stock in one warehouse.

    Built through the same use cases the HTTP layer calls, so the fixture cannot
    drift from production behaviour (or from the tests that follow it).
    """

    app: FakeApplication
    tenant_id: TenantId
    product_id: ProductId
    sku_id: SkuId
    warehouse_id: WarehouseId
    context: RequestContext
    sku_code: str = "TEE-BLUE-M"
    unit_price_minor: int = 2599
    currency: str = "GBP"
    extra_sku_ids: list[SkuId] = field(default_factory=list)

    def stock_of(self, sku_id: SkuId | None = None) -> tuple[int, int, int]:
        """``(on_hand, reserved, available)`` for a SKU across all warehouses."""
        target = sku_id or self.sku_id
        on_hand = reserved = available = 0
        for item in self.app.stores.inventory_items.values():
            if item.tenant_id == self.tenant_id and item.sku_id == target:
                on_hand += int(item.on_hand)
                reserved += int(item.reserved)
                available += int(item.available)
        return on_hand, reserved, available

    def reservations_of(self, sku_id: SkuId | None = None) -> list[object]:
        target = sku_id or self.sku_id
        found = []
        for reservation in self.app.stores.reservations.values():
            if reservation.tenant_id != self.tenant_id:
                continue
            if any(allocation.sku_id == target for allocation in reservation.allocations):
                found.append(reservation)
        return found

    def with_context(self, **overrides: object) -> RequestContext:
        return self.context.child(**overrides)

    async def add_sku(self, code: str, unit_price_minor: int) -> SkuId:
        """Create (and activate) another SKU on the same product."""
        outcome = await self.app.use_cases.create_sku(
            CreateSkuCommand(
                tenant_id=self.tenant_id,
                context=self.context,
                product_id=self.product_id,
                code=code,
                name=code,
                unit_price_minor=unit_price_minor,
                currency=self.currency,
            )
        )
        self.extra_sku_ids.append(outcome.view.id)
        return outcome.view.id


async def _build_shop(
    *, on_hand: int = 0, warehouse_count: int = 1, unavailable_policy: str | None = None
) -> Shop:
    policy = (
        UnavailableStockPolicy[unavailable_policy]
        if unavailable_policy is not None
        else UnavailableStockPolicy.BACKORDER
    )
    app = build_fake_application(unavailable_policy=policy)
    context = RequestContext(actor_id="test", actor_type=ActorType.SERVICE_ACCOUNT)

    tenant = await app.use_cases.register_tenant(
        RegisterTenantCommand(context=context, slug=f"acme-{id(app)}", name="Acme")
    )
    tenant_id = tenant.view.id
    tenant_context = context.child(tenant_id=tenant_id)

    product = await app.use_cases.create_product(
        CreateProductCommand(
            tenant_id=tenant_id,
            context=tenant_context,
            slug="blue-tee",
            name="Blue Tee",
        )
    )
    await app.use_cases.activate_product(
        ChangeProductStateCommand(
            tenant_id=tenant_id, context=tenant_context, product_id=product.view.id
        )
    )
    sku = await app.use_cases.create_sku(
        CreateSkuCommand(
            tenant_id=tenant_id,
            context=tenant_context,
            product_id=product.view.id,
            code="TEE-BLUE-M",
            name="Blue Tee M",
            unit_price_minor=2599,
            currency="GBP",
        )
    )

    warehouses: list[WarehouseId] = []
    for index in range(warehouse_count):
        warehouse = await app.use_cases.register_warehouse(
            RegisterWarehouseCommand(
                tenant_id=tenant_id,
                context=tenant_context,
                code=f"LON{index + 1}",
                name=f"London DC {index + 1}",
            )
        )
        warehouses.append(warehouse.view.id)

    if on_hand:
        per_warehouse = _split(on_hand, warehouse_count)
        for index, warehouse_id in enumerate(warehouses):
            if not per_warehouse[index]:
                continue
            await app.use_cases.adjust_inventory(
                AdjustInventoryCommand(
                    tenant_id=tenant_id,
                    context=tenant_context,
                    sku_id=sku.view.id,
                    warehouse_id=warehouse_id,
                    quantity_delta=per_warehouse[index],
                    reason=AdjustmentReason.GOODS_RECEIPT,
                )
            )

    return Shop(
        app=app,
        tenant_id=tenant_id,
        product_id=product.view.id,
        sku_id=sku.view.id,
        warehouse_id=warehouses[0],
        context=tenant_context,
    )


def _split(total: int, parts: int) -> list[int]:
    base, remainder = divmod(total, parts)
    return [base + (1 if index < remainder else 0) for index in range(parts)]


@pytest.fixture
async def shop() -> Shop:
    """A tenant, one active product/SKU and an empty warehouse."""
    return await _build_shop()


@pytest.fixture
async def stocked_shop() -> Shop:
    """The same, with 10 units on hand."""
    return await _build_shop(on_hand=10)


def quantity(value: int) -> Quantity:
    return Quantity(value)
