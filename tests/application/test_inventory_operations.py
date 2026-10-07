"""Application tests: inventory use cases (adjust, reserve, release, confirm).

The invariants asserted here are the ones that protect against overselling:
``available`` never goes negative, a released or expired hold can never be
confirmed, and a held unit is never handed out twice.
"""

from __future__ import annotations

import pytest
from tests.application.conftest import Shop, _build_shop

from inventory.application.commands import (
    AdjustInventoryCommand,
    ConfirmReservationCommand,
    GetAvailabilityQuery,
    ReleaseReservationCommand,
    ReserveInventoryCommand,
)
from inventory.domain.allocation import AllocationStrategy
from inventory.domain.errors import (
    InsufficientInventory,
    InvalidReservationTransition,
    ReservationExpired,
    StockAdjustmentBelowReserved,
)
from inventory.domain.value_objects import (
    AdjustmentReason,
    ReservationStatus,
    StockRequirement,
)
from shared.application.errors import InvalidRequest, NotFound
from shared.domain.errors import InvariantViolation
from shared.domain.identifiers import SkuId, WarehouseId, new_id
from shared.domain.value_objects import Quantity

pytestmark = pytest.mark.application


def requirement(sku_id: SkuId, quantity: int) -> StockRequirement:
    return StockRequirement(sku_id=sku_id, quantity=Quantity(quantity))


def reserve_command(
    shop: Shop,
    quantity: int = 1,
    *,
    strategy: AllocationStrategy = AllocationStrategy.ALL_OR_NOTHING,
    ttl_seconds: int | None = None,
    sku_id: SkuId | None = None,
    warehouse_ids: tuple[WarehouseId, ...] | None = None,
) -> ReserveInventoryCommand:
    return ReserveInventoryCommand(
        tenant_id=shop.tenant_id,
        context=shop.context,
        requirements=(requirement(sku_id or shop.sku_id, quantity),),
        strategy=strategy,
        ttl_seconds=ttl_seconds,
        warehouse_ids=warehouse_ids,
    )


