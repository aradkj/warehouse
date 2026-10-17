"""Durable PostgreSQL fulfillment persistence and concurrency integration tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa
from tests.conftest import test_database_url as get_test_database_url

from fulfillment.adapters.unit_of_work import SqlAlchemyFulfillmentUnitOfWork
from fulfillment.domain.fulfillment import Fulfillment
from fulfillment.domain.value_objects import FulfillmentStatus, FulfillmentTask
from shared.application.errors import ConcurrencyConflict, DuplicateResource
from shared.domain.identifiers import (
    FulfillmentTaskId,
    OrderId,
    SkuId,
    TenantId,
    WarehouseId,
    new_id,
)
from shared.domain.value_objects import Quantity
from shared.infrastructure.db.session import Database


@pytest.fixture
async def database_handle() -> Database:
    db = Database(get_test_database_url(), pool_size=5)
    try:
        yield db
    finally:
        await db.dispose()


async def _seed_prerequisites(
    scope: Any,
    tenant_id: uuid.UUID,
    order_id: uuid.UUID,
    warehouse_id: uuid.UUID,
    product_id: uuid.UUID,
    sku_id: uuid.UUID,
    order_status: str = "CONFIRMED",
    is_warehouse_active: bool = True,
) -> None:
    now = datetime.now(UTC)
    # 1. Tenant (tenants table is not RLS-isolated)
    await scope.session.execute(
        sa.text(
            "INSERT INTO tenants (id, slug, name, status, created_at, updated_at, version) "
            "VALUES (:id, :slug, :name, 'ACTIVE', :now, :now, 1) ON CONFLICT DO NOTHING"
        ),
        {"id": tenant_id, "slug": f"t-{uuid.uuid4().hex[:6]}", "name": "Test Tenant", "now": now},
    )
    # Set the tenant context for RLS policies
    await scope.set_tenant_context(tenant_id)

    # 2. Warehouse
    await scope.session.execute(
        sa.text(
            "INSERT INTO warehouses (id, tenant_id, code, name, is_active, created_at, updated_at, version) "
            "VALUES (:id, :tenant_id, :code, :name, :is_active, :now, :now, 1) ON CONFLICT DO NOTHING"
        ),
        {
            "id": warehouse_id,
            "tenant_id": tenant_id,
            "code": f"WH-{uuid.uuid4().hex[:6].upper()}",
            "name": "Warehouse",
            "is_active": is_warehouse_active,
            "now": now,
        },
    )
    # 3. Product & SKU
    await scope.session.execute(
        sa.text(
            "INSERT INTO products (id, tenant_id, slug, name, status, created_at, updated_at, version) "
            "VALUES (:id, :tenant_id, :slug, :name, 'ACTIVE', :now, :now, 1) ON CONFLICT DO NOTHING"
        ),
        {
            "id": product_id,
            "tenant_id": tenant_id,
            "slug": f"p-{uuid.uuid4().hex[:6]}",
            "name": "Prod",
            "now": now,
        },
    )
    await scope.session.execute(
        sa.text(
            "INSERT INTO skus (id, tenant_id, product_id, code, name, unit_price_minor, currency, status, created_at, updated_at, version) "
            "VALUES (:id, :tenant_id, :product_id, :code, :name, 1000, 'USD', 'ACTIVE', :now, :now, 1) ON CONFLICT DO NOTHING"
        ),
        {
            "id": sku_id,
            "tenant_id": tenant_id,
            "product_id": product_id,
            "code": f"SKU-{uuid.uuid4().hex[:6].upper()}",
            "name": "Sku",
            "now": now,
        },
    )
    # 4. Order
    confirmed_at = now if order_status == "CONFIRMED" else None
    await scope.session.execute(
        sa.text(
            "INSERT INTO orders (id, tenant_id, customer_reference, currency, status, confirmed_at, created_at, updated_at, version) "
            "VALUES (:id, :tenant_id, 'ref-1', 'USD', :status, :confirmed_at, :now, :now, 1) ON CONFLICT DO NOTHING"
        ),
        {
            "id": order_id,
            "tenant_id": tenant_id,
            "status": order_status,
            "confirmed_at": confirmed_at,
            "now": now,
        },
    )
    # 5. Order line
    await scope.session.execute(
        sa.text(
            "INSERT INTO order_lines (id, tenant_id, order_id, line_number, sku_id, product_id, sku_code, product_name, quantity, unit_price_minor, currency, created_at) "
            "VALUES (:id, :tenant_id, :order_id, 1, :sku_id, :product_id, 'SKU-1', 'Prod', 2, 1000, 'USD', :now) ON CONFLICT DO NOTHING"
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "order_id": order_id,
            "sku_id": sku_id,
            "product_id": product_id,
            "now": now,
        },
    )


@pytest.mark.asyncio
async def test_fulfillment_durability_across_uow_instances(database_handle: Database) -> None:
    """DATA-001: Fulfillment state persists to PostgreSQL and survives separate UoW instances."""
    t_id = uuid.uuid4()
    o_id = uuid.uuid4()
    w_id = uuid.uuid4()
    p_id = uuid.uuid4()
    s_id = uuid.uuid4()

    async with database_handle.new_scope() as seed_scope:
        await _seed_prerequisites(seed_scope, t_id, o_id, w_id, p_id, s_id)
        await seed_scope.commit()

    tenant_id = TenantId(t_id)
    order_id = OrderId(o_id)
    warehouse_id = WarehouseId(w_id)
    sku_id = SkuId(s_id)

    task = FulfillmentTask(
        task_id=FulfillmentTaskId(new_id()),
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        quantity=Quantity(2),
    )
    fulfillment = Fulfillment.create(
        tenant_id=tenant_id,
        order_id=order_id,
        warehouse_id=warehouse_id,
        tasks=[task],
        max_retries=3,
    )
    fulfillment.allocate()

    # Save via UoW 1
    async with SqlAlchemyFulfillmentUnitOfWork(
        database_handle.new_scope(tenant_id=str(tenant_id))
    ) as uow1:
        await uow1.fulfillments.add(fulfillment)
        await uow1.commit()

    # Read via separate UoW 2
    async with SqlAlchemyFulfillmentUnitOfWork(
        database_handle.new_scope(tenant_id=str(tenant_id))
    ) as uow2:
        loaded = await uow2.fulfillments.get(tenant_id=tenant_id, fulfillment_id=fulfillment.id)
        assert loaded is not None
        assert loaded.id == fulfillment.id
        assert loaded.tenant_id == tenant_id
        assert loaded.order_id == order_id
        assert loaded.warehouse_id == warehouse_id
        assert loaded.status == FulfillmentStatus.ALLOCATED
        assert loaded.version == fulfillment.version
        assert len(loaded.tasks) == 1
        assert loaded.tasks[0].sku_id == sku_id
        assert loaded.tasks[0].quantity == Quantity(2)

        # Also verify list_by_order
        by_order = await uow2.fulfillments.list_by_order(tenant_id=tenant_id, order_id=order_id)
        assert len(by_order) == 1
        assert by_order[0].id == fulfillment.id


@pytest.mark.asyncio
async def test_fulfillment_rollback_does_not_persist(database_handle: Database) -> None:
    """DATA-001: Rolling back transaction scope discards fulfillment writes."""
    t_id = uuid.uuid4()
    o_id = uuid.uuid4()
    w_id = uuid.uuid4()
    p_id = uuid.uuid4()
    s_id = uuid.uuid4()

    async with database_handle.new_scope() as seed_scope:
        await _seed_prerequisites(seed_scope, t_id, o_id, w_id, p_id, s_id)
        await seed_scope.commit()

    tenant_id = TenantId(t_id)
    order_id = OrderId(o_id)
    warehouse_id = WarehouseId(w_id)
    sku_id = SkuId(s_id)

    task = FulfillmentTask(
        task_id=FulfillmentTaskId(new_id()),
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        quantity=Quantity(1),
    )
    fulfillment = Fulfillment.create(
        tenant_id=tenant_id,
        order_id=order_id,
        warehouse_id=warehouse_id,
        tasks=[task],
    )

    # Add and rollback
    async with SqlAlchemyFulfillmentUnitOfWork(
        database_handle.new_scope(tenant_id=str(tenant_id))
    ) as uow:
        await uow.fulfillments.add(fulfillment)
        await uow.rollback()

    # Verify not present in database
    async with SqlAlchemyFulfillmentUnitOfWork(
        database_handle.new_scope(tenant_id=str(tenant_id))
    ) as check_uow:
        res = await check_uow.fulfillments.get(tenant_id=tenant_id, fulfillment_id=fulfillment.id)
        assert res is None


@pytest.mark.asyncio
async def test_fulfillment_rls_tenant_isolation(database_handle: Database) -> None:
    """DATA-001: Row-Level Security prevents cross-tenant access to fulfillments."""
    t1_id = uuid.uuid4()
    o1_id = uuid.uuid4()
    w1_id = uuid.uuid4()
    p1_id = uuid.uuid4()
    s1_id = uuid.uuid4()

    t2_id = uuid.uuid4()
    o2_id = uuid.uuid4()
    w2_id = uuid.uuid4()
    p2_id = uuid.uuid4()
    s2_id = uuid.uuid4()

    async with database_handle.new_scope() as seed_scope1:
        await _seed_prerequisites(seed_scope1, t1_id, o1_id, w1_id, p1_id, s1_id)
        await seed_scope1.commit()

    async with database_handle.new_scope() as seed_scope2:
        await _seed_prerequisites(seed_scope2, t2_id, o2_id, w2_id, p2_id, s2_id)
        await seed_scope2.commit()

    tenant_1 = TenantId(t1_id)
    tenant_2 = TenantId(t2_id)

    task = FulfillmentTask(
        task_id=FulfillmentTaskId(new_id()),
        warehouse_id=WarehouseId(w1_id),
        sku_id=SkuId(s1_id),
        quantity=Quantity(1),
    )
    fulfillment_t1 = Fulfillment.create(
        tenant_id=tenant_1,
        order_id=OrderId(o1_id),
        warehouse_id=WarehouseId(w1_id),
        tasks=[task],
    )

    # Persist for Tenant 1
    async with SqlAlchemyFulfillmentUnitOfWork(
        database_handle.new_scope(tenant_id=str(tenant_1))
    ) as uow1:
        await uow1.fulfillments.add(fulfillment_t1)
        await uow1.commit()

    # Query with Tenant 2 context (RLS active)
    async with SqlAlchemyFulfillmentUnitOfWork(
        database_handle.new_scope(tenant_id=str(tenant_2))
    ) as uow2:
        cross_read = await uow2.fulfillments.get(
            tenant_id=tenant_2, fulfillment_id=fulfillment_t1.id
        )
        assert cross_read is None

        by_order = await uow2.fulfillments.list_by_order(
            tenant_id=tenant_2, order_id=OrderId(o1_id)
        )
        assert len(by_order) == 0


@pytest.mark.asyncio
async def test_fulfillment_active_uniqueness_prevents_concurrent_duplicates(
    database_handle: Database,
) -> None:
    """BIZ-002: PostgreSQL partial unique index enforces single active fulfillment per order."""
    t_id = uuid.uuid4()
    o_id = uuid.uuid4()
    w_id = uuid.uuid4()
    p_id = uuid.uuid4()
    s_id = uuid.uuid4()

    async with database_handle.new_scope() as seed_scope:
        await _seed_prerequisites(seed_scope, t_id, o_id, w_id, p_id, s_id)
        await seed_scope.commit()

    tenant_id = TenantId(t_id)
    order_id = OrderId(o_id)
    warehouse_id = WarehouseId(w_id)
    sku_id = SkuId(s_id)

    task1 = FulfillmentTask(
        task_id=FulfillmentTaskId(new_id()),
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        quantity=Quantity(1),
    )
    f1 = Fulfillment.create(
        tenant_id=tenant_id,
        order_id=order_id,
        warehouse_id=warehouse_id,
        tasks=[task1],
    )

    task2 = FulfillmentTask(
        task_id=FulfillmentTaskId(new_id()),
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        quantity=Quantity(1),
    )
    f2 = Fulfillment.create(
        tenant_id=tenant_id,
        order_id=order_id,
        warehouse_id=warehouse_id,
        tasks=[task2],
    )

    # First fulfillment succeeds
    async with SqlAlchemyFulfillmentUnitOfWork(
        database_handle.new_scope(tenant_id=str(tenant_id))
    ) as uow1:
        await uow1.fulfillments.add(f1)
        await uow1.commit()

    # Second active fulfillment for the same order raises DuplicateResource from database index
    with pytest.raises(DuplicateResource) as exc_info:
        async with SqlAlchemyFulfillmentUnitOfWork(
            database_handle.new_scope(tenant_id=str(tenant_id))
        ) as uow2:
            await uow2.fulfillments.add(f2)
            await uow2.commit()

    assert exc_info.value.resource_type == "fulfillment"
    assert exc_info.value.field == "order_id"


@pytest.mark.asyncio
async def test_fulfillment_save_optimistic_concurrency(database_handle: Database) -> None:
    """DATA-001: Optimistic concurrency control via expected_version protects against lost updates."""
    t_id = uuid.uuid4()
    o_id = uuid.uuid4()
    w_id = uuid.uuid4()
    p_id = uuid.uuid4()
    s_id = uuid.uuid4()

    async with database_handle.new_scope() as seed_scope:
        await _seed_prerequisites(seed_scope, t_id, o_id, w_id, p_id, s_id)
        await seed_scope.commit()

    tenant_id = TenantId(t_id)
    order_id = OrderId(o_id)
    warehouse_id = WarehouseId(w_id)
    sku_id = SkuId(s_id)

    task = FulfillmentTask(
        task_id=FulfillmentTaskId(new_id()),
        warehouse_id=warehouse_id,
        sku_id=sku_id,
        quantity=Quantity(1),
    )
    f = Fulfillment.create(
        tenant_id=tenant_id,
        order_id=order_id,
        warehouse_id=warehouse_id,
        tasks=[task],
    )

    async with SqlAlchemyFulfillmentUnitOfWork(
        database_handle.new_scope(tenant_id=str(tenant_id))
    ) as uow:
        await uow.fulfillments.add(f)
        await uow.commit()

    # Saving with wrong version raises ConcurrencyConflict
    async with SqlAlchemyFulfillmentUnitOfWork(
        database_handle.new_scope(tenant_id=str(tenant_id))
    ) as uow_conflict:
        loaded = await uow_conflict.fulfillments.get(tenant_id=tenant_id, fulfillment_id=f.id)
        assert loaded is not None
        loaded.allocate()
        with pytest.raises(ConcurrencyConflict):
            await uow_conflict.fulfillments.save(loaded, expected_version=999)

    # Saving with correct expected_version succeeds
    async with SqlAlchemyFulfillmentUnitOfWork(
        database_handle.new_scope(tenant_id=str(tenant_id))
    ) as uow_success:
        loaded = await uow_success.fulfillments.get(tenant_id=tenant_id, fulfillment_id=f.id)
        assert loaded is not None
        expected_v = loaded.version
        loaded.allocate()
        await uow_success.fulfillments.save(loaded, expected_version=expected_v)
        await uow_success.commit()


async def test_database_rejects_cross_tenant_order_reference(database_handle: Database) -> None:
    """PostgreSQL foreign key constraint strictly rejects fulfillment referencing another tenant's order."""
    t1 = uuid.uuid4()
    t2 = uuid.uuid4()

    # Seed Tenant 1
    w1 = uuid.uuid4()
    p1 = uuid.uuid4()
    s1 = uuid.uuid4()
    o1 = uuid.uuid4()
    async with database_handle.new_scope() as seed_scope:
        await _seed_prerequisites(seed_scope, t1, o1, w1, p1, s1)
        await seed_scope.commit()

    # Seed Tenant 2
    w2 = uuid.uuid4()
    p2 = uuid.uuid4()
    s2 = uuid.uuid4()
    o2 = uuid.uuid4()
    async with database_handle.new_scope() as seed_scope:
        await _seed_prerequisites(seed_scope, t2, o2, w2, p2, s2)
        await seed_scope.commit()

    # Tenant 1 attempts to create a fulfillment referencing Tenant 2's order
    async with database_handle.new_scope(tenant_id=str(t1)) as scope:
        f_id = uuid.uuid4()
        with pytest.raises(
            sa.exc.IntegrityError, match="fk_fulfillments_tenant_id_order_id_orders"
        ):
            await scope.session.execute(
                sa.text(
                    "INSERT INTO fulfillments (id, tenant_id, order_id, warehouse_id, status, version) "
                    "VALUES (:id, :t1, :o2, :w1, 'PENDING', 1)"
                ),
                {"id": f_id, "t1": t1, "o2": o2, "w1": w1},
            )
            await scope.commit()


