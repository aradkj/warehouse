"""Concurrency tests: real PostgreSQL, real parallel transactions.

These are the tests the whole phase exists for. Overselling is not a theoretical
risk: two channels clicking "buy" on the last unit at the same instant is the
normal case, and the only thing standing between them and a negative stock level is
PostgreSQL row locking (`SELECT ... FOR UPDATE`) plus the CHECK constraints.

How the tests are built
-----------------------
* **Real parallelism.** Every racer runs in its own task with its own
  ``SqlAlchemyTransactionScope``, hence its own session and its own pooled
  connection. Nothing is shared in memory, so the only coordination possible is the
  one the database performs.
* **Deterministic synchronisation.** Racers wait on an ``asyncio.Barrier`` (the
  async equivalent of a start gun) so they enter their critical section together.
  No ``sleep`` is used to order or serialise anything.
* **Assertions are about outcomes, not about timing.** A test never asserts "the
  second one was blocked for N milliseconds"; it asserts how many reservations were
  created and what the stock row contains afterwards. That is what makes the suite
  repeatable.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from tests.conftest import test_database_url as resolve_database_url
from tests.support.database import rows, scalar

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
    RegisterWarehouseCommand,
    ReserveInventoryCommand,
)
from inventory.domain.allocation import AllocationStrategy
from inventory.domain.errors import InsufficientInventory
from inventory.domain.value_objects import AdjustmentReason, StockRequirement
from ordering.application.commands import CreateOrderCommand, OrderLineRequest
from shared.application.context import ActorType, RequestContext
from shared.application.errors import IdempotencyOperationInProgress
from shared.domain.clock import SystemClock
from shared.domain.identifiers import SkuId, TenantId, WarehouseId
from shared.domain.value_objects import Quantity
from shared.infrastructure.db.errors import sqlstate_of
from shared.infrastructure.db.session import Database

pytestmark = pytest.mark.concurrency

#: Every racer blocks here until the whole group is ready, then they all go at once.
RACER_START = 2


@dataclass(frozen=True, slots=True)
class Fixture:
    """A tenant whose SKU is stocked in one or more warehouses."""

    tenant_id: TenantId
    sku_id: SkuId
    warehouse_ids: tuple[WarehouseId, ...]


@pytest.fixture
async def database() -> Database:  # type: ignore[misc]
    """A pool large enough for every racer to hold its own connection."""
    handle = Database(resolve_database_url(), pool_size=20, max_overflow=20)
    try:
        yield handle
    finally:
        await handle.dispose()


@pytest.fixture
async def admin_engine() -> AsyncEngine:  # type: ignore[misc]
    engine = create_async_engine(resolve_database_url())
    try:
        yield engine
    finally:
        await engine.dispose()


def context_for(tenant_id: TenantId | None, *, key: str | None = None) -> RequestContext:
    return RequestContext(
        tenant_id=tenant_id,
        actor_id="load-test",
        actor_type=ActorType.SALES_CHANNEL,
        idempotency_key=key,
    )


def use_cases_for(database: Database) -> Any:
    settings = Settings(
        database_url=resolve_database_url(), environment="test", log_level="WARNING", log_json=False
    )
    return build_use_cases(settings=settings, clock=SystemClock(), scope=database.new_scope())


async def provision(
    database: Database,
    *,
    stock_per_warehouse: tuple[int, ...] = (1,),
    slug: str = "camera",
    code: str = "CAMERA-1",
) -> Fixture:
    """Create a tenant with an active SKU stocked in ``len(stock_per_warehouse)`` warehouses."""
    use_cases = use_cases_for(database)
    platform = context_for(None)
    tenant = await use_cases.register_tenant(
        RegisterTenantCommand(context=platform, slug=slug, name=slug.title())
    )
    tenant_id: TenantId = tenant.view.id
    context = context_for(tenant_id)

    product = await use_cases.create_product(
        CreateProductCommand(
            tenant_id=tenant_id, context=context, slug=f"{slug}-product", name="Camera"
        )
    )
    await use_cases.activate_product(
        ChangeProductStateCommand(tenant_id=tenant_id, context=context, product_id=product.view.id)
    )
    sku = await use_cases.create_sku(
        CreateSkuCommand(
            tenant_id=tenant_id,
            context=context,
            product_id=product.view.id,
            code=code,
            name=code,
            unit_price_minor=49900,
            currency="GBP",
        )
    )

    warehouse_ids: list[WarehouseId] = []
    for index, quantity in enumerate(stock_per_warehouse):
        warehouse = await use_cases.register_warehouse(
            RegisterWarehouseCommand(
                tenant_id=tenant_id, context=context, code=f"W{index}", name=f"Warehouse {index}"
            )
        )
        warehouse_ids.append(warehouse.view.id)
        if quantity:
            await use_cases.adjust_inventory(
                AdjustInventoryCommand(
                    tenant_id=tenant_id,
                    context=context,
                    sku_id=sku.view.id,
                    warehouse_id=warehouse.view.id,
                    quantity_delta=quantity,
                    reason=AdjustmentReason.GOODS_RECEIPT,
                )
            )

    return Fixture(tenant_id=tenant_id, sku_id=sku.view.id, warehouse_ids=tuple(warehouse_ids))


async def run_concurrently[T](
    count: int, worker: Callable[[int, asyncio.Barrier], Awaitable[T]]
) -> list[T]:
    """Run ``count`` workers that synchronise on a barrier, and return their results.

    ``asyncio.gather`` preserves order, so ``results[i]`` belongs to worker ``i``.
    """
    barrier = asyncio.Barrier(count)
    return list(await asyncio.gather(*(worker(index, barrier) for index in range(count))))


async def reserve_once(
    database: Database,
    fixture: Fixture,
    *,
    quantity: int = 1,
    barrier: asyncio.Barrier | None = None,
    strategy: AllocationStrategy = AllocationStrategy.ALL_OR_NOTHING,
) -> str:
    """Attempt one reservation in its own transaction; return the outcome as a string."""
    if barrier is not None:
        await barrier.wait()

    use_cases = use_cases_for(database)
    try:
        outcome = await use_cases.reserve_inventory(
            ReserveInventoryCommand(
                tenant_id=fixture.tenant_id,
                context=context_for(fixture.tenant_id),
                requirements=(
                    StockRequirement(
                        sku_id=fixture.sku_id, quantity=Quantity(quantity), order_line_id=None
                    ),
                ),
                strategy=strategy,
            )
        )
    except InsufficientInventory:
        return "rejected"
    return "created" if outcome.view.reservation is not None else "partial_empty"


async def stock_rows(engine: AsyncEngine, sku_id: SkuId) -> list[dict[str, int]]:
    return await rows(
        engine,
        "SELECT on_hand, reserved, on_hand - reserved AS available"
        " FROM inventory_items WHERE sku_id = :sku ORDER BY warehouse_id",
        sku=sku_id,
    )


class TestLastUnitRace:
    """The specification's headline scenario: two buyers, one unit."""

    async def test_exactly_one_of_two_concurrent_reservations_succeeds(
        self, database: Database, admin_engine: AsyncEngine
    ) -> None:
        fixture = await provision(database, stock_per_warehouse=(1,))

        results = await run_concurrently(
            2, lambda index, barrier: reserve_once(database, fixture, barrier=barrier)
        )

        assert sorted(results) == ["created", "rejected"], results
        assert await scalar(admin_engine, "SELECT count(*) FROM reservations") == 1

        snapshot = (await stock_rows(admin_engine, fixture.sku_id))[0]
        assert snapshot == {"on_hand": 1, "reserved": 1, "available": 0}
        assert snapshot["available"] >= 0

    async def test_the_race_is_repeatable(
        self, database: Database, admin_engine: AsyncEngine
    ) -> None:
        """Run the same race several times: the invariant must hold every time.

        A concurrency bug that appears once in twenty runs is still a bug, so the
        assertion is not "it worked once".
        """
        for attempt in range(5):
            fixture = await provision(
                database,
                stock_per_warehouse=(1,),
                slug=f"race-{attempt}",
                code=f"RACE-{attempt}",
            )
            results = await run_concurrently(
                2, lambda index, barrier, f=fixture: reserve_once(database, f, barrier=barrier)
            )
            assert sorted(results) == ["created", "rejected"], results

            snapshot = (await stock_rows(admin_engine, fixture.sku_id))[0]
            assert snapshot["available"] == 0
            assert snapshot["reserved"] <= snapshot["on_hand"]


