"""Ordering commands, queries and views."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from inventory.ports.publication import ReservationView
from ordering.domain.order import Order
from ordering.domain.value_objects import OrderLine, OrderStatus
from shared.application.context import RequestContext
from shared.domain.identifiers import OrderId, OrderLineId, ProductId, SkuId, TenantId


@dataclass(frozen=True, slots=True)
class OrderLineRequest:
    """One requested line, before pricing: what and how much."""

    sku_id: SkuId
    quantity: int


@dataclass(frozen=True, slots=True)
class CreateOrderCommand:
    """Submit an order.

    Prices are *not* part of the command: they are read from the catalog inside
    the transaction, so a channel cannot dictate the price it pays.
    """

    tenant_id: TenantId
    context: RequestContext
    lines: tuple[OrderLineRequest, ...]
    customer_reference: str | None = None


@dataclass(frozen=True, slots=True)
class ConfirmOrderCommand:
    tenant_id: TenantId
    context: RequestContext
    order_id: OrderId


@dataclass(frozen=True, slots=True)
class CancelOrderCommand:
    tenant_id: TenantId
    context: RequestContext
    order_id: OrderId
    reason: str
    #: Optional expectation from an ``If-Match`` header (optimistic concurrency).
    expected_version: int | None = None


@dataclass(frozen=True, slots=True)
class GetOrderQuery:
    tenant_id: TenantId
    context: RequestContext
    order_id: OrderId


@dataclass(frozen=True, slots=True)
class ListOrdersQuery:
    tenant_id: TenantId
    context: RequestContext
    limit: int
    cursor: str | None = None
    status: OrderStatus | None = None


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class OrderLineView:
    id: OrderLineId
    line_number: int
    sku_id: SkuId
    product_id: ProductId
    sku_code: str
    product_name: str
    quantity: int
    unit_price_minor: int
    line_total_minor: int
    currency: str

    @classmethod
    def of(cls, line: OrderLine) -> OrderLineView:
        return cls(
            id=line.id,
            line_number=line.line_number,
            sku_id=line.sku_id,
            product_id=line.product_id,
            sku_code=line.sku_code,
            product_name=line.product_name,
            quantity=int(line.quantity),
            unit_price_minor=line.unit_price.amount_minor,
            line_total_minor=line.line_total.amount_minor,
            currency=line.unit_price.currency,
        )


@dataclass(frozen=True, slots=True)
class ReservationSummaryView:
    """Compact view of the inventory hold backing an order."""

    id: str
    status: str
    expires_at: datetime
    allocated_quantity: int
    warehouse_ids: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def of(cls, reservation: ReservationView) -> ReservationSummaryView:
        warehouses: list[str] = []
        for allocation in reservation.allocations:
            warehouse = str(allocation.warehouse_id)
            if warehouse not in warehouses:
                warehouses.append(warehouse)
        return cls(
            id=str(reservation.id),
            status=reservation.status.value,
            expires_at=reservation.expires_at,
            allocated_quantity=reservation.total_quantity,
            warehouse_ids=tuple(warehouses),
        )


@dataclass(frozen=True, slots=True)
class OrderView:
    id: OrderId
    status: OrderStatus
    currency: str
    customer_reference: str | None
    total_minor: int
    total_quantity: int
    placed_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    cancelled_at: datetime | None
    cancellation_reason: str | None
    lines: tuple[OrderLineView, ...]
    reservation: ReservationSummaryView | None
    version: int

    @classmethod
    def of(cls, order: Order, *, reservation: ReservationView | None = None) -> OrderView:
        return cls(
            id=order.id,
            status=order.status,
            currency=order.currency,
            customer_reference=order.customer_reference,
            total_minor=order.total.amount_minor,
            total_quantity=int(order.total_quantity()),
            placed_at=order.created_at,
            updated_at=order.updated_at,
            confirmed_at=order.confirmed_at,
            cancelled_at=order.cancelled_at,
            cancellation_reason=order.cancellation_reason,
            lines=tuple(OrderLineView.of(line) for line in order.lines),
            reservation=(
                ReservationSummaryView.of(reservation) if reservation is not None else None
            ),
            version=order.version,
        )


__all__ = [
    "CancelOrderCommand",
    "ConfirmOrderCommand",
    "CreateOrderCommand",
    "GetOrderQuery",
    "ListOrdersQuery",
    "OrderLineRequest",
    "OrderLineView",
    "OrderView",
    "ReservationSummaryView",
]
