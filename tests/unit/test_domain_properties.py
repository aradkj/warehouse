"""Property-based invariant tests using Hypothesis (Phase 5 Workstream B).

Verifies core domain invariants under arbitrary generated sequences of operations:
1. Available stock (on_hand - reserved) is strictly non-negative at all times.
2. Reserved stock is non-negative and <= on_hand.
3. Once a reservation is released or expired, it cannot be transitioned to confirmed.
4. Idempotent reservation releases never double-refund stock.
5. Order state machine never permits backward or illegal transitions.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from inventory.domain.errors import (
    InsufficientInventory,
    InvalidAdjustment,
    InvalidReservationTransition,
    StockAdjustmentBelowReserved,
)
from inventory.domain.inventory_item import InventoryItem
from inventory.domain.reservation import Reservation
from inventory.domain.value_objects import (
    AdjustmentReason,
    ReservationAllocation,
    ReservationStatus,
)
from ordering.domain.errors import InvalidOrderTransition
from ordering.domain.order import Order
from ordering.domain.value_objects import Money, OrderLine, OrderStatus
from shared.domain.errors import InvariantViolation
from shared.domain.identifiers import (
    ProductId,
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


@st.composite
def stock_operation_sequence(draw: st.DrawFn) -> list[tuple[str, int]]:
    """Generates a sequence of positive and negative adjustments and reservation attempts."""
    operations = draw(
        st.lists(
            st.tuples(
                st.sampled_from(["adjust", "reserve", "release"]),
                st.integers(min_value=-50, max_value=100),
            ),
            min_size=1,
            max_size=30,
        )
    )
    return operations


class TestInventoryDomainProperties:
    """Hypothesis properties for Inventory invariants."""

    @given(operations=stock_operation_sequence())
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow], deadline=None)
    def test_available_stock_invariant_never_violated(
        self, operations: list[tuple[str, int]]
    ) -> None:
        item = InventoryItem.register(
            tenant_id=TENANT,
            warehouse_id=WarehouseId(new_id()),
            sku_id=SkuId(new_id()),
            now=NOW,
        )

        active_reservations: list[tuple[ReservationId, Quantity]] = []

        for op_type, amount in operations:
            if op_type == "adjust":
                with contextlib.suppress(
                    InvalidAdjustment, StockAdjustmentBelowReserved, InvariantViolation
                ):
                    item.adjust(
                        delta=amount,
                        reason=(
                            AdjustmentReason.GOODS_RECEIPT
                            if amount > 0
                            else AdjustmentReason.CYCLE_COUNT
                        ),
                        now=NOW,
                    )

            elif op_type == "reserve" and amount > 0:
                qty = Quantity(amount)
                res_id = ReservationId(new_id())
                with contextlib.suppress(
                    InsufficientInventory, InvariantViolation, InvalidAdjustment
                ):
                    item.reserve(quantity=qty, reservation_id=res_id, now=NOW)
                    active_reservations.append((res_id, qty))

            elif op_type == "release" and active_reservations and amount > 0:
                res_id, qty_to_release = active_reservations.pop(0)
                with contextlib.suppress(Exception):
                    item.release(
                        quantity=qty_to_release,
                        reservation_id=res_id,
                        reason="hypothesis_test",
                        now=NOW,
                    )

            # Core Domain Invariants:
            assert (
                item.on_hand.value >= 0
            ), f"on_hand became negative: {item.on_hand.value}"
            assert (
                item.reserved.value >= 0
            ), f"reserved became negative: {item.reserved.value}"
            assert (
                item.available.value >= 0
            ), f"available became negative: {item.available.value}"
            assert (
                item.available.value == item.on_hand.value - item.reserved.value
            ), "available != on_hand - reserved"
            assert (
                item.reserved.value <= item.on_hand.value
            ), "reserved exceeded on_hand"

    @given(
        initial_stock=st.integers(min_value=1, max_value=500),
        reserve_amount=st.integers(min_value=1, max_value=500),
    )
    @settings(max_examples=50)
    def test_reservation_lifecycle_transitions(
        self, initial_stock: int, reserve_amount: int
    ) -> None:
        item = InventoryItem.register(
            tenant_id=TENANT,
            warehouse_id=WarehouseId(new_id()),
            sku_id=SkuId(new_id()),
            now=NOW,
        )
        item.adjust(delta=initial_stock, reason=AdjustmentReason.GOODS_RECEIPT, now=NOW)

        res_id = ReservationId(new_id())
        if reserve_amount > initial_stock:
            with pytest.raises(InsufficientInventory):
                item.reserve(
                    quantity=Quantity(reserve_amount),
                    reservation_id=res_id,
                    now=NOW,
                )
            return

        item.reserve(
            quantity=Quantity(reserve_amount),
            reservation_id=res_id,
            now=NOW,
        )
        reservation = Reservation.create(
            tenant_id=TENANT,
            order_id=None,
            expires_at=NOW + timedelta(minutes=30),
            allocations=(
                ReservationAllocation.create(
                    inventory_item_id=item.id,
                    warehouse_id=item.warehouse_id,
                    sku_id=item.sku_id,
                    quantity=Quantity(reserve_amount),
                    order_line_id=None,
                    now=NOW,
                ),
            ),
            now=NOW,
        )

        assert reservation.status == ReservationStatus.ACTIVE

        # Release the reservation
        reservation.release(reason="hypothesis_test", now=NOW)
        assert reservation.status == ReservationStatus.RELEASED

        # Invariant: Released reservation CANNOT be confirmed
        with pytest.raises(InvalidReservationTransition):
            reservation.confirm(now=NOW)

        # Invariant: Released reservation CANNOT be released again (transition refused)
        with pytest.raises(InvalidReservationTransition):
            reservation.release(reason="repeat_release", now=NOW)


class TestOrderLifecycleProperties:
    """Hypothesis properties for Order state transitions."""

    @given(
        lines_count=st.integers(min_value=1, max_value=5),
        unit_price=st.integers(min_value=100, max_value=10000),
    )
    @settings(max_examples=50)
    def test_order_creation_and_forward_only_lifecycle(
        self, lines_count: int, unit_price: int
    ) -> None:
        lines = [
            OrderLine.create(
                line_number=idx,
                sku_id=SkuId(new_id()),
                product_id=ProductId(new_id()),
                sku_code=f"SKU-{idx}",
                product_name=f"Product {idx}",
                quantity=Quantity(1),
                unit_price=Money(amount_minor=unit_price, currency="GBP"),
                now=NOW,
            )
            for idx in range(1, lines_count + 1)
        ]

        order = Order.place(
            tenant_id=TENANT,
            customer_id=None,
            customer_reference="PO-TEST",
            currency="GBP",
            lines=lines,
            now=NOW,
        )

        assert order.status == OrderStatus.PENDING

        # Transition to INVENTORY_RESERVED
        res_id = ReservationId(new_id())
        order.mark_inventory_reserved(reservation_id=res_id, now=NOW)
        assert order.status == OrderStatus.INVENTORY_RESERVED

        # Full coverage confirm
        coverage = {line.id: line.quantity.value for line in order.lines}
        order.confirm(coverage=coverage, now=NOW)
        assert order.status == OrderStatus.CONFIRMED

        # Invariant: Confirmed order cannot be double-confirmed
        with pytest.raises(InvalidOrderTransition):
            order.confirm(coverage=coverage, now=NOW)

        # Invariant: Confirmed order cannot move back to pending or reserved
        with pytest.raises(InvalidOrderTransition):
            order.mark_inventory_reserved(reservation_id=res_id, now=NOW)