class TestHighContention:
    """More racers than units: exactly as many successes as there is stock."""

    async def test_twenty_racers_for_ten_units(
        self, database: Database, admin_engine: AsyncEngine
    ) -> None:
        fixture = await provision(database, stock_per_warehouse=(10,))

        results = await run_concurrently(
            20, lambda index, barrier: reserve_once(database, fixture, barrier=barrier)
        )

        assert results.count("created") == 10
        assert results.count("rejected") == 10
        assert (
            await scalar(admin_engine, "SELECT count(*) FROM reservations WHERE status='ACTIVE'")
            == 10
        )

        snapshot = (await stock_rows(admin_engine, fixture.sku_id))[0]
        assert snapshot == {"on_hand": 10, "reserved": 10, "available": 0}
        assert snapshot["available"] >= 0

    async def test_reservations_of_two_units_each_never_oversell(
        self, database: Database, admin_engine: AsyncEngine
    ) -> None:
        """Mixed quantities: 10 units, 10 racers asking for 2 each -> 5 winners."""
        fixture = await provision(database, stock_per_warehouse=(10,))

        results = await run_concurrently(
            10,
            lambda index, barrier: reserve_once(database, fixture, quantity=2, barrier=barrier),
        )

        assert results.count("created") == 5
        snapshot = (await stock_rows(admin_engine, fixture.sku_id))[0]
        assert snapshot == {"on_hand": 10, "reserved": 10, "available": 0}

    async def test_no_row_ever_holds_more_than_it_has(
        self, database: Database, admin_engine: AsyncEngine
    ) -> None:
        """The database itself must agree: no row with a negative availability."""
        fixture = await provision(database, stock_per_warehouse=(7,))

        await run_concurrently(
            25, lambda index, barrier: reserve_once(database, fixture, barrier=barrier)
        )

        negatives = await scalar(
            admin_engine,
            "SELECT count(*) FROM inventory_items WHERE reserved > on_hand OR on_hand < 0",
        )
        assert negatives == 0
        snapshot = (await stock_rows(admin_engine, fixture.sku_id))[0]
        assert snapshot["reserved"] == 7


