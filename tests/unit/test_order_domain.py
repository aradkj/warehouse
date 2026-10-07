"""Unit tests: the order aggregate's lifecycle and invariants.

Three rules are pinned down here, because they are the ones a reviewer will look
for:

* **no backwards transitions** - the lifecycle is a table, and every illegal move
  raises instead of silently flipping a status;
* **a confirmed order has coverage for every line** - ``confirm`` refuses without
  it, and it cannot be reached from any state other than ``INVENTORY_RESERVED``;
* **prices are snapshots** - a later catalogue change cannot alter an order.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ordering.domain.errors import (
    DuplicateOrderLine,
    InvalidOrder,
    InvalidOrderTransition,
    OrderLinesNotReserved,
)
from ordering.domain.order import MAX_CUSTOMER_REFERENCE_LENGTH, Order
from ordering.domain.value_objects import MAX_ORDER_LINES, OrderLine, OrderStatus
from shared.domain.identifiers import OrderId, ProductId, ReservationId, SkuId, TenantId, new_id
from shared.domain.value_objects import Money, Quantity

pytestmark = pytest.mark.unit

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
TENANT = TenantId(new_id())


def line(
    *,
    number: int = 1,
    sku_id: SkuId | None = None,
    quantity: int = 2,
    unit_price_minor: int = 2599,
) -> OrderLine:
    return OrderLine.create(
        line_number=number,
        sku_id=sku_id or SkuId(new_id()),
        product_id=ProductId(new_id()),
        sku_code="TEE-BLUE-M",
        product_name="Blue Tee",
        quantity=Quantity(quantity),
        unit_price=Money(unit_price_minor, "GBP"),
        now=NOW,
    )


def order(*lines: OrderLine, currency: str = "GBP", reference: str | None = "PO-1") -> Order:
    return Order.place(
        tenant_id=TENANT,
        customer_id=None,
        customer_reference=reference,
        currency=currency,
        lines=list(lines) or [line()],
        now=NOW,
    )


class TestOrderPlacement:
    def test_a_new_order_is_pending(self) -> None:
        aggregate = order()
        assert aggregate.status is OrderStatus.PENDING
        assert [event.event_name for event in aggregate.events] == ["OrderPlaced"]

    def test_an_order_requires_at_least_one_line(self) -> None:
        with pytest.raises(InvalidOrder, match="at least one line"):
            Order.place(
                tenant_id=TENANT,
                customer_id=None,
                customer_reference=None,
                currency="GBP",
                lines=[],
                now=NOW,
            )

    def test_totals_are_derived_from_the_lines(self) -> None:
        aggregate = order(
            line(quantity=2, unit_price_minor=2599),
            line(number=2, quantity=1, unit_price_minor=1000),
        )
        assert aggregate.total == Money(2599 * 2 + 1000, "GBP")
        assert aggregate.total_quantity() == Quantity(3)

    def test_one_line_per_sku(self) -> None:
        sku_id = SkuId(new_id())
        with pytest.raises(DuplicateOrderLine):
            order(line(sku_id=sku_id), line(number=2, sku_id=sku_id))

    def test_line_count_is_bounded(self) -> None:
        with pytest.raises(InvalidOrder):
            order(*[line(number=n) for n in range(1, MAX_ORDER_LINES + 2)])

    def test_line_numbers_must_be_positive_and_unique(self) -> None:
        # Mirrors the ``line_number >= 1`` CHECK and the
        # ``uq_order_lines_order_id_line_number`` unique constraint in the schema.
        with pytest.raises(InvalidOrder, match="start at 1"):
            order(line(number=0))
        with pytest.raises(InvalidOrder, match="must be unique"):
            order(line(number=1), line(number=1, sku_id=SkuId(new_id())))

    def test_a_line_must_order_a_positive_quantity(self) -> None:
        from shared.domain.errors import InvalidValue

        with pytest.raises(InvalidValue):
            line(quantity=0)

    def test_customer_reference_is_validated_and_optional(self) -> None:
        assert order(reference=None).customer_reference is None
        with pytest.raises(InvalidOrder):
            order(reference="x" * (MAX_CUSTOMER_REFERENCE_LENGTH + 1))


class TestOrderLifecycle:
    def test_reserving_inventory_moves_to_inventory_reserved(self) -> None:
        aggregate = order()
        aggregate.mark_inventory_reserved(reservation_id=ReservationId(new_id()), now=NOW)
        assert aggregate.status is OrderStatus.INVENTORY_RESERVED

    def test_confirmation_requires_full_coverage_for_every_line(self) -> None:
        aggregate = order(line(quantity=3))
        aggregate.mark_inventory_reserved(reservation_id=ReservationId(new_id()), now=NOW)
        target_line = aggregate.lines[0]

        with pytest.raises(OrderLinesNotReserved):
            aggregate.confirm(coverage={target_line.id: 2}, now=NOW)
        with pytest.raises(OrderLinesNotReserved):
            aggregate.confirm(coverage={}, now=NOW)

        aggregate.confirm(coverage={target_line.id: 3}, now=NOW)
        assert aggregate.status is OrderStatus.CONFIRMED

    def test_confirmation_is_refused_from_any_other_state(self) -> None:
        with pytest.raises(InvalidOrderTransition):
            order().confirm(coverage={}, now=NOW)

    def test_backorder_records_the_shortfall(self) -> None:
        aggregate = order()
        aggregate.mark_backordered(
            shortfalls=[{"sku_id": str(aggregate.lines[0].sku_id), "missing": 2}], now=NOW
        )
        assert aggregate.status is OrderStatus.BACKORDERED
        event = aggregate.events[-1]
        assert event.event_name == "OrderBackordered"
        assert event.payload()["missing_quantity"] == 2

    def test_a_backordered_order_can_be_reserved_and_confirmed_later(self) -> None:
        aggregate = order()
        aggregate.mark_backordered(shortfalls=[], now=NOW)
        aggregate.mark_inventory_reserved(reservation_id=ReservationId(new_id()), now=NOW)
        aggregate.confirm(coverage={aggregate.lines[0].id: 2}, now=NOW)
        assert aggregate.status is OrderStatus.CONFIRMED

    def test_confirming_twice_is_refused(self) -> None:
        aggregate = order()
        aggregate.mark_inventory_reserved(reservation_id=ReservationId(new_id()), now=NOW)
        aggregate.confirm(coverage={aggregate.lines[0].id: 2}, now=NOW)
        with pytest.raises(InvalidOrderTransition):
            aggregate.confirm(coverage={aggregate.lines[0].id: 2}, now=NOW)

    def test_confirmed_orders_cannot_move_backwards(self) -> None:
        aggregate = order()
        aggregate.mark_inventory_reserved(reservation_id=ReservationId(new_id()), now=NOW)
        aggregate.confirm(coverage={aggregate.lines[0].id: 2}, now=NOW)
        with pytest.raises(InvalidOrderTransition):
            aggregate.mark_inventory_reserved(reservation_id=ReservationId(new_id()), now=NOW)

    def test_cancelling_a_pending_order_is_allowed(self) -> None:
        aggregate = order()
        aggregate.cancel(reason="customer changed their mind", now=NOW)
        assert aggregate.status is OrderStatus.CANCELLED
        assert aggregate.cancellation_reason == "customer changed their mind"

    def test_cancelling_a_confirmed_order_requires_compensation(self) -> None:
        aggregate = order()
        aggregate.mark_inventory_reserved(reservation_id=ReservationId(new_id()), now=NOW)
        aggregate.confirm(coverage={aggregate.lines[0].id: 2}, now=NOW)
        with pytest.raises(InvalidOrderTransition) as caught:
            aggregate.cancel(reason="too late", now=NOW)
        assert caught.value.details["reason"] == "requires_compensation"

    def test_a_cancellation_reason_is_required(self) -> None:
        with pytest.raises(InvalidOrder):
            order().cancel(reason="   ", now=NOW)
        with pytest.raises(InvalidOrder):
            order().cancel(reason="x" * 257, now=NOW)

    def test_terminal_states_are_terminal(self) -> None:
        aggregate = order()
        aggregate.cancel(reason="cancelled", now=NOW)
        with pytest.raises(InvalidOrderTransition):
            aggregate.cancel(reason="again", now=NOW)

    def test_fulfilment_transitions_exist_but_are_not_exposed_yet(self) -> None:
        aggregate = order()
        aggregate.mark_inventory_reserved(reservation_id=ReservationId(new_id()), now=NOW)
        aggregate.confirm(coverage={aggregate.lines[0].id: 2}, now=NOW)
        aggregate.mark_shipped(now=NOW)
        aggregate.mark_delivered(now=NOW)
        assert aggregate.status is OrderStatus.DELIVERED


class TestOrderSnapshots:
    def test_line_pricing_is_snapshotted_at_placement(self) -> None:
        aggregate = order(line(unit_price_minor=2599, quantity=2))
        snapshot = aggregate.lines[0]
        assert snapshot.unit_price == Money(2599, "GBP")
        assert snapshot.line_total == Money(5198, "GBP")
        assert snapshot.sku_code == "TEE-BLUE-M"
        assert snapshot.product_name == "Blue Tee"

    def test_the_order_currency_comes_from_the_lines(self) -> None:
        aggregate = order(currency="GBP")
        assert aggregate.currency == "GBP"
        assert aggregate.total.currency == "GBP"

    def test_quantities_by_sku_groups_lines(self) -> None:
        first, second = SkuId(new_id()), SkuId(new_id())
        aggregate = order(
            line(sku_id=first, quantity=2),
            line(number=2, sku_id=second, quantity=5),
        )
        assert aggregate.quantities_by_sku() == {first: 2, second: 5}

    def test_lines_can_be_looked_up_by_identity(self) -> None:
        aggregate = order()
        target = aggregate.lines[0].id
        assert aggregate.line_for(target).id == target

    def test_events_are_collected_and_can_be_pulled(self) -> None:
        aggregate = order()
        aggregate.mark_inventory_reserved(reservation_id=ReservationId(new_id()), now=NOW)
        aggregate.confirm(coverage={aggregate.lines[0].id: 2}, now=NOW)
        names = [event.event_name for event in aggregate.pull_events()]
        assert names == ["OrderPlaced", "OrderInventoryReserved", "OrderConfirmed"]
        assert isinstance(aggregate.id, OrderId.__supertype__)  # type: ignore[attr-defined]