async def test_database_rejects_cross_tenant_warehouse_reference(database_handle: Database) -> None:
    """PostgreSQL foreign key constraint strictly rejects fulfillment referencing another tenant's warehouse."""
    t1 = uuid.uuid4()
    t2 = uuid.uuid4()

    # Seed Tenant 1
    w1 = uuid.uuid4()
    p1 = uuid.uuid4()
    s1 = uuid.uuid4()
    o1 = uuid.uuid4()
    async with database_handle.new_scope() as seed_scope:
        await _seed_prerequisites(seed_scope, t1, o1, w1, p1, s1)
        await seed_scope.commit()

    # Seed Tenant 2
    w2 = uuid.uuid4()
    p2 = uuid.uuid4()
    s2 = uuid.uuid4()
    o2 = uuid.uuid4()
    async with database_handle.new_scope() as seed_scope:
        await _seed_prerequisites(seed_scope, t2, o2, w2, p2, s2)
        await seed_scope.commit()

    # Tenant 1 attempts to create a fulfillment referencing Tenant 2's warehouse
    async with database_handle.new_scope(tenant_id=str(t1)) as scope:
        f_id = uuid.uuid4()
        with pytest.raises(
            sa.exc.IntegrityError, match="fk_fulfillments_tenant_id_warehouse_id_warehouses"
        ):
            await scope.session.execute(
                sa.text(
                    "INSERT INTO fulfillments (id, tenant_id, order_id, warehouse_id, status, version) "
                    "VALUES (:id, :t1, :o1, :w2, 'PENDING', 1)"
                ),
                {"id": f_id, "t1": t1, "o1": o1, "w2": w2},
            )
            await scope.commit()