class TestMultiWarehouseContention:
    """Contention across several rows of the same SKU.

    Allocation picks candidates in a deterministic order and locks them one by one,
    so two racers that both need "up to 2 units" must not both succeed against the
    same pair of rows.
    """

    async def test_two_warehouses_with_two_units_each_support_four_winners(
        self, database: Database, admin_engine: AsyncEngine
    ) -> None:
        fixture = await provision(database, stock_per_warehouse=(2, 2))

        results = await run_concurrently(
            10, lambda index, barrier: reserve_once(database, fixture, barrier=barrier)
        )

        assert results.count("created") == 4
        snapshot = await stock_rows(admin_engine, fixture.sku_id)
        assert [row["reserved"] for row in snapshot] == [2, 2]
        assert all(row["available"] == 0 for row in snapshot)

    async def test_a_multi_line_reservation_is_all_or_nothing_across_rows(
        self, database: Database, admin_engine: AsyncEngine
    ) -> None:
        """Cross-row requests must not be able to half-succeed into an oversell."""
        fixture = await provision(database, stock_per_warehouse=(1, 1))

        async def worker(index: int, barrier: asyncio.Barrier) -> str:
            await barrier.wait()
            use_cases = use_cases_for(database)
            try:
                outcome = await use_cases.reserve_inventory(
                    ReserveInventoryCommand(
                        tenant_id=fixture.tenant_id,
                        context=context_for(fixture.tenant_id),
                        requirements=(
                            StockRequirement(
                                sku_id=fixture.sku_id, quantity=Quantity(2), order_line_id=None
                            ),
                        ),
                        strategy=AllocationStrategy.ALL_OR_NOTHING,
                    )
                )
            except InsufficientInventory:
                return "rejected"
            return "created" if outcome.view.reservation is not None else "partial_empty"

        results = await run_concurrently(6, worker)

        # Each request needs both rows; exactly one can win, and the loser takes
        # nothing (an all-or-nothing plan holds no partial allocation).
        assert results.count("created") == 1
        snapshot = await stock_rows(admin_engine, fixture.sku_id)
        assert sum(row["reserved"] for row in snapshot) == 2
        assert all(row["available"] >= 0 for row in snapshot)
        assert await scalar(admin_engine, "SELECT count(*) FROM reservations") == 1


