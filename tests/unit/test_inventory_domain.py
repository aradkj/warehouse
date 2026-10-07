"""Unit tests: the inventory aggregate - the platform's oversell guard.

These tests exercise the invariant directly, without a database: no sequence of
domain operations may drive ``available`` below zero, and no released or expired
hold may ever be confirmed. They are the fast feedback loop that the concurrency
suite then re-proves under real parallel transactions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from inventory.domain.allocation import AllocationStrategy, InventoryAllocationPolicy
from inventory.domain.errors import (
    InsufficientInventory,
    InvalidAdjustment,
    InvalidReservation,
    InvalidReservationTransition,
    ReservationExpired,
    StockAdjustmentBelowReserved,
)
from inventory.domain.inventory_item import InventoryItem
from inventory.domain.reservation import Reservation
from inventory.domain.value_objects import (
    AdjustmentReason,
    AllocationCandidate,
    MovementType,
    ReservationAllocation,
    ReservationStatus,
    StockRequirement,
)
from inventory.domain.warehouse import Warehouse
from shared.domain.errors import InvariantViolation
from shared.domain.identifiers import (
    InventoryItemId,
    OrderId,
    ReservationId,
    SkuId,
    TenantId,
    WarehouseId,
    new_id,
)
from shared.domain.value_objects import Quantity

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
TENANT = TenantId(new_id())


def item(*, warehouse_id: WarehouseId | None = None, sku_id: SkuId | None = None) -> InventoryItem:
    return InventoryItem.register(
        tenant_id=TENANT,
        warehouse_id=warehouse_id or WarehouseId(new_id()),
        sku_id=sku_id or SkuId(new_id()),
        now=NOW,
    )


def stocked(quantity: int, **kwargs: object) -> InventoryItem:
    aggregate = item(**kwargs)  # type: ignore[arg-type]
    aggregate.adjust(delta=quantity, reason=AdjustmentReason.GOODS_RECEIPT, now=NOW)
    return aggregate


def reservation_id() -> ReservationId:
    return ReservationId(new_id())


class TestInventoryItemAdjustments:
    def test_a_new_item_starts_empty(self) -> None:
        aggregate = item()
        assert aggregate.on_hand == Quantity(0)
        assert aggregate.reserved == Quantity(0)
        assert aggregate.available == Quantity(0)

    def test_receipt_increases_on_hand_and_writes_a_ledger_entry(self) -> None:
        aggregate = item()
        movement = aggregate.adjust(
            delta=10, reason=AdjustmentReason.GOODS_RECEIPT, now=NOW, note="PO-1"
        )
        assert aggregate.on_hand == Quantity(10)
        assert aggregate.available == Quantity(10)
        assert movement.movement_type is MovementType.RECEIPT
        assert movement.quantity_delta == 10
        assert movement.on_hand_after == 10
        assert movement.reserved_after == 0

    def test_zero_adjustment_is_refused(self) -> None:
        with pytest.raises(InvalidAdjustment):
            item().adjust(delta=0, reason=AdjustmentReason.MANUAL_CORRECTION, now=NOW)

    def test_write_off_may_not_make_on_hand_negative(self) -> None:
        aggregate = stocked(3)
        with pytest.raises(InvariantViolation):
            aggregate.adjust(delta=-4, reason=AdjustmentReason.DAMAGE, now=NOW)
        assert aggregate.on_hand == Quantity(3)

    def test_write_off_below_reserved_is_refused(self) -> None:
        aggregate = stocked(5)
        hold = reservation_id()
        aggregate.reserve(quantity=Quantity(4), reservation_id=hold, now=NOW)

        with pytest.raises(StockAdjustmentBelowReserved):
            aggregate.adjust(delta=-2, reason=AdjustmentReason.DAMAGE, now=NOW)
        assert aggregate.on_hand == Quantity(5)
        assert aggregate.reserved == Quantity(4)


class TestInventoryItemReservations:
    def test_reserving_reduces_availability_but_not_physical_stock(self) -> None:
        aggregate = stocked(5)
        aggregate.reserve(quantity=Quantity(2), reservation_id=reservation_id(), now=NOW)
        assert aggregate.on_hand == Quantity(5)
        assert aggregate.reserved == Quantity(2)
        assert aggregate.available == Quantity(3)

    def test_reserving_more_than_available_is_impossible(self) -> None:
        aggregate = stocked(1)
        with pytest.raises(InsufficientInventory) as caught:
            aggregate.reserve(quantity=Quantity(2), reservation_id=reservation_id(), now=NOW)
        assert caught.value.code == "insufficient_inventory"
        assert aggregate.reserved == Quantity(0)
        assert aggregate.available == Quantity(1)

    def test_zero_quantity_reservation_is_refused(self) -> None:
        with pytest.raises(InvalidAdjustment):
            stocked(1).reserve(quantity=Quantity(0), reservation_id=reservation_id(), now=NOW)

    def test_releasing_returns_the_stock(self) -> None:
        aggregate = stocked(5)
        hold = reservation_id()
        aggregate.reserve(quantity=Quantity(3), reservation_id=hold, now=NOW)
        movement = aggregate.release(
            quantity=Quantity(3), reservation_id=hold, reason="cancelled", now=NOW
        )
        assert aggregate.reserved == Quantity(0)
        assert aggregate.available == Quantity(5)
        assert movement.movement_type is MovementType.RESERVATION_RELEASE
        assert movement.quantity_delta == -3

    def test_releasing_more_than_reserved_is_refused(self) -> None:
        aggregate = stocked(5)
        aggregate.reserve(quantity=Quantity(1), reservation_id=reservation_id(), now=NOW)
        with pytest.raises(InvariantViolation):
            aggregate.release(
                quantity=Quantity(2), reservation_id=reservation_id(), reason="oops", now=NOW
            )

    def test_confirming_consumes_stock_from_both_counters(self) -> None:
        aggregate = stocked(5)
        hold = reservation_id()
        aggregate.reserve(quantity=Quantity(2), reservation_id=hold, now=NOW)
        movement = aggregate.commit_reservation(quantity=Quantity(2), reservation_id=hold, now=NOW)
        assert aggregate.on_hand == Quantity(3)
        assert aggregate.reserved == Quantity(0)
        assert aggregate.available == Quantity(3)
        assert movement.movement_type is MovementType.RESERVATION_CONFIRMATION

    def test_the_available_invariant_holds_through_a_mixed_sequence(self) -> None:
        aggregate = item()
        aggregate.adjust(delta=10, reason=AdjustmentReason.GOODS_RECEIPT, now=NOW)
        first, second = reservation_id(), reservation_id()
        aggregate.reserve(quantity=Quantity(6), reservation_id=first, now=NOW)
        aggregate.release(quantity=Quantity(2), reservation_id=first, reason="partial", now=NOW)
        aggregate.reserve(quantity=Quantity(4), reservation_id=second, now=NOW)
        aggregate.commit_reservation(quantity=Quantity(4), reservation_id=second, now=NOW)
        assert aggregate.on_hand == Quantity(6)
        assert aggregate.reserved == Quantity(4)
        assert aggregate.available == Quantity(6 - 4)
        assert aggregate.available.value >= 0

    def test_every_mutation_produces_exactly_one_movement(self) -> None:
        aggregate = stocked(5)
        hold = reservation_id()
        aggregate.reserve(quantity=Quantity(2), reservation_id=hold, now=NOW)
        aggregate.commit_reservation(quantity=Quantity(2), reservation_id=hold, now=NOW)
        movements = aggregate.pull_movements()
        assert [movement.movement_type for movement in movements] == [
            MovementType.RECEIPT,
            MovementType.RESERVATION,
            MovementType.RESERVATION_CONFIRMATION,
        ]
        assert aggregate.pull_movements() == ()

    def test_events_describe_every_stock_change(self) -> None:
        aggregate = stocked(1)
        names = [event.event_name for event in aggregate.events]
        assert names == ["InventoryStockAdjusted"]
        assert aggregate.version == 1


class TestReservationLifecycle:
    def allocation(self, aggregate: InventoryItem, quantity: int = 1) -> ReservationAllocation:
        return ReservationAllocation.create(
            inventory_item_id=aggregate.id,
            warehouse_id=aggregate.warehouse_id,
            sku_id=aggregate.sku_id,
            quantity=Quantity(quantity),
            order_line_id=None,
            now=NOW,
        )

    def make(self, aggregate: InventoryItem, *, expires_in: int = 900) -> Reservation:
        return Reservation.create(
            tenant_id=TENANT,
            order_id=None,
            allocations=[self.allocation(aggregate)],
            expires_at=NOW + timedelta(seconds=expires_in),
            now=NOW,
        )

    def test_a_reservation_requires_an_allocation(self) -> None:
        with pytest.raises(InvalidReservation):
            Reservation.create(
                tenant_id=TENANT,
                order_id=None,
                allocations=[],
                expires_at=NOW + timedelta(minutes=5),
                now=NOW,
            )

    def test_expiry_must_be_in_the_future(self) -> None:
        with pytest.raises(InvalidReservation):
            self.make(item(), expires_in=0)

    def test_confirming_an_active_hold_is_allowed_once(self) -> None:
        aggregate = item()
        reservation = self.make(aggregate)
        reservation.confirm(now=NOW)
        assert reservation.status is ReservationStatus.CONFIRMED
        with pytest.raises(InvalidReservationTransition):
            reservation.confirm(now=NOW)

    def test_released_reservations_cannot_be_confirmed(self) -> None:
        reservation = self.make(item())
        reservation.release(reason="cancelled", now=NOW)
        assert reservation.status is ReservationStatus.RELEASED
        with pytest.raises(InvalidReservationTransition):
            reservation.confirm(now=NOW)

    def test_expired_reservations_cannot_be_confirmed(self) -> None:
        reservation = self.make(item(), expires_in=60)
        late = NOW + timedelta(seconds=61)
        with pytest.raises(ReservationExpired):
            reservation.confirm(now=late)
        assert reservation.status is ReservationStatus.ACTIVE

    def test_cannot_expire_before_the_expiry_instant(self) -> None:
        reservation = self.make(item(), expires_in=600)
        with pytest.raises(InvariantViolation):
            reservation.expire(now=NOW)

    def test_expiry_marks_the_hold_and_cannot_be_repeated(self) -> None:
        reservation = self.make(item(), expires_in=60)
        late = NOW + timedelta(seconds=60)
        reservation.expire(now=late)
        assert reservation.status is ReservationStatus.EXPIRED
        with pytest.raises(InvalidReservationTransition):
            reservation.expire(now=late)
        with pytest.raises(InvalidReservationTransition):
            reservation.confirm(now=late)

    def test_release_cannot_follow_confirmation(self) -> None:
        reservation = self.make(item())
        reservation.confirm(now=NOW)
        with pytest.raises(InvalidReservationTransition):
            reservation.release(reason="too late", now=NOW)

    def test_is_active_accounts_for_expiry_without_a_sweeper(self) -> None:
        reservation = self.make(item(), expires_in=60)
        assert reservation.is_active(now=NOW)
        assert not reservation.is_active(now=NOW + timedelta(seconds=61))
        assert reservation.is_expired_at(NOW + timedelta(seconds=61))

    def test_allocations_are_grouped_per_order_line(self) -> None:
        first, second = item(), item()
        line_id = new_id()
        reservation = Reservation.create(
            tenant_id=TENANT,
            order_id=OrderId(new_id()),
            allocations=[
                ReservationAllocation.create(
                    inventory_item_id=first.id,
                    warehouse_id=first.warehouse_id,
                    sku_id=first.sku_id,
                    quantity=Quantity(2),
                    order_line_id=line_id,
                    now=NOW,
                ),
                ReservationAllocation.create(
                    inventory_item_id=second.id,
                    warehouse_id=second.warehouse_id,
                    sku_id=second.sku_id,
                    quantity=Quantity(3),
                    order_line_id=line_id,
                    now=NOW,
                ),
            ],
            expires_at=NOW + timedelta(minutes=15),
            now=NOW,
        )
        assert reservation.total_quantity() == Quantity(5)
        assert reservation.covered_lines() == {line_id: 5}
        assert reservation.quantities_by_item()[first.id] == Quantity(2)
        assert set(reservation.warehouse_ids()) == {first.warehouse_id, second.warehouse_id}


class TestWarehouseLifecycle:
    def make(self) -> Warehouse:
        return Warehouse.register(tenant_id=TENANT, code="lon1", name="London DC", now=NOW)

    def test_registration_normalises_the_code(self) -> None:
        warehouse = self.make()
        assert warehouse.code == "LON1"
        assert warehouse.is_active

    def test_deactivate_and_reactivate(self) -> None:
        warehouse = self.make()
        warehouse.deactivate(now=NOW)
        assert not warehouse.is_active
        warehouse.activate(now=NOW)
        assert warehouse.is_active

    def test_double_deactivation_is_refused(self) -> None:
        from inventory.domain.errors import InvalidWarehouseTransition

        warehouse = self.make()
        warehouse.deactivate(now=NOW)
        with pytest.raises(InvalidWarehouseTransition):
            warehouse.deactivate(now=NOW)

    def test_rename_requires_a_name(self) -> None:
        from shared.domain.errors import InvalidValue

        with pytest.raises(InvalidValue):
            self.make().rename(name="  ", now=NOW)


class TestAllocationPolicy:
    """The policy chooses *where* stock comes from; the strategy chooses *whether*.

    Semantics that the rest of the platform relies on:

    * under ``ALL_OR_NOTHING`` a shortfall means "hold nothing";
    * under ``PARTIAL`` the policy holds whatever is available on each line and
      reports the remaining shortfall, which is what lets an order be accepted as
      ``BACKORDERED`` with a real (if incomplete) hold;
    * a candidate is never oversubscribed across the lines of one request.
    """

    @staticmethod
    def candidate(available: int, sku_id: SkuId) -> AllocationCandidate:
        return AllocationCandidate(
            inventory_item_id=InventoryItemId(new_id()),
            warehouse_id=WarehouseId(new_id()),
            sku_id=sku_id,
            available=Quantity(available),
        )

    @staticmethod
    def requirement(sku_id: SkuId, quantity: int) -> StockRequirement:
        return StockRequirement(sku_id=sku_id, quantity=Quantity(quantity))

    def test_splits_across_warehouses(self) -> None:
        sku_id = SkuId(new_id())
        first = self.candidate(2, sku_id)
        second = self.candidate(5, sku_id)
        plan = InventoryAllocationPolicy(AllocationStrategy.ALL_OR_NOTHING).plan(
            requirements=[self.requirement(sku_id, 6)],
            candidates=[second, first],
            now=NOW,
        )
        assert plan.is_complete
        held = plan.quantity_for_item(first.inventory_item_id) + plan.quantity_for_item(
            second.inventory_item_id
        )
        assert held == Quantity(6)

    def test_allocation_is_deterministic_for_the_same_inputs(self) -> None:
        sku_id = SkuId(new_id())
        candidates = [self.candidate(3, sku_id), self.candidate(4, sku_id)]
        policy = InventoryAllocationPolicy(AllocationStrategy.ALL_OR_NOTHING)
        request = [self.requirement(sku_id, 5)]
        first = policy.plan(requirements=request, candidates=candidates, now=NOW)
        second = policy.plan(requirements=request, candidates=candidates, now=NOW)
        assert [(a.inventory_item_id, a.quantity) for a in first.allocations] == [
            (a.inventory_item_id, a.quantity) for a in second.allocations
        ]

    def test_all_or_nothing_holds_nothing_when_a_single_unit_is_missing(self) -> None:
        sku_id = SkuId(new_id())
        plan = InventoryAllocationPolicy(AllocationStrategy.ALL_OR_NOTHING).plan(
            requirements=[self.requirement(sku_id, 3)],
            candidates=[self.candidate(2, sku_id)],
            now=NOW,
        )
        assert plan.allocations == ()
        assert not plan.is_complete
        assert plan.shortfalls[0].required == Quantity(3)
        assert plan.shortfalls[0].allocated == Quantity(0)
        assert plan.shortfalls[0].missing == Quantity(3)

    def test_partial_strategy_holds_what_is_available_and_reports_the_rest(self) -> None:
        sku_id = SkuId(new_id())
        candidate = self.candidate(2, sku_id)
        plan = InventoryAllocationPolicy(AllocationStrategy.PARTIAL).plan(
            requirements=[self.requirement(sku_id, 5)],
            candidates=[candidate],
            now=NOW,
        )
        assert not plan.is_complete
        assert plan.quantity_for_item(candidate.inventory_item_id) == Quantity(2)
        assert plan.shortfalls[0].allocated == Quantity(2)
        assert plan.shortfalls[0].missing == Quantity(3)

    def test_two_lines_of_one_sku_share_the_available_stock(self) -> None:
        sku_id = SkuId(new_id())
        candidate = self.candidate(4, sku_id)
        plan = InventoryAllocationPolicy(AllocationStrategy.PARTIAL).plan(
            requirements=[self.requirement(sku_id, 3), self.requirement(sku_id, 3)],
            candidates=[candidate],
            now=NOW,
        )
        held = plan.quantity_for_item(candidate.inventory_item_id)
        assert held == Quantity(4)  # never more than the warehouse holds
        assert plan.allocations[0].quantity == Quantity(3)
        assert plan.allocations[1].quantity == Quantity(1)
        assert not plan.is_complete

    def test_an_unknown_sku_is_a_pure_shortfall(self) -> None:
        sku_id = SkuId(new_id())
        plan = InventoryAllocationPolicy(AllocationStrategy.PARTIAL).plan(
            requirements=[self.requirement(sku_id, 4)],
            candidates=[],
            now=NOW,
        )
        assert plan.allocations == ()
        assert plan.shortfalls[0].missing == Quantity(4)

    def test_candidates_of_other_skus_are_ignored(self) -> None:
        sku_id, other_sku = SkuId(new_id()), SkuId(new_id())
        plan = InventoryAllocationPolicy(AllocationStrategy.ALL_OR_NOTHING).plan(
            requirements=[self.requirement(sku_id, 1)],
            candidates=[self.candidate(10, other_sku)],
            now=NOW,
        )
        assert plan.allocations == ()
        assert not plan.is_complete


__all__ = ["TestAllocationPolicy"]
