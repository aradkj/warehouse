"""The Order aggregate.

The order is the platform's central business document. Two invariants dominate
its design:

1. **No backwards transitions.** The lifecycle is an explicit transition table.
   Moving out of ``CONFIRMED`` is refused outright: a shipped or paid order is
   corrected by an explicit compensating workflow (Phase 2), never by flipping a
   status column back.
2. **A confirmed order must have valid reservations for every line.** The
   aggregate refuses ``confirm()`` unless the caller presents coverage for each
   line - and the caller can only obtain that coverage from inventory, which means
   the check cannot be bypassed by a code path that simply constructs an order and
   sets its status.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from ordering.domain.errors import (
    DuplicateOrderLine,
    InvalidOrder,
    InvalidOrderTransition,
    OrderLinesNotReserved,
)
from ordering.domain.events import (
    OrderBackordered,
    OrderCancelled,
    OrderConfirmed,
    OrderDelivered,
    OrderInventoryReserved,
    OrderPlaced,
    OrderShipped,
)
from ordering.domain.value_objects import MAX_ORDER_LINES, OrderLine, OrderStatus
from shared.domain.aggregate import AggregateRoot
from shared.domain.identifiers import (
    CustomerId,
    OrderId,
    OrderLineId,
    ReservationId,
    SkuId,
    TenantId,
    new_id,
)
from shared.domain.value_objects import Money, Quantity

MAX_CUSTOMER_REFERENCE_LENGTH = 128
MAX_CANCELLATION_REASON_LENGTH = 256

#: Allowed transitions. Every state change goes through this table, which is the
#: single source of truth for "which moves are legal".
_ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset(
        {OrderStatus.INVENTORY_RESERVED, OrderStatus.BACKORDERED, OrderStatus.CANCELLED}
    ),
    # A back-ordered order can be reserved once stock exists, or cancelled.
    OrderStatus.BACKORDERED: frozenset({OrderStatus.INVENTORY_RESERVED, OrderStatus.CANCELLED}),
    OrderStatus.INVENTORY_RESERVED: frozenset(
        {OrderStatus.CONFIRMED, OrderStatus.BACKORDERED, OrderStatus.CANCELLED}
    ),
    # Fulfilment drives these; a Phase 1 API never does, which is why the
    # transitions exist without an endpoint (ADR-0004).
    OrderStatus.CONFIRMED: frozenset({OrderStatus.SHIPPED}),
    OrderStatus.SHIPPED: frozenset({OrderStatus.DELIVERED}),
    OrderStatus.DELIVERED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}

#: States from which a cancellation may be initiated in Phase 1.
_CANCELLABLE = frozenset(
    {OrderStatus.PENDING, OrderStatus.BACKORDERED, OrderStatus.INVENTORY_RESERVED}
)


class Order(AggregateRoot):
    """Order aggregate root."""

    __slots__ = (
        "_cancellation_reason",
        "_cancelled_at",
        "_confirmed_at",
        "_created_at",
        "_currency",
        "_customer_id",
        "_customer_reference",
        "_id",
        "_lines",
        "_status",
        "_updated_at",
    )

    def __init__(
        self,
        *,
        id: OrderId,
        tenant_id: TenantId,
        customer_id: CustomerId | None,
        customer_reference: str | None,
        currency: str,
        status: OrderStatus,
        lines: Sequence[OrderLine],
        created_at: datetime,
        updated_at: datetime,
        confirmed_at: datetime | None = None,
        cancelled_at: datetime | None = None,
        cancellation_reason: str | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(tenant_id=tenant_id, version=version)
        if not lines:
            raise InvalidOrder("an order must contain at least one line", field="lines")
        if len(lines) > MAX_ORDER_LINES:
            raise InvalidOrder(
                f"an order may contain at most {MAX_ORDER_LINES} lines", field="lines"
            )
        sku_ids = [line.sku_id for line in lines]
        if len(set(sku_ids)) != len(sku_ids):
            raise DuplicateOrderLine(
                "an order may hold at most one line per sku",
                details={"sku_ids": [str(sku_id) for sku_id in sku_ids]},
            )
        line_numbers = [line.line_number for line in lines]
        if any(number < 1 for number in line_numbers):
            raise InvalidOrder(
                "order line numbers start at 1",
                field="lines",
                details={"line_numbers": line_numbers},
            )
        if len(set(line_numbers)) != len(line_numbers):
            raise InvalidOrder(
                "order line numbers must be unique within an order",
                field="lines",
                details={"line_numbers": line_numbers},
            )
        for line in lines:
            if line.unit_price.currency != currency:
                raise InvalidOrder(
                    "every line must use the order currency",
                    field="currency",
                    details={"order_currency": currency, "line_currency": line.unit_price.currency},
                )
        self._id = id
        self._customer_id = customer_id
        self._customer_reference = customer_reference
        self._currency = currency
        self._status = status
        self._lines = list(lines)
        self._created_at = created_at
        self._updated_at = updated_at
        self._confirmed_at = confirmed_at
        self._cancelled_at = cancelled_at
        self._cancellation_reason = cancellation_reason

    # -- creation ----------------------------------------------------------
    @classmethod
    def place(
        cls,
        *,
        tenant_id: TenantId,
        customer_id: CustomerId | None,
        customer_reference: str | None,
        currency: str,
        lines: Sequence[OrderLine],
        now: datetime,
    ) -> Order:
        """Place an order; it starts as ``PENDING`` until stock is secured."""
        reference = _validate_reference(customer_reference)
        order_id = OrderId(new_id())
        order = cls(
            id=order_id,
            tenant_id=tenant_id,
            customer_id=customer_id,
            customer_reference=reference,
            currency=currency,
            status=OrderStatus.PENDING,
            lines=lines,
            created_at=now,
            updated_at=now,
            version=0,
        )
        order._record_event(
            OrderPlaced(
                tenant_id=tenant_id,
                order_id=order_id,
                line_count=len(order._lines),
                total_quantity=int(order.total_quantity()),
                total_minor=order.total.amount_minor,
                currency=currency,
                customer_reference=reference,
            )
        )
        return order

    # -- state -------------------------------------------------------------
    @property
    def id(self) -> OrderId:
        return self._id

    @property
    def customer_id(self) -> CustomerId | None:
        return self._customer_id

    @property
    def customer_reference(self) -> str | None:
        return self._customer_reference

    @property
    def currency(self) -> str:
        return self._currency

    @property
    def status(self) -> OrderStatus:
        return self._status

    @property
    def lines(self) -> tuple[OrderLine, ...]:
        return tuple(self._lines)

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def confirmed_at(self) -> datetime | None:
        return self._confirmed_at

    @property
    def cancelled_at(self) -> datetime | None:
        return self._cancelled_at

    @property
    def cancellation_reason(self) -> str | None:
        return self._cancellation_reason

    # -- pricing (snapshot arithmetic) -------------------------------------
    @property
    def total(self) -> Money:
        """Order total, computed from the stored price snapshots."""
        total = Money.zero(self._currency)
        for line in self._lines:
            total = total + line.line_total
        return total

    def total_quantity(self) -> Quantity:
        return Quantity(sum(int(line.quantity) for line in self._lines))

    def line_for(self, order_line_id: OrderLineId) -> OrderLine:
        for line in self._lines:
            if line.id == order_line_id:
                return line
        raise InvalidOrder(
            "the order has no such line",
            field="lines",
            details={"order_line_id": str(order_line_id)},
        )

    def quantities_by_sku(self) -> Mapping[SkuId, int]:
        return {line.sku_id: int(line.quantity) for line in self._lines}

    # -- lifecycle ---------------------------------------------------------
    def mark_inventory_reserved(
        self, *, reservation_id: ReservationId | None, now: datetime
    ) -> None:
        """Record that every line is now held by inventory."""
        self._transition_to(OrderStatus.INVENTORY_RESERVED, now=now)
        self._record_event(
            OrderInventoryReserved(
                tenant_id=self.tenant_id,
                order_id=self._id,
                reservation_id=reservation_id,
                reserved_quantity=int(self.total_quantity()),
            )
        )

    def mark_backordered(
        self, *, shortfalls: Sequence[Mapping[str, object]], now: datetime
    ) -> None:
        """Record that the order was accepted without full stock coverage.

        ``shortfalls`` is the inventory report (per SKU missing quantities); it is
        carried on the event so downstream consumers can reason about the back
        order without re-querying inventory.
        """
        self._transition_to(OrderStatus.BACKORDERED, now=now)
        missing = 0
        for shortfall in shortfalls:
            value = shortfall.get("missing", 0)
            if isinstance(value, int):
                missing += value
        self._record_event(
            OrderBackordered(
                tenant_id=self.tenant_id,
                order_id=self._id,
                missing_quantity=missing,
                shortfalls=[dict(shortfall) for shortfall in shortfalls],
            )
        )

    def confirm(self, *, coverage: Mapping[OrderLineId, int], now: datetime) -> None:
        """Confirm the order after verifying full reservation coverage.

        ``coverage`` maps each order line to the quantity currently held for it
        (produced by the inventory context). Missing or insufficient coverage is
        refused - a confirmed order always has a valid hold for every line.
        """
        if self._status is not OrderStatus.INVENTORY_RESERVED:
            raise InvalidOrderTransition(self._status.value, OrderStatus.CONFIRMED.value)
        uncovered: list[dict[str, object]] = []
        for line in self._lines:
            held = coverage.get(line.id, 0)
            if held < int(line.quantity):
                uncovered.append(
                    {
                        "order_line_id": str(line.id),
                        "sku_id": str(line.sku_id),
                        "required": int(line.quantity),
                        "reserved": held,
                    }
                )
        if uncovered:
            raise OrderLinesNotReserved(uncovered)
        self._transition_to(OrderStatus.CONFIRMED, now=now)
        self._confirmed_at = now
        self._record_event(
            OrderConfirmed(
                tenant_id=self.tenant_id,
                order_id=self._id,
                total_minor=self.total.amount_minor,
                currency=self._currency,
            )
        )

    def mark_shipped(self, *, now: datetime) -> None:
        """Hand the order to a carrier (driven by fulfilment, Phase 2)."""
        self._transition_to(OrderStatus.SHIPPED, now=now)
        self._record_event(OrderShipped(tenant_id=self.tenant_id, order_id=self._id))

    def mark_delivered(self, *, now: datetime) -> None:
        """Mark the order delivered (terminal)."""
        self._transition_to(OrderStatus.DELIVERED, now=now)
        self._record_event(OrderDelivered(tenant_id=self.tenant_id, order_id=self._id))

    def cancel(self, *, reason: str, now: datetime) -> None:
        """Cancel the order.

        Only reachable from states where no stock has been consumed. Cancelling a
        CONFIRMED order is a compensating workflow, not a state flip.
        """
        if self._status not in _CANCELLABLE:
            raise InvalidOrderTransition(
                self._status.value,
                OrderStatus.CANCELLED.value,
                reason="requires_compensation" if self._status is OrderStatus.CONFIRMED else None,
            )
        cleaned = reason.strip()
        if not cleaned or len(cleaned) > MAX_CANCELLATION_REASON_LENGTH:
            raise InvalidOrder(
                "a cancellation reason of 1-"
                f"{MAX_CANCELLATION_REASON_LENGTH} characters is required",
                field="reason",
            )
        self._transition_to(OrderStatus.CANCELLED, now=now)
        self._cancelled_at = now
        self._cancellation_reason = cleaned
        self._record_event(
            OrderCancelled(tenant_id=self.tenant_id, order_id=self._id, reason=cleaned)
        )

    # -- internals ---------------------------------------------------------
    def _transition_to(self, target: OrderStatus, *, now: datetime) -> None:
        if target not in _ALLOWED_TRANSITIONS[self._status]:
            raise InvalidOrderTransition(self._status.value, target.value)
        self._status = target
        self._updated_at = now


def _validate_reference(reference: str | None) -> str | None:
    if reference is None:
        return None
    stripped = reference.strip()
    if not stripped:
        return None
    if len(stripped) > MAX_CUSTOMER_REFERENCE_LENGTH:
        raise InvalidOrder(
            f"customer reference must be at most {MAX_CUSTOMER_REFERENCE_LENGTH} characters",
            field="customer_reference",
        )
    return stripped


__all__ = ["Order"]
