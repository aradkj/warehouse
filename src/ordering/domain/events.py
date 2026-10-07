"""Ordering domain events."""

from __future__ import annotations

from dataclasses import dataclass

from shared.domain.events import TenantDomainEvent
from shared.domain.identifiers import OrderId, ReservationId


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderPlaced(TenantDomainEvent):
    order_id: OrderId
    line_count: int
    total_quantity: int
    total_minor: int
    currency: str
    customer_reference: str | None


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderInventoryReserved(TenantDomainEvent):
    order_id: OrderId
    reservation_id: ReservationId | None
    reserved_quantity: int


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderBackordered(TenantDomainEvent):
    """The submission flow accepted the order without securing all stock."""

    order_id: OrderId
    missing_quantity: int
    shortfalls: list[dict[str, object]]


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderConfirmed(TenantDomainEvent):
    order_id: OrderId
    total_minor: int
    currency: str


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderCancelled(TenantDomainEvent):
    order_id: OrderId
    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderShipped(TenantDomainEvent):
    order_id: OrderId


@dataclass(frozen=True, kw_only=True, slots=True)
class OrderDelivered(TenantDomainEvent):
    order_id: OrderId


__all__ = [
    "OrderBackordered",
    "OrderCancelled",
    "OrderConfirmed",
    "OrderDelivered",
    "OrderInventoryReserved",
    "OrderPlaced",
    "OrderShipped",
]
