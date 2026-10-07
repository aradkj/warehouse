"""Repository tests against real PostgreSQL.

These prove the things a fake cannot: that the SQLAlchemy adapters really persist
and read back, that tenant scoping is enforced in SQL, that the unique constraints
and the optimistic version guard fire in the database, and that stock levels are
protected by row-level constraints rather than by application convention.

Wiring is the production wiring - ``build_use_cases`` over a real ``Database``
scope - so an adapter bug cannot hide behind a test-only code path.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tests.conftest import test_database_url as resolve_database_url
from tests.support.database import count, rows, scalar

from bootstrap.config import Settings
from bootstrap.dependencies import build_use_cases
from catalog.application.commands import (
    ChangeProductStateCommand,
    ChangeSkuPriceCommand,
    CreateProductCommand,
    CreateSkuCommand,
    GetProductQuery,
)
from identity.application.commands import RegisterTenantCommand
from inventory.application.commands import (
    AdjustInventoryCommand,
    GetReservationQuery,
    RegisterWarehouseCommand,
    ReserveInventoryCommand,
)
from inventory.domain.allocation import AllocationStrategy
from inventory.domain.errors import InsufficientInventory
from inventory.domain.value_objects import AdjustmentReason, StockRequirement
from ordering.application.commands import (
    CreateOrderCommand,
    GetOrderQuery,
    ListOrdersQuery,
    OrderLineRequest,
)
from ordering.domain.value_objects import OrderStatus
from shared.application.context import ActorType, RequestContext
from shared.application.errors import ConcurrencyConflict, DuplicateResource, NotFound
from shared.application.idempotency import IdempotencyState
from shared.application.pagination import decode_cursor
from shared.domain.clock import SystemClock
from shared.domain.identifiers import ProductId, SkuId, TenantId
from shared.domain.value_objects import Quantity
from shared.infrastructure.db.session import Database

pytestmark = pytest.mark.repository


@pytest.fixture
async def database_handle() -> AsyncIterator[Database]:
    """Real database handle: this is what the adapters are pointed at."""
    handle = Database(resolve_database_url(), pool_size=2, max_overflow=2)
    try:
        yield handle
    finally:
        await handle.dispose()


@pytest.fixture
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    """Read-only engine for assertions on what PostgreSQL actually stores."""
    engine = create_async_engine(resolve_database_url())
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def use_cases(database_handle: Database) -> object:
    settings = Settings(
        database_url=resolve_database_url(), environment="test", log_level="WARNING", log_json=False
    )
    return build_use_cases(
        settings=settings, clock=SystemClock(), scope=database_handle.new_scope()
    )


@pytest.fixture
async def tenant_id(use_cases: object) -> str:
    registered = await use_cases.register_tenant(  # type: ignore[attr-defined]
        RegisterTenantCommand(
            context=RequestContext(
                actor_id="repository-test", actor_type=ActorType.SERVICE_ACCOUNT
            ),
            slug="persistence-co",
            name="Persistence Co",
        )
    )
    return registered.view.id


def context_for(tenant_id: str, *, idempotency_key: str | None = None) -> RequestContext:
    return RequestContext(
        tenant_id=TenantId(tenant_id),
        actor_id="repository-test",
        actor_type=ActorType.SERVICE_ACCOUNT,
        idempotency_key=idempotency_key,
    )


async def provision_catalog(
    use_cases: object, tenant_id: str, *, slug: str = "round-trip", code: str = "RT-1"
) -> tuple[ProductId, SkuId]:
    """Create and activate a product with one active SKU."""
    tenant_uuid = TenantId(tenant_id)
    context = context_for(tenant_id)
    product = await use_cases.create_product(  # type: ignore[attr-defined]
        CreateProductCommand(
            tenant_id=tenant_uuid, context=context, slug=slug, name=slug.replace("-", " ").title()
        )
    )
    await use_cases.activate_product(  # type: ignore[attr-defined]
        ChangeProductStateCommand(
            tenant_id=tenant_uuid, context=context, product_id=product.view.id
        )
    )
    sku = await use_cases.create_sku(  # type: ignore[attr-defined]
        CreateSkuCommand(
            tenant_id=tenant_uuid,
            context=context,
            product_id=product.view.id,
            code=code,
            name=code,
            unit_price_minor=2599,
            currency="GBP",
        )
    )
    return product.view.id, sku.view.id


async def provision_warehouse(
    use_cases: object,
    tenant_id: str,
    *,
    code: str = "LON1",
    quantity: int | None = None,
    sku_id: SkuId | None = None,
) -> str:
    """Register a warehouse, optionally receiving stock for ``sku_id``."""
    tenant_uuid = TenantId(tenant_id)
    context = context_for(tenant_id)
    warehouse = await use_cases.register_warehouse(  # type: ignore[attr-defined]
        RegisterWarehouseCommand(tenant_id=tenant_uuid, context=context, code=code, name=code)
    )
    if quantity and sku_id is not None:
        await use_cases.adjust_inventory(  # type: ignore[attr-defined]
            AdjustInventoryCommand(
                tenant_id=tenant_uuid,
                context=context,
                sku_id=sku_id,
                warehouse_id=warehouse.view.id,
                quantity_delta=quantity,
                reason=AdjustmentReason.GOODS_RECEIPT,
            )
        )
    return warehouse.view.id


class TestCatalogPersistence:
    async def test_a_product_and_its_sku_round_trip(
        self, use_cases: object, tenant_id: str
    ) -> None:
        product_id, sku_id = await provision_catalog(use_cases, tenant_id)
        context = context_for(tenant_id)

        read_back = await use_cases.get_product(  # type: ignore[attr-defined]
            GetProductQuery(tenant_id=TenantId(tenant_id), context=context, product_id=product_id)
        )
        assert read_back.id == product_id
        assert [child.id for child in read_back.skus] == [sku_id]

        await use_cases.change_sku_price(  # type: ignore[attr-defined]
            ChangeSkuPriceCommand(
                tenant_id=TenantId(tenant_id),
                context=context,
                sku_id=sku_id,
                unit_price_minor=999,
                currency="GBP",
            )
        )

    async def test_price_changes_are_visible_to_a_fresh_read(
        self, use_cases: object, tenant_id: str, admin_engine: AsyncEngine
    ) -> None:
        _, sku_id = await provision_catalog(use_cases, tenant_id, slug="priced", code="PRC-1")
        await use_cases.change_sku_price(  # type: ignore[attr-defined]
            ChangeSkuPriceCommand(
                tenant_id=TenantId(tenant_id),
                context=context_for(tenant_id),
                sku_id=sku_id,
                unit_price_minor=999,
                currency="GBP",
            )
        )

        stored = await scalar(
            admin_engine, "SELECT unit_price_minor FROM skus WHERE id = :id", id=sku_id
        )
        assert stored == 999

    async def test_slug_uniqueness_is_enforced_by_the_database(
        self, use_cases: object, tenant_id: str
    ) -> None:
        context = context_for(tenant_id)
        command = CreateProductCommand(
            tenant_id=TenantId(tenant_id), context=context, slug="unique-slug", name="First"
        )
        await use_cases.create_product(command)  # type: ignore[attr-defined]

        with pytest.raises(DuplicateResource):
            await use_cases.create_product(  # type: ignore[attr-defined]
                CreateProductCommand(
                    tenant_id=TenantId(tenant_id),
                    context=context,
                    slug="unique-slug",
                    name="Second",
                )
            )

    async def test_the_same_slug_is_allowed_for_another_tenant(self, use_cases: object) -> None:
        platform = RequestContext(actor_id="t", actor_type=ActorType.SERVICE_ACCOUNT)
        first = await use_cases.register_tenant(  # type: ignore[attr-defined]
            RegisterTenantCommand(context=platform, slug="slug-a", name="A")
        )
        second = await use_cases.register_tenant(  # type: ignore[attr-defined]
            RegisterTenantCommand(context=platform, slug="slug-b", name="B")
        )

        for tenant in (first.view.id, second.view.id):
            await use_cases.create_product(  # type: ignore[attr-defined]
                CreateProductCommand(
                    tenant_id=tenant,
                    context=context_for(str(tenant)),
                    slug="shared-slug",
                    name="Shared",
                )
            )

    async def test_optimistic_concurrency_is_enforced_at_the_row(
        self, use_cases: object, tenant_id: str, database_handle: Database
    ) -> None:
        from catalog.adapters.persistence.repositories import SqlAlchemyProductRepository
        from catalog.domain.value_objects import ProductSlug

        product_id, _ = await provision_catalog(
            use_cases, tenant_id, slug="versioned", code="VER-1"
        )

        # Two independent sessions standing in for two concurrent writers.
        async with database_handle.session_factory() as session:
            repository = SqlAlchemyProductRepository(session)
            first = await repository.get_by_slug(
                tenant_id=TenantId(tenant_id), slug=ProductSlug("versioned")
            )
            assert first is not None and first.version == 2  # created, then activated

            first.revise(name="Winner", description=None, attributes=None, now=datetime.now(UTC))
            await repository.save(first, expected_version=2)
            await session.commit()

        async with database_handle.session_factory() as session:
            repository = SqlAlchemyProductRepository(session)
            stale = await repository.get(tenant_id=TenantId(tenant_id), product_id=product_id)
            assert stale is not None and stale.version == 3
            stale.revise(name="Stale", description=None, attributes=None, now=datetime.now(UTC))
            with pytest.raises(ConcurrencyConflict):
                await repository.save(stale, expected_version=2)

    async def test_tenant_scoped_reads_do_not_leak(self, use_cases: object) -> None:
        platform = RequestContext(actor_id="t", actor_type=ActorType.SERVICE_ACCOUNT)
        first = await use_cases.register_tenant(  # type: ignore[attr-defined]
            RegisterTenantCommand(context=platform, slug="tenant-a", name="Tenant A")
        )
        second = await use_cases.register_tenant(  # type: ignore[attr-defined]
            RegisterTenantCommand(context=platform, slug="tenant-b", name="Tenant B")
        )
        product = await use_cases.create_product(  # type: ignore[attr-defined]
            CreateProductCommand(
                tenant_id=first.view.id,
                context=context_for(str(first.view.id)),
                slug="private",
                name="Private",
            )
        )

        with pytest.raises(NotFound):
            await use_cases.get_product(  # type: ignore[attr-defined]
                GetProductQuery(
                    tenant_id=second.view.id,
                    context=context_for(str(second.view.id)),
                    product_id=product.view.id,
                )
            )


class TestInventoryPersistence:
    async def test_stock_movements_are_appended_not_updated(
        self, use_cases: object, tenant_id: str, admin_engine: AsyncEngine
    ) -> None:
        _, sku_id = await provision_catalog(use_cases, tenant_id, slug="stock-co", code="STK-1")
        warehouse_id = await provision_warehouse(use_cases, tenant_id)
        context = context_for(tenant_id)

        await use_cases.adjust_inventory(  # type: ignore[attr-defined]
            AdjustInventoryCommand(
                tenant_id=TenantId(tenant_id),
                context=context,
                sku_id=sku_id,
                warehouse_id=warehouse_id,
                quantity_delta=5,
                reason=AdjustmentReason.GOODS_RECEIPT,
            )
        )
        await use_cases.adjust_inventory(  # type: ignore[attr-defined]
            AdjustInventoryCommand(
                tenant_id=TenantId(tenant_id),
                context=context,
                sku_id=sku_id,
                warehouse_id=warehouse_id,
                quantity_delta=-2,
                reason=AdjustmentReason.DAMAGE,
            )
        )

        movements = await rows(
            admin_engine,
            "SELECT quantity_delta, on_hand_after FROM inventory_movements"
            " WHERE sku_id = :sku ORDER BY occurred_at, id",
            sku=sku_id,
        )
        assert [(row["quantity_delta"], row["on_hand_after"]) for row in movements] == [
            (5, 5),
            (-2, 3),
        ]
        assert (
            await scalar(
                admin_engine, "SELECT on_hand FROM inventory_items WHERE sku_id = :sku", sku=sku_id
            )
            == 3
        )

    async def test_a_reservation_and_its_allocations_are_persisted(
        self, use_cases: object, tenant_id: str, admin_engine: AsyncEngine
    ) -> None:
        _, sku_id = await provision_catalog(use_cases, tenant_id, slug="res-co", code="RES-1")
        await provision_warehouse(use_cases, tenant_id, quantity=6, sku_id=sku_id)
        context = context_for(tenant_id)

        reserved = await use_cases.reserve_inventory(  # type: ignore[attr-defined]
            ReserveInventoryCommand(
                tenant_id=TenantId(tenant_id),
                context=context,
                requirements=(
                    StockRequirement(sku_id=sku_id, quantity=Quantity(4), order_line_id=None),
                ),
                strategy=AllocationStrategy.ALL_OR_NOTHING,
            )
        )
        reservation_id = reserved.view.reservation.id

        read_back = await use_cases.get_reservation(  # type: ignore[attr-defined]
            GetReservationQuery(
                tenant_id=TenantId(tenant_id), context=context, reservation_id=reservation_id
            )
        )
        assert read_back.total_quantity == 4
        assert read_back.is_active
        assert await count(
            admin_engine, "reservation_allocations", reservation_id=reservation_id
        ) == len(read_back.allocations)
        assert (
            await scalar(
                admin_engine,
                "SELECT reserved FROM inventory_items WHERE sku_id = :sku",
                sku=sku_id,
            )
            == 4
        )

    async def test_the_database_refuses_an_oversold_row(
        self, use_cases: object, tenant_id: str, admin_engine: AsyncEngine
    ) -> None:
        """The aggregate guards availability; the CHECK constraint guards the table.

        Proving the constraint fires matters because it is what protects the data
        against *any* writer, including a future one that forgets the aggregate.
        """
        _, sku_id = await provision_catalog(use_cases, tenant_id, slug="check-co", code="CHK-1")
        await provision_warehouse(use_cases, tenant_id, quantity=1, sku_id=sku_id)

        async with admin_engine.begin() as connection:
            with pytest.raises(IntegrityError):
                await connection.execute(
                    text("UPDATE inventory_items SET reserved = on_hand + 1 WHERE sku_id = :sku"),
                    {"sku": sku_id},
                )

    async def test_a_reservation_larger_than_available_is_refused_and_changes_nothing(
        self, use_cases: object, tenant_id: str, admin_engine: AsyncEngine
    ) -> None:
        _, sku_id = await provision_catalog(use_cases, tenant_id, slug="guard-co", code="GRD-1")
        await provision_warehouse(use_cases, tenant_id, quantity=2, sku_id=sku_id)

        with pytest.raises(InsufficientInventory):
            await use_cases.reserve_inventory(  # type: ignore[attr-defined]
                ReserveInventoryCommand(
                    tenant_id=TenantId(tenant_id),
                    context=context_for(tenant_id),
                    requirements=(
                        StockRequirement(sku_id=sku_id, quantity=Quantity(3), order_line_id=None),
                    ),
                    strategy=AllocationStrategy.ALL_OR_NOTHING,
                )
            )

        assert await count(admin_engine, "reservations") == 0
        assert (
            await scalar(
                admin_engine,
                "SELECT on_hand - reserved FROM inventory_items WHERE sku_id = :sku",
                sku=sku_id,
            )
            == 2
        )


class TestOrderPersistence:
    async def test_an_order_survives_a_restart(
        self, use_cases: object, tenant_id: str, admin_engine: AsyncEngine
    ) -> None:
        _, sku_id = await provision_catalog(use_cases, tenant_id, slug="ord-co", code="ORD-1")
        await provision_warehouse(use_cases, tenant_id, quantity=5, sku_id=sku_id)

        submitted = await use_cases.create_order(  # type: ignore[attr-defined]
            CreateOrderCommand(
                tenant_id=TenantId(tenant_id),
                context=context_for(tenant_id, idempotency_key="persist-order"),
                lines=(OrderLineRequest(sku_id=sku_id, quantity=2),),
                customer_reference="PO-PERSIST",
            )
        )
        assert submitted.view.status is OrderStatus.CONFIRMED

        # A brand-new process (new engine, new session factory) sees the same state.
        fresh = Database(resolve_database_url(), pool_size=1, max_overflow=1)
        try:
            settings = Settings(
                database_url=resolve_database_url(),
                environment="test",
                log_level="WARNING",
                log_json=False,
            )
            reader = build_use_cases(
                settings=settings, clock=SystemClock(), scope=fresh.new_scope()
            )
            read_back = await reader.get_order(
                GetOrderQuery(
                    tenant_id=TenantId(tenant_id),
                    context=context_for(tenant_id),
                    order_id=submitted.view.id,
                )
            )
        finally:
            await fresh.dispose()

        assert read_back.id == submitted.view.id
        assert read_back.status is OrderStatus.CONFIRMED
        assert read_back.customer_reference == "PO-PERSIST"
        assert read_back.total_minor == submitted.view.total_minor
        assert read_back.reservation is not None
        assert await count(admin_engine, "orders", id=submitted.view.id) == 1
        assert await count(admin_engine, "order_lines", order_id=submitted.view.id) == 1

    async def test_an_idempotency_record_is_committed_with_the_order(
        self, use_cases: object, tenant_id: str, admin_engine: AsyncEngine
    ) -> None:
        _, sku_id = await provision_catalog(use_cases, tenant_id, slug="idem-co", code="IDM-1")
        await provision_warehouse(use_cases, tenant_id, quantity=4, sku_id=sku_id)

        await use_cases.create_order(  # type: ignore[attr-defined]
            CreateOrderCommand(
                tenant_id=TenantId(tenant_id),
                context=context_for(tenant_id, idempotency_key="stored-key"),
                lines=(OrderLineRequest(sku_id=sku_id, quantity=1),),
            )
        )

        record = await rows(
            admin_engine,
            "SELECT operation, state, resource_type, response_status FROM idempotency_records"
            " WHERE tenant_id = :tenant AND idempotency_key = :key",
            tenant=tenant_id,
            key="stored-key",
        )
        assert len(record) == 1
        assert record[0]["operation"] == "ordering.create_order"
        # Stored as the enum *value*; "completed" is IdempotencyState.COMPLETED.
        assert record[0]["state"] == IdempotencyState.COMPLETED.value
        assert record[0]["resource_type"] == "order"
        assert record[0]["response_status"] == 201

    async def test_orders_are_paged_newest_first(self, use_cases: object, tenant_id: str) -> None:
        _, sku_id = await provision_catalog(use_cases, tenant_id, slug="page-co", code="PGE-1")
        await provision_warehouse(use_cases, tenant_id, quantity=5, sku_id=sku_id)

        for index in range(3):
            await use_cases.create_order(  # type: ignore[attr-defined]
                CreateOrderCommand(
                    tenant_id=TenantId(tenant_id),
                    context=context_for(tenant_id, idempotency_key=f"page-order-{index}"),
                    lines=(OrderLineRequest(sku_id=sku_id, quantity=1),),
                    customer_reference=f"PO-{index}",
                )
            )

        page = await use_cases.list_orders(  # type: ignore[attr-defined]
            ListOrdersQuery(tenant_id=TenantId(tenant_id), context=context_for(tenant_id), limit=2)
        )
        assert len(page.items) == 2
        assert page.next_cursor is not None
        assert decode_cursor(page.next_cursor) is not None

        second = await use_cases.list_orders(  # type: ignore[attr-defined]
            ListOrdersQuery(
                tenant_id=TenantId(tenant_id),
                context=context_for(tenant_id),
                limit=2,
                cursor=page.next_cursor,
            )
        )
        assert {order.id for order in second.items}.isdisjoint({order.id for order in page.items})