class TestStockAdjustments:
    async def test_receiving_goods_creates_the_stock_row_and_a_movement(self, shop: Shop) -> None:
        outcome = await shop.app.use_cases.adjust_inventory(
            AdjustInventoryCommand(
                tenant_id=shop.tenant_id,
                context=shop.context,
                sku_id=shop.sku_id,
                warehouse_id=shop.warehouse_id,
                quantity_delta=7,
                reason=AdjustmentReason.GOODS_RECEIPT,
            )
        )

        assert outcome.view.item.on_hand == 7
        assert outcome.view.item.available == 7
        assert outcome.view.movement.quantity_delta == 7
        assert outcome.view.movement.on_hand_after == 7
        assert outcome.view.movement.reason is AdjustmentReason.GOODS_RECEIPT

    async def test_a_zero_delta_is_refused(self, shop: Shop) -> None:
        with pytest.raises(InvalidRequest):
            await shop.app.use_cases.adjust_inventory(
                AdjustInventoryCommand(
                    tenant_id=shop.tenant_id,
                    context=shop.context,
                    sku_id=shop.sku_id,
                    warehouse_id=shop.warehouse_id,
                    quantity_delta=0,
                    reason=AdjustmentReason.CYCLE_COUNT,
                )
            )

    async def test_a_write_off_below_zero_is_refused(self, shop: Shop) -> None:
        await shop.app.use_cases.adjust_inventory(
            AdjustInventoryCommand(
                tenant_id=shop.tenant_id,
                context=shop.context,
                sku_id=shop.sku_id,
                warehouse_id=shop.warehouse_id,
                quantity_delta=2,
                reason=AdjustmentReason.GOODS_RECEIPT,
            )
        )
        with pytest.raises(InvariantViolation):
            await shop.app.use_cases.adjust_inventory(
                AdjustInventoryCommand(
                    tenant_id=shop.tenant_id,
                    context=shop.context,
                    sku_id=shop.sku_id,
                    warehouse_id=shop.warehouse_id,
                    quantity_delta=-3,
                    reason=AdjustmentReason.DAMAGE,
                )
            )
        assert shop.stock_of() == (2, 0, 2)

    async def test_reserved_stock_cannot_be_written_off(self, stocked_shop: Shop) -> None:
        reserved = await stocked_shop.app.use_cases.reserve_inventory(
            reserve_command(stocked_shop, quantity=6)
        )
        assert reserved.view.is_complete

        with pytest.raises(StockAdjustmentBelowReserved):
            await stocked_shop.app.use_cases.adjust_inventory(
                AdjustInventoryCommand(
                    tenant_id=stocked_shop.tenant_id,
                    context=stocked_shop.context,
                    sku_id=stocked_shop.sku_id,
                    warehouse_id=stocked_shop.warehouse_id,
                    quantity_delta=-5,
                    reason=AdjustmentReason.DAMAGE,
                )
            )
        assert stocked_shop.stock_of() == (10, 6, 4)

    async def test_adjusting_an_unknown_sku_is_not_found(self, shop: Shop) -> None:
        with pytest.raises(NotFound):
            await shop.app.use_cases.adjust_inventory(
                AdjustInventoryCommand(
                    tenant_id=shop.tenant_id,
                    context=shop.context,
                    sku_id=SkuId(new_id()),
                    warehouse_id=shop.warehouse_id,
                    quantity_delta=1,
                    reason=AdjustmentReason.GOODS_RECEIPT,
                )
            )

    async def test_adjusting_another_tenants_sku_is_not_found(self) -> None:
        first = await _build_shop(on_hand=1)
        second = await _build_shop(on_hand=0)

        with pytest.raises(NotFound):
            await second.app.use_cases.adjust_inventory(
                AdjustInventoryCommand(
                    tenant_id=second.tenant_id,
                    context=second.context,
                    sku_id=SkuId(first.sku_id),
                    warehouse_id=second.warehouse_id,
                    quantity_delta=5,
                    reason=AdjustmentReason.GOODS_RECEIPT,
                )
            )


class TestReservation:
    async def test_a_reservation_holds_stock_across_warehouses(self) -> None:
        shop = await _build_shop(on_hand=10, warehouse_count=2)
        outcome = await shop.app.use_cases.reserve_inventory(reserve_command(shop, quantity=8))

        assert outcome.view.is_complete
        assert outcome.view.reservation is not None
        assert outcome.view.reservation.total_quantity == 8
        warehouses = {
            allocation.warehouse_id for allocation in outcome.view.reservation.allocations
        }
        assert len(warehouses) == 2, "the hold should be split across both warehouses"
        on_hand, reserved, available = shop.stock_of()
        assert (on_hand, reserved, available) == (10, 8, 2)

    async def test_all_or_nothing_holds_nothing_when_stock_is_short(
        self, stocked_shop: Shop
    ) -> None:
        with pytest.raises(InsufficientInventory) as caught:
            await stocked_shop.app.use_cases.reserve_inventory(reserve_command(stocked_shop, 11))

        # ALL_OR_NOTHING holds nothing, so the whole requirement is reported as
        # missing rather than the one unit the shelf happened to lack.
        assert caught.value.shortfalls[0].missing == Quantity(11)
        assert caught.value.shortfalls[0].allocated == Quantity(0)
        # Nothing was held: a failed all-or-nothing attempt leaves no trace.
        assert stocked_shop.stock_of() == (10, 0, 10)
        assert stocked_shop.app.stores.reservations == {}

    async def test_partial_strategy_holds_what_it_can(self, stocked_shop: Shop) -> None:
        outcome = await stocked_shop.app.use_cases.reserve_inventory(
            reserve_command(stocked_shop, 12, strategy=AllocationStrategy.PARTIAL)
        )

        assert outcome.view.is_complete is False
        assert outcome.view.reservation is not None
        assert outcome.view.reservation.total_quantity == 10
        assert outcome.view.shortfalls[0].missing == 2
        assert stocked_shop.stock_of() == (10, 10, 0)

    async def test_partial_strategy_with_no_stock_produces_no_reservation(self, shop: Shop) -> None:
        outcome = await shop.app.use_cases.reserve_inventory(
            reserve_command(shop, 3, strategy=AllocationStrategy.PARTIAL)
        )
        assert outcome.view.reservation is None
        assert outcome.view.is_complete is False
        assert outcome.view.shortfalls[0].missing == 3
        assert shop.app.stores.reservations == {}

    async def test_a_reservation_requires_at_least_one_requirement(self, shop: Shop) -> None:
        command = ReserveInventoryCommand(
            tenant_id=shop.tenant_id, context=shop.context, requirements=()
        )
        with pytest.raises(InvalidRequest):
            await shop.app.use_cases.reserve_inventory(command)

    async def test_the_ttl_is_bounded(self, stocked_shop: Shop) -> None:
        with pytest.raises(InvalidRequest):
            await stocked_shop.app.use_cases.reserve_inventory(
                reserve_command(stocked_shop, 1, ttl_seconds=90_000)
            )

    async def test_stock_never_goes_negative_under_repeated_attempts(
        self, stocked_shop: Shop
    ) -> None:
        # PARTIAL never raises for stock reasons, so every iteration must either
        # hold a unit or come back empty-handed - never oversell.
        for _ in range(12):
            outcome = await stocked_shop.app.use_cases.reserve_inventory(
                reserve_command(stocked_shop, 1, strategy=AllocationStrategy.PARTIAL)
            )
            if outcome.view.reservation is None:
                assert outcome.view.shortfalls[0].missing == 1

        on_hand, reserved, available = stocked_shop.stock_of()
        assert reserved == 10
        assert available == 0
        assert on_hand - reserved >= 0