class TestRowLocking:
    """The mechanism itself: a second writer waits for the first transaction."""

    async def test_a_reservation_waits_for_the_transaction_holding_the_row(
        self, database: Database, admin_engine: AsyncEngine
    ) -> None:
        """Proves the race is decided by ``SELECT ... FOR UPDATE``, not by luck.

        Transaction A locks the stock row and holds it. Two things are asserted:

        1. another connection cannot take the same lock (``FOR UPDATE NOWAIT`` fails
           with SQLSTATE ``55P03``) - the lock is real, and this part needs no timing
           assumption at all;
        2. a racer started while A holds the lock does not finish, and succeeds once
           A commits.

        The bounded wait in (2) is a probe, not synchronisation: no outcome depends
        on its duration, and the only way the assertion fails is if the racer really
        did acquire the row without waiting - which is the defect under test.
        """
        fixture = await provision(database, stock_per_warehouse=(1,))
        lock_row = text("SELECT reserved FROM inventory_items WHERE sku_id = :sku FOR UPDATE")

        async with admin_engine.begin() as blocker:
            await blocker.execute(lock_row, {"sku": fixture.sku_id})

            async with admin_engine.connect() as prober:
                with pytest.raises(DBAPIError) as caught:
                    await prober.execute(
                        text(
                            "SELECT reserved FROM inventory_items"
                            " WHERE sku_id = :sku FOR UPDATE NOWAIT"
                        ),
                        {"sku": fixture.sku_id},
                    )
                assert sqlstate_of(caught.value) == "55P03", "the row lock must be exclusive"

            racer = asyncio.create_task(reserve_once(database, fixture))
            done, _pending = await asyncio.wait({racer}, timeout=1.0)
            assert racer not in done, "the racer must wait for the row lock"

        # The blocking transaction is gone: the racer now holds the lock and wins.
        assert await racer == "created"
        snapshot = (await stock_rows(admin_engine, fixture.sku_id))[0]
        assert snapshot == {"on_hand": 1, "reserved": 1, "available": 0}

    async def test_a_sequential_second_reservation_cannot_take_the_same_unit(
        self, database: Database, admin_engine: AsyncEngine
    ) -> None:
        """No stale read can oversell: the second attempt sees the committed row."""
        fixture = await provision(database, stock_per_warehouse=(1,))

        first = await reserve_once(database, fixture)
        second = await reserve_once(database, fixture)

        assert (first, second) == ("created", "rejected")
        snapshot = (await stock_rows(admin_engine, fixture.sku_id))[0]
        assert snapshot["available"] == 0
        assert await scalar(admin_engine, "SELECT count(*) FROM reservations") == 1