async def test_database_rejects_cross_tenant_task_sku_reference(database_handle: Database) -> None:
    """PostgreSQL foreign key constraint strictly rejects fulfillment task referencing another tenant's SKU."""
    t1 = uuid.uuid4()
    t2 = uuid.uuid4()

    w1 = uuid.uuid4()
    p1 = uuid.uuid4()
    s1 = uuid.uuid4()
    o1 = uuid.uuid4()
    async with database_handle.new_scope() as seed_scope:
        await _seed_prerequisites(seed_scope, t1, o1, w1, p1, s1)
        await seed_scope.commit()

    w2 = uuid.uuid4()
    p2 = uuid.uuid4()
    s2 = uuid.uuid4()
    o2 = uuid.uuid4()
    async with database_handle.new_scope() as seed_scope:
        await _seed_prerequisites(seed_scope, t2, o2, w2, p2, s2)
        await seed_scope.commit()

    # Create valid fulfillment for Tenant 1
    f_id = uuid.uuid4()
    async with database_handle.new_scope(tenant_id=str(t1)) as scope:
        await scope.session.execute(
            sa.text(
                "INSERT INTO fulfillments (id, tenant_id, order_id, warehouse_id, status, version) "
                "VALUES (:id, :t1, :o1, :w1, 'PENDING', 1)"
            ),
            {"id": f_id, "t1": t1, "o1": o1, "w1": w1},
        )
        await scope.commit()

    # Tenant 1 attempts to create a task referencing Tenant 2's SKU (s2)
    async with database_handle.new_scope(tenant_id=str(t1)) as scope:
        task_id = uuid.uuid4()
        with pytest.raises(
            sa.exc.IntegrityError, match="fk_fulfillment_tasks_tenant_id_sku_id_skus"
        ):
            await scope.session.execute(
                sa.text(
                    "INSERT INTO fulfillment_tasks (id, tenant_id, fulfillment_id, warehouse_id, sku_id, quantity) "
                    "VALUES (:id, :t1, :f_id, :w1, :s2, 5)"
                ),
                {"id": task_id, "t1": t1, "f_id": f_id, "w1": w1, "s2": s2},
            )
            await scope.commit()