class TestReleaseAndConfirm:
    async def test_releasing_returns_stock_to_the_pool(self, stocked_shop: Shop) -> None:
        held = await stocked_shop.app.use_cases.reserve_inventory(reserve_command(stocked_shop, 4))
        assert held.view.reservation is not None

        released = await stocked_shop.app.use_cases.release_reservation(
            ReleaseReservationCommand(
                tenant_id=stocked_shop.tenant_id,
                context=stocked_shop.context,
                reservation_id=held.view.reservation.id,
                reason="customer cancelled",
            )
        )

        assert released.view.status is ReservationStatus.RELEASED
        assert released.view.release_reason == "customer cancelled"
        assert stocked_shop.stock_of() == (10, 0, 10)

    async def test_releasing_twice_is_idempotent(self, stocked_shop: Shop) -> None:
        held = await stocked_shop.app.use_cases.reserve_inventory(reserve_command(stocked_shop, 2))
        assert held.view.reservation is not None
        command = ReleaseReservationCommand(
            tenant_id=stocked_shop.tenant_id,
            context=stocked_shop.context,
            reservation_id=held.view.reservation.id,
        )

        first = await stocked_shop.app.use_cases.release_reservation(command)
        second = await stocked_shop.app.use_cases.release_reservation(command)

        assert first.view.status is second.view.status is ReservationStatus.RELEASED
        assert stocked_shop.stock_of() == (10, 0, 10)

    async def test_a_released_hold_can_never_be_confirmed(self, stocked_shop: Shop) -> None:
        held = await stocked_shop.app.use_cases.reserve_inventory(reserve_command(stocked_shop, 1))
        assert held.view.reservation is not None
        await stocked_shop.app.use_cases.release_reservation(
            ReleaseReservationCommand(
                tenant_id=stocked_shop.tenant_id,
                context=stocked_shop.context,
                reservation_id=held.view.reservation.id,
            )
        )

        with pytest.raises(InvalidReservationTransition):
            await stocked_shop.app.use_cases.confirm_reservation(
                ConfirmReservationCommand(
                    tenant_id=stocked_shop.tenant_id,
                    context=stocked_shop.context,
                    reservation_id=held.view.reservation.id,
                )
            )

    async def test_confirming_consumes_the_stock(self, stocked_shop: Shop) -> None:
        held = await stocked_shop.app.use_cases.reserve_inventory(reserve_command(stocked_shop, 3))
        assert held.view.reservation is not None

        confirmed = await stocked_shop.app.use_cases.confirm_reservation(
            ConfirmReservationCommand(
                tenant_id=stocked_shop.tenant_id,
                context=stocked_shop.context,
                reservation_id=held.view.reservation.id,
            )
        )

        assert confirmed.view.status is ReservationStatus.CONFIRMED
        assert stocked_shop.stock_of() == (7, 0, 7)

    async def test_confirming_twice_is_refused(self, stocked_shop: Shop) -> None:
        held = await stocked_shop.app.use_cases.reserve_inventory(reserve_command(stocked_shop, 3))
        assert held.view.reservation is not None
        command = ConfirmReservationCommand(
            tenant_id=stocked_shop.tenant_id,
            context=stocked_shop.context,
            reservation_id=held.view.reservation.id,
        )
        await stocked_shop.app.use_cases.confirm_reservation(command)

        with pytest.raises(InvalidReservationTransition):
            await stocked_shop.app.use_cases.confirm_reservation(command)

    async def test_an_expired_hold_cannot_be_confirmed(self, stocked_shop: Shop) -> None:
        held = await stocked_shop.app.use_cases.reserve_inventory(
            reserve_command(stocked_shop, 2, ttl_seconds=60)
        )
        assert held.view.reservation is not None

        stocked_shop.app.clock.advance(120)

        with pytest.raises(ReservationExpired):
            await stocked_shop.app.use_cases.confirm_reservation(
                ConfirmReservationCommand(
                    tenant_id=stocked_shop.tenant_id,
                    context=stocked_shop.context,
                    reservation_id=held.view.reservation.id,
                )
            )
        # The units are still held (Phase 1 has no sweeper - that job belongs to
        # the Phase 2 worker), but they can no longer be consumed.
        assert stocked_shop.stock_of() == (10, 2, 8)

    async def test_an_unknown_reservation_is_not_found(self, stocked_shop: Shop) -> None:
        from shared.domain.identifiers import ReservationId

        with pytest.raises(NotFound):
            await stocked_shop.app.use_cases.release_reservation(
                ReleaseReservationCommand(
                    tenant_id=stocked_shop.tenant_id,
                    context=stocked_shop.context,
                    reservation_id=ReservationId(new_id()),
                )
            )

    async def test_a_reservation_from_another_tenant_is_not_found(self) -> None:
        first = await _build_shop(on_hand=3)
        second = await _build_shop(on_hand=3)
        held = await first.app.use_cases.reserve_inventory(reserve_command(first, 1))
        assert held.view.reservation is not None

        with pytest.raises(NotFound):
            await second.app.use_cases.release_reservation(
                ReleaseReservationCommand(
                    tenant_id=second.tenant_id,
                    context=second.context,
                    reservation_id=held.view.reservation.id,
                )
            )
        assert first.stock_of() == (3, 1, 2)


class TestAvailabilityQuery:
    async def test_availability_reports_the_aggregates_numbers(self, stocked_shop: Shop) -> None:
        await stocked_shop.app.use_cases.reserve_inventory(reserve_command(stocked_shop, 4))

        page = await stocked_shop.app.use_cases.get_availability(
            GetAvailabilityQuery(
                tenant_id=stocked_shop.tenant_id,
                context=stocked_shop.context,
                limit=10,
                sku_id=stocked_shop.sku_id,
            )
        )

        assert len(page.items) == 1
        item = page.items[0]
        assert (item.on_hand, item.reserved, item.available) == (10, 4, 6)

    async def test_availability_is_tenant_scoped(self) -> None:
        first = await _build_shop(on_hand=5)
        second = await _build_shop(on_hand=1)

        page = await second.app.use_cases.get_availability(
            GetAvailabilityQuery(
                tenant_id=second.tenant_id,
                context=second.context,
                limit=10,
                sku_id=first.sku_id,
            )
        )
        assert page.items == ()
