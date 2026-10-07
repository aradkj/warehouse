"""Inventory commands, queries and application views.

Views are the application layer's canonical representation of a resource. The
HTTP layer maps them onto response schemas, and other contexts consume them
through the published port - neither has to know about the aggregates' internals.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from inventory.domain.allocation import AllocationStrategy
from inventory.domain.inventory_item import InventoryItem
from inventory.domain.movement import InventoryMovement
from inventory.domain.value_objects import (
    AdjustmentReason,
    MovementType,
    StockRequirement,
)
from inventory.domain.warehouse import Warehouse

# The three views below are the context's published shapes, so they live in
# ``ports.publication`` where other contexts (and the HTTP layer) may import
# them without reaching into this module's other internals.
from inventory.ports.publication import AllocationView, ReservationView, ShortfallView
from shared.application.context import RequestContext
from shared.domain.identifiers import (
    InventoryItemId,
    InventoryMovementId,
    OrderId,
    ReservationId,
    SkuId,
    TenantId,
    WarehouseId,
)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class RegisterWarehouseCommand:
    tenant_id: TenantId
    context: RequestContext
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class AdjustInventoryCommand:
    """Signed stock correction for one SKU in one warehouse.

    A positive delta is a receipt; a negative delta is a write-off or a cycle-count
    correction. The aggregate refuses anything that would break the availability
    invariant.
    """

    tenant_id: TenantId
    context: RequestContext
    sku_id: SkuId
    warehouse_id: WarehouseId
    quantity_delta: int
    reason: AdjustmentReason
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ReserveInventoryCommand:
    """Attempt to hold stock for one or more SKUs.

    ``strategy`` decides what happens when the request cannot be satisfied in
    full: hold what is available (``PARTIAL``) or hold nothing
    (``ALL_OR_NOTHING``).
    """

    tenant_id: TenantId
    context: RequestContext
    requirements: tuple[StockRequirement, ...]
    order_id: OrderId | None = None
    warehouse_ids: tuple[WarehouseId, ...] | None = None
    strategy: AllocationStrategy = AllocationStrategy.ALL_OR_NOTHING
    ttl_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class ReleaseReservationCommand:
    tenant_id: TenantId
    context: RequestContext
    reservation_id: ReservationId
    reason: str = "released_by_client"


@dataclass(frozen=True, slots=True)
class ConfirmReservationCommand:
    tenant_id: TenantId
    context: RequestContext
    reservation_id: ReservationId


@dataclass(frozen=True, slots=True)
class ExpireReservationsCommand:
    """System command to sweep and expire active reservations past their TTL."""

    context: RequestContext
    limit: int = 50


@dataclass(frozen=True, slots=True)
class GetReservationQuery:
    tenant_id: TenantId
    context: RequestContext
    reservation_id: ReservationId


@dataclass(frozen=True, slots=True)
class ListWarehousesQuery:
    tenant_id: TenantId
    context: RequestContext
    limit: int
    cursor: str | None = None
    include_inactive: bool = True


@dataclass(frozen=True, slots=True)
class GetWarehouseQuery:
    tenant_id: TenantId
    context: RequestContext
    warehouse_id: WarehouseId


@dataclass(frozen=True, slots=True)
class GetAvailabilityQuery:
    """Availability report, optionally narrowed to one SKU and/or one warehouse."""

    tenant_id: TenantId
    context: RequestContext
    limit: int
    cursor: str | None = None
    sku_id: SkuId | None = None
    warehouse_id: WarehouseId | None = None


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class WarehouseView:
    id: WarehouseId
    code: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    version: int

    @classmethod
    def of(cls, warehouse: Warehouse) -> WarehouseView:
        return cls(
            id=warehouse.id,
            code=warehouse.code,
            name=warehouse.name,
            is_active=warehouse.is_active,
            created_at=warehouse.created_at,
            updated_at=warehouse.updated_at,
            version=warehouse.version,
        )


@dataclass(frozen=True, slots=True)
class AvailabilityView:
    """Stock position of one SKU in one warehouse.

    ``available`` is computed by the aggregate as ``on_hand - reserved``; it is
    reported rather than derived by clients so that every consumer sees the same
    number the reservation logic uses.
    """

    inventory_item_id: InventoryItemId
    warehouse_id: WarehouseId
    sku_id: SkuId
    on_hand: int
    reserved: int
    available: int
    updated_at: datetime
    version: int

    @classmethod
    def of(cls, item: InventoryItem) -> AvailabilityView:
        return cls(
            inventory_item_id=item.id,
            warehouse_id=item.warehouse_id,
            sku_id=item.sku_id,
            on_hand=int(item.on_hand),
            reserved=int(item.reserved),
            available=int(item.available),
            updated_at=item.updated_at,
            version=item.version,
        )


@dataclass(frozen=True, slots=True)
class MovementView:
    id: InventoryMovementId
    inventory_item_id: InventoryItemId
    warehouse_id: WarehouseId
    sku_id: SkuId
    movement_type: MovementType
    reason: AdjustmentReason | None
    quantity_delta: int
    on_hand_after: int
    reserved_after: int
    reference_type: str | None
    reference_id: UUID | None
    note: str | None
    occurred_at: datetime

    @classmethod
    def of(cls, movement: InventoryMovement) -> MovementView:
        return cls(
            id=movement.id,
            inventory_item_id=movement.inventory_item_id,
            warehouse_id=movement.warehouse_id,
            sku_id=movement.sku_id,
            movement_type=movement.movement_type,
            reason=movement.reason,
            quantity_delta=movement.quantity_delta,
            on_hand_after=movement.on_hand_after,
            reserved_after=movement.reserved_after,
            reference_type=movement.reference_type,
            reference_id=movement.reference_id,
            note=movement.note,
            occurred_at=movement.occurred_at,
        )


@dataclass(frozen=True, slots=True)
class AdjustmentView:
    """Result of an adjustment: the new stock position and the ledger entry."""

    item: AvailabilityView
    movement: MovementView

    @classmethod
    def of(cls, item: InventoryItem, movement: InventoryMovement) -> AdjustmentView:
        return cls(item=AvailabilityView.of(item), movement=MovementView.of(movement))


@dataclass(frozen=True, slots=True)
class ReservationResultView:
    """Outcome of a reservation attempt.

    ``reservation`` is ``None`` only when nothing at all could be held, which is
    a legitimate outcome for the ``PARTIAL`` strategy (the caller decides whether
    that becomes a back-order or a rejection).
    """

    reservation: ReservationView | None
    shortfalls: tuple[ShortfallView, ...] = field(default_factory=tuple)
    is_complete: bool = False

    @property
    def has_reservation(self) -> bool:
        return self.reservation is not None

    def covered_lines(self) -> Mapping[UUID, int]:
        return self.reservation.covered_lines() if self.reservation is not None else {}


__all__ = [
    "AdjustInventoryCommand",
    "AdjustmentView",
    "AllocationView",
    "AvailabilityView",
    "ConfirmReservationCommand",
    "ExpireReservationsCommand",
    "GetAvailabilityQuery",
    "GetReservationQuery",
    "GetWarehouseQuery",
    "ListWarehousesQuery",
    "MovementView",
    "RegisterWarehouseCommand",
    "ReleaseReservationCommand",
    "ReservationResultView",
    "ReservationView",
    "ReserveInventoryCommand",
    "ShortfallView",
    "WarehouseView",
]
