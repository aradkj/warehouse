"""Fulfillment domain events."""

from __future__ import annotations

from dataclasses import dataclass

from shared.domain.events import TenantDomainEvent
from shared.domain.identifiers import FulfillmentId, OrderId, WarehouseId


@dataclass(frozen=True, kw_only=True, slots=True)
class FulfillmentCreated(TenantDomainEvent):
    """Fulfillment aggregate created for an order."""

    fulfillment_id: FulfillmentId
    order_id: OrderId
    warehouse_id: WarehouseId
    status: str


@dataclass(frozen=True, kw_only=True, slots=True)
class FulfillmentAllocated(TenantDomainEvent):
    """Warehouse tasks successfully allocated."""

    fulfillment_id: FulfillmentId
    order_id: OrderId
    warehouse_id: WarehouseId


@dataclass(frozen=True, kw_only=True, slots=True)
class FulfillmentShipped(TenantDomainEvent):
    """Shipment completed by carrier."""

    fulfillment_id: FulfillmentId
    order_id: OrderId
    tracking_number: str
    carrier: str


@dataclass(frozen=True, kw_only=True, slots=True)
class FulfillmentFailed(TenantDomainEvent):
    """Fulfillment failed either permanently or after exhausting retries."""

    fulfillment_id: FulfillmentId
    order_id: OrderId
    warehouse_id: WarehouseId
    reason: str
    is_permanent: bool


@dataclass(frozen=True, kw_only=True, slots=True)
class FulfillmentCompensated(TenantDomainEvent):
    """Compensating action triggered: reservations released or reallocated."""

    fulfillment_id: FulfillmentId
    order_id: OrderId
    warehouse_id: WarehouseId
    reason: str


__all__ = [
    "FulfillmentAllocated",
    "FulfillmentCompensated",
    "FulfillmentCreated",
    "FulfillmentFailed",
    "FulfillmentShipped",
]