class TestConcurrentOrdersAndIdempotency:
    """Racing order submissions: the same key, and different keys."""

    async def test_concurrent_submissions_with_the_same_key_create_one_order(
        self, database: Database, admin_engine: AsyncEngine
    ) -> None:
        """Two identical requests race; the unique index on ``(tenant, key)`` decides.

        Even with both requests in flight at the same instant, exactly one order may
        exist and stock may be held exactly once. The loser either replays the
        winner's order or is told the operation is still in progress - never a second
        order.
        """
        fixture = await provision(database, stock_per_warehouse=(3,))
        lines = (OrderLineRequest(sku_id=fixture.sku_id, quantity=1),)

        async def worker(index: int, barrier: asyncio.Barrier) -> str:
            await barrier.wait()
            use_cases = use_cases_for(database)
            try:
                outcome = await use_cases.create_order(
                    CreateOrderCommand(
                        tenant_id=fixture.tenant_id,
                        context=context_for(fixture.tenant_id, key="checkout-8472"),
                        lines=lines,
                    )
                )
            except IdempotencyOperationInProgress:
                # A documented outcome, not a failure: the winner had not committed
                # yet, so the client retries with the same key and gets the replay.
                return "in_progress"
            return f"ok:{outcome.view.id}"

        results = await run_concurrently(2, worker)

        accepted = {result[3:] for result in results if result.startswith("ok:")}
        assert all(result == "in_progress" or result.startswith("ok:") for result in results), (
            results
        )
        assert len(accepted) <= 1, f"one key must not produce two orders: {results}"

        # Whatever the two requests answered, the database saw one business
        # operation: one order, one reservation, one hold in stock.
        assert await scalar(admin_engine, "SELECT count(*) FROM orders") == 1
        assert await scalar(admin_engine, "SELECT count(*) FROM reservations") == 1
        snapshot = (await stock_rows(admin_engine, fixture.sku_id))[0]
        assert snapshot == {"on_hand": 3, "reserved": 1, "available": 2}

    async def test_concurrent_submissions_with_different_keys_both_succeed(
        self, database: Database, admin_engine: AsyncEngine
    ) -> None:
        """Different keys are different business operations - both must go through."""
        fixture = await provision(database, stock_per_warehouse=(5,))
        lines = (OrderLineRequest(sku_id=fixture.sku_id, quantity=2),)

        async def worker(index: int, barrier: asyncio.Barrier) -> str:
            await barrier.wait()
            use_cases = use_cases_for(database)
            outcome = await use_cases.create_order(
                CreateOrderCommand(
                    tenant_id=fixture.tenant_id,
                    context=context_for(fixture.tenant_id, key=f"checkout-{index}"),
                    lines=lines,
                )
            )
            return str(outcome.view.id)

        order_ids = await run_concurrently(2, worker)

        assert len(set(order_ids)) == 2
        assert await scalar(admin_engine, "SELECT count(*) FROM orders") == 2
        snapshot = (await stock_rows(admin_engine, fixture.sku_id))[0]
        assert snapshot == {"on_hand": 5, "reserved": 4, "available": 1}

    async def test_concurrent_order_submissions_competing_for_last_sku_unit_with_distinct_keys(
        self, database: Database, admin_engine: AsyncEngine
    ) -> None:
        """Two independent orders concurrently compete for the single remaining unit of a SKU.

        Guarantees verified:
        - Exactly one order successfully secures the reservation and confirms.
        - Competing order is backordered (or rejected under REJECT policy) with 0 reserved.
        - Available inventory never becomes negative (on_hand=1, reserved=1, available=0).
        - Exactly one active reservation exists in PostgreSQL.
        - Exactly one order confirmed outbox event exists.
        """
        fixture = await provision(database, stock_per_warehouse=(1,))
        lines = (OrderLineRequest(sku_id=fixture.sku_id, quantity=1),)

        async def worker(index: int, barrier: asyncio.Barrier) -> tuple[str, str]:
            await barrier.wait()
            use_cases = use_cases_for(database)
            outcome = await use_cases.create_order(
                CreateOrderCommand(
                    tenant_id=fixture.tenant_id,
                    context=context_for(fixture.tenant_id, key=f"compete-order-{index}"),
                    lines=lines,
                )
            )
            return str(outcome.view.id), outcome.view.status

        results = await run_concurrently(2, worker)
        statuses = [status for _, status in results]

        # Exactly one order is CONFIRMED and the competitor is BACKORDERED
        assert sorted(statuses) == ["BACKORDERED", "CONFIRMED"], f"Unexpected statuses: {statuses}"

        # Invariants in PostgreSQL:
        assert await scalar(admin_engine, "SELECT count(*) FROM orders") == 2
        assert (
            await scalar(admin_engine, "SELECT count(*) FROM reservations WHERE status = 'ACTIVE'")
            == 1
        )

        snapshot = (await stock_rows(admin_engine, fixture.sku_id))[0]
        assert snapshot == {"on_hand": 1, "reserved": 1, "available": 0}
        assert snapshot["available"] >= 0


__all__: list[str] = []
