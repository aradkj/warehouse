"""Reservation expiration integration tests against PostgreSQL.

Proves:
1. Expired active reservation (expires_at < now) is transitioned to EXPIRED by
   ExpireReservations use case.
2. Reserved stock is restored to available.
3. InventoryReservationExpired event is dispatched into outbox_events in the same transaction.
4. Non-expired reservation remains ACTIVE.
5. Re-running expiration sweep is idempotent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.conftest import test_database_url as get_test_database_url
from tests.support.database import count, scalar

from bootstrap.config import Settings
from bootstrap.dependencies import build_use_cases
from catalog.application.commands import (
    ChangeProductStateCommand,
    CreateProductCommand,
    CreateSkuCommand,
)
from identity.application.commands import RegisterTenantCommand
from inventory.application.commands import (
    AdjustInventoryCommand,
    ExpireReservationsCommand,
    RegisterWarehouseCommand,
    ReserveInventoryCommand,
)
from inventory.domain.allocation import AllocationStrategy
from inventory.domain.value_objects import AdjustmentReason, ReservationStatus, StockRequirement
from shared.application.context import ActorType, RequestContext
from shared.domain.clock import SystemClock
from shared.domain.identifiers import TenantId
from shared.domain.value_objects import Quantity
from shared.infrastructure.db.session import Database

pytestmark = [pytest.mark.repository]


@pytest.fixture
async def database_handle() -> Any:
    db = Database(get_test_database_url(), pool_size=2)
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture
async def admin_engine() -> Any:
    engine = Database(get_test_database_url())._engine
    yield engine


async def test_expired_reservation_is_released_and_stock_restored(
    database_handle: Database, admin_engine: AsyncEngine
) -> None:
    settings = Settings(
        database_url=get_test_database_url(),
        environment="test",
    )
    scope = database_handle.new_scope()
    use_cases = build_use_cases(settings=settings, clock=SystemClock(), scope=scope)

    # 1. Register tenant, warehouse, product, SKU, receive stock
    platform_ctx = RequestContext(actor_id="test", actor_type=ActorType.SERVICE_ACCOUNT)
    tenant_res = await use_cases.register_tenant(
        RegisterTenantCommand(
            context=platform_ctx, slug=f"exp-{uuid.uuid4().hex[:6]}", name="Exp Tenant"
        )
    )
    tenant_id = tenant_res.view.id
    tenant_uuid = TenantId(tenant_id)
    ctx = RequestContext(tenant_id=tenant_uuid, actor_id="test")

    wh = await use_cases.register_warehouse(
        RegisterWarehouseCommand(tenant_id=tenant_uuid, context=ctx, code="WH-EXP", name="WH EXP")
    )
    product = await use_cases.create_product(
        CreateProductCommand(
            tenant_id=tenant_uuid, context=ctx, slug="exp-product", name="Exp Product"
        )
    )
    await use_cases.activate_product(
        ChangeProductStateCommand(
            tenant_id=tenant_uuid, context=ctx, product_id=product.view.id
        )
    )
    sku = await use_cases.create_sku(
        CreateSkuCommand(
            tenant_id=tenant_uuid,
            context=ctx,
            product_id=product.view.id,
            code="SKU-EXP-1",
            name="SKU EXP 1",
            unit_price_minor=1000,
            currency="GBP",
        )
    )

    await use_cases.adjust_inventory(
        AdjustInventoryCommand(
            tenant_id=tenant_uuid,
            context=ctx,
            sku_id=sku.view.id,
            warehouse_id=wh.view.id,
            quantity_delta=10,
            reason=AdjustmentReason.GOODS_RECEIPT,
        )
    )

    # 2. Reserve stock with 1 second TTL
    res = await use_cases.reserve_inventory(
        ReserveInventoryCommand(
            tenant_id=tenant_uuid,
            context=ctx,
            requirements=(
                StockRequirement(sku_id=sku.view.id, quantity=Quantity(4), order_line_id=None),
            ),
            strategy=AllocationStrategy.ALL_OR_NOTHING,
            ttl_seconds=1,
        )
    )
    reservation_id = res.view.reservation.id

    # Verify stock reserved
    reserved_qty = await scalar(
        admin_engine, "SELECT reserved FROM inventory_items WHERE sku_id = :sku", sku=sku.view.id
    )
    assert reserved_qty == 4

    # 3. Fast forward time by updating expires_at to past
    async with database_handle.new_scope() as update_scope:
        from sqlalchemy import update

        from inventory.adapters.persistence.tables import reservations
        past_expires = datetime.now(UTC) - timedelta(seconds=10)
        past_created = past_expires - timedelta(minutes=10)
        await update_scope.session.execute(
            update(reservations)
            .where(reservations.c.id == reservation_id)
            .values(expires_at=past_expires, created_at=past_created)
        )
        await update_scope.commit()

    # 4. Run ExpireReservations use case
    expire_scope = database_handle.new_scope()
    exp_use_cases = build_use_cases(settings=settings, clock=SystemClock(), scope=expire_scope)
    outcome = await exp_use_cases.expire_reservations(
        ExpireReservationsCommand(context=ctx, limit=50)
    )
    assert outcome.view >= 1

    # 5. Assert reservation status is EXPIRED and stock is restored
    res_status = await scalar(
        admin_engine, "SELECT status FROM reservations WHERE id = :id", id=reservation_id
    )
    assert res_status == ReservationStatus.EXPIRED.value

    reserved_stock = await scalar(
        admin_engine, "SELECT reserved FROM inventory_items WHERE sku_id = :sku", sku=sku.view.id
    )
    assert reserved_stock == 0

    # 6. Assert ReservationExpired event is in outbox_events
    outbox_count = await count(
        admin_engine,
        "outbox_events",
        tenant_id=tenant_id,
        event_type="ReservationExpired",
    )
    assert outbox_count >= 1


async def test_active_future_reservation_is_not_expired(
    database_handle: Database, admin_engine: AsyncEngine
) -> None:
    """Reservations whose expires_at is still in the future are skipped by the sweeper."""
    settings = Settings(
        database_url=get_test_database_url(),
        environment="test",
    )
    scope = database_handle.new_scope()
    use_cases = build_use_cases(settings=settings, clock=SystemClock(), scope=scope)

    platform_ctx = RequestContext(actor_id="test", actor_type=ActorType.SERVICE_ACCOUNT)
    tenant_res = await use_cases.register_tenant(
        RegisterTenantCommand(
            context=platform_ctx, slug=f"fut-{uuid.uuid4().hex[:6]}", name="Future Tenant"
        )
    )
    tenant_id = tenant_res.view.id
    tenant_uuid = TenantId(tenant_id)
    ctx = RequestContext(tenant_id=tenant_uuid, actor_id="test")

    wh = await use_cases.register_warehouse(
        RegisterWarehouseCommand(tenant_id=tenant_uuid, context=ctx, code="WH-FUT", name="WH FUT")
    )
    product = await use_cases.create_product(
        CreateProductCommand(
            tenant_id=tenant_uuid, context=ctx, slug="fut-product", name="Fut Product"
        )
    )
    sku = await use_cases.create_sku(
        CreateSkuCommand(
            tenant_id=tenant_uuid,
            context=ctx,
            product_id=product.view.id,
            code="SKU-FUT-1",
            name="SKU FUT 1",
            unit_price_minor=1000,
            currency="GBP",
        )
    )
    await use_cases.adjust_inventory(
        AdjustInventoryCommand(
            tenant_id=tenant_uuid,
            context=ctx,
            sku_id=sku.view.id,
            warehouse_id=wh.view.id,
            quantity_delta=10,
            reason=AdjustmentReason.GOODS_RECEIPT,
        )
    )

    # Reserve with 3600 seconds TTL (far in the future)
    res = await use_cases.reserve_inventory(
        ReserveInventoryCommand(
            tenant_id=tenant_uuid,
            context=ctx,
            requirements=(
                StockRequirement(sku_id=sku.view.id, quantity=Quantity(3), order_line_id=None),
            ),
            strategy=AllocationStrategy.ALL_OR_NOTHING,
            ttl_seconds=3600,
        )
    )
    res_id = res.view.reservation.id

    # Run sweeper
    sweep_scope = database_handle.new_scope()
    sweep_use_cases = build_use_cases(settings=settings, clock=SystemClock(), scope=sweep_scope)
    outcome = await sweep_use_cases.expire_reservations(
        ExpireReservationsCommand(context=ctx, limit=50)
    )
    assert outcome.view == 0

    # Reservation remains ACTIVE
    status = await scalar(
        admin_engine, "SELECT status FROM reservations WHERE id = :id", id=res_id
    )
    assert status == ReservationStatus.ACTIVE.value

    # Stock reserved remains 3
    reserved = await scalar(
        admin_engine, "SELECT reserved FROM inventory_items WHERE sku_id = :sku", sku=sku.view.id
    )
    assert reserved == 3


async def test_concurrent_expiration_sweep_does_not_double_release(
    database_handle: Database, admin_engine: AsyncEngine
) -> None:
    """Two concurrent workers running expire_reservations do not double-release inventory."""
    import asyncio

    settings = Settings(
        database_url=get_test_database_url(),
        environment="test",
    )
    scope = database_handle.new_scope()
    use_cases = build_use_cases(settings=settings, clock=SystemClock(), scope=scope)

    platform_ctx = RequestContext(actor_id="test", actor_type=ActorType.SERVICE_ACCOUNT)
    tenant_res = await use_cases.register_tenant(
        RegisterTenantCommand(
            context=platform_ctx, slug=f"cexp-{uuid.uuid4().hex[:6]}", name="Conc Exp Tenant"
        )
    )
    tenant_id = tenant_res.view.id
    tenant_uuid = TenantId(tenant_id)
    ctx = RequestContext(tenant_id=tenant_uuid, actor_id="test")

    wh = await use_cases.register_warehouse(
        RegisterWarehouseCommand(tenant_id=tenant_uuid, context=ctx, code="WH-CEXP", name="WH CEXP")
    )
    product = await use_cases.create_product(
        CreateProductCommand(
            tenant_id=tenant_uuid, context=ctx, slug="cexp-product", name="Cexp Product"
        )
    )
    sku = await use_cases.create_sku(
        CreateSkuCommand(
            tenant_id=tenant_uuid,
            context=ctx,
            product_id=product.view.id,
            code="SKU-CEXP-1",
            name="SKU CEXP 1",
            unit_price_minor=1000,
            currency="GBP",
        )
    )
    await use_cases.adjust_inventory(
        AdjustInventoryCommand(
            tenant_id=tenant_uuid,
            context=ctx,
            sku_id=sku.view.id,
            warehouse_id=wh.view.id,
            quantity_delta=10,
            reason=AdjustmentReason.GOODS_RECEIPT,
        )
    )

    res = await use_cases.reserve_inventory(
        ReserveInventoryCommand(
            tenant_id=tenant_uuid,
            context=ctx,
            requirements=(
                StockRequirement(sku_id=sku.view.id, quantity=Quantity(5), order_line_id=None),
            ),
            strategy=AllocationStrategy.ALL_OR_NOTHING,
            ttl_seconds=1,
        )
    )
    res_id = res.view.reservation.id

    # Fast-forward expires_at to past
    async with database_handle.new_scope() as update_scope:
        from sqlalchemy import update

        from inventory.adapters.persistence.tables import reservations
        past_expires = datetime.now(UTC) - timedelta(seconds=10)
        past_created = past_expires - timedelta(minutes=10)
        await update_scope.session.execute(
            update(reservations)
            .where(reservations.c.id == res_id)
            .values(expires_at=past_expires, created_at=past_created)
        )
        await update_scope.commit()

    # Run two workers concurrently
    async def _sweep() -> Any:
        sw_scope = database_handle.new_scope()
        sw_use_cases = build_use_cases(settings=settings, clock=SystemClock(), scope=sw_scope)
        return await sw_use_cases.expire_reservations(
            ExpireReservationsCommand(context=ctx, limit=50)
        )

    outcomes = await asyncio.gather(_sweep(), _sweep())

    # Total expired across both sweeps must equal 1 (not double-expired!)
    total_expired = sum(o.view for o in outcomes)
    assert total_expired == 1

    # Stock reserved must be 0 (never negative!)
    reserved = await scalar(
        admin_engine, "SELECT reserved FROM inventory_items WHERE sku_id = :sku", sku=sku.view.id
    )
    assert reserved == 0

