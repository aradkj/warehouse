"""Inventory domain events.

These events are the platform's integration points: Phase 2 publishes them
through the transactional outbox so that fulfilment, search projections and
audit can react without inventory knowing about them.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from shared.domain.events import TenantDomainEvent
from shared.domain.identifiers import (
    InventoryItemId,
    InventoryMovementId,
    ReservationId,
    SkuId,
    WarehouseId,
)


# ---------------------------------------------------------------------------
# Warehouse
# ---------------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True, slots=True)
class WarehouseRegistered(TenantDomainEvent):
    warehouse_id: WarehouseId
    code: str


@dataclass(frozen=True, kw_only=True, slots=True)
class WarehouseRenamed(TenantDomainEvent):
    warehouse_id: WarehouseId
    name: str


@dataclass(frozen=True, kw_only=True, slots=True)
class WarehouseDeactivated(TenantDomainEvent):
    warehouse_id: WarehouseId


@dataclass(frozen=True, kw_only=True, slots=True)
class WarehouseActivated(TenantDomainEvent):
    warehouse_id: WarehouseId


# ---------------------------------------------------------------------------
# Stock
# ---------------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True, slots=True)
class InventoryStockAdjusted(TenantDomainEvent):
    inventory_item_id: InventoryItemId
    warehouse_id: WarehouseId
    sku_id: SkuId
    movement_id: InventoryMovementId
    quantity_delta: int
    on_hand: int
    reserved: int
    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class InventoryStockReserved(TenantDomainEvent):
    inventory_item_id: InventoryItemId
    warehouse_id: WarehouseId
    sku_id: SkuId
    movement_id: InventoryMovementId
    reservation_id: ReservationId
    quantity: int
    on_hand: int
    reserved: int


@dataclass(frozen=True, kw_only=True, slots=True)
class InventoryStockReleased(TenantDomainEvent):
    inventory_item_id: InventoryItemId
    warehouse_id: WarehouseId
    sku_id: SkuId
    movement_id: InventoryMovementId
    reservation_id: ReservationId
    quantity: int
    reason: str
    on_hand: int
    reserved: int


@dataclass(frozen=True, kw_only=True, slots=True)
class InventoryStockCommitted(TenantDomainEvent):
    """Reserved stock left the warehouse because an order was confirmed."""

    inventory_item_id: InventoryItemId
    warehouse_id: WarehouseId
    sku_id: SkuId
    movement_id: InventoryMovementId
    reservation_id: ReservationId
    quantity: int
    on_hand: int
    reserved: int


# ---------------------------------------------------------------------------
# Reservations
# ---------------------------------------------------------------------------
@dataclass(frozen=True, kw_only=True, slots=True)
class ReservationCreated(TenantDomainEvent):
    reservation_id: ReservationId
    order_id: UUID | None
    expires_at: str
    allocated_quantity: int
    allocation_count: int


@dataclass(frozen=True, kw_only=True, slots=True)
class ReservationConfirmed(TenantDomainEvent):
    reservation_id: ReservationId
    order_id: UUID | None
    allocated_quantity: int


@dataclass(frozen=True, kw_only=True, slots=True)
class ReservationReleased(TenantDomainEvent):
    reservation_id: ReservationId
    order_id: UUID | None
    allocated_quantity: int
    reason: str


@dataclass(frozen=True, kw_only=True, slots=True)
class ReservationExpired(TenantDomainEvent):
    """A hold timed out.

    Phase 1 raises this from the domain only; the sweeper that drives it in
    production is a Phase 2 Celery job.
    """

    reservation_id: ReservationId
    order_id: UUID | None
    allocated_quantity: int


__all__ = [
    "InventoryStockAdjusted",
    "InventoryStockCommitted",
    "InventoryStockReleased",
    "InventoryStockReserved",
    "ReservationConfirmed",
    "ReservationCreated",
    "ReservationExpired",
    "ReservationReleased",
    "WarehouseActivated",
    "WarehouseDeactivated",
    "WarehouseRegistered",
    "WarehouseRenamed",
]
