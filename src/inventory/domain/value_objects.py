"""Inventory value objects: movement classification, reservation states and the
allocation plan produced by the allocation policy.

``Quantity`` and ``Money`` come from the shared kernel; everything here is
specific to how stock moves.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from shared.domain.aggregate import Entity
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import (
    InventoryItemId,
    OrderLineId,
    ReservationAllocationId,
    SkuId,
    WarehouseId,
    new_id,
)
from shared.domain.value_objects import Quantity


class MovementType(StrEnum):
    """What a stock movement does to the two counters of an inventory item.

    ===========================  =========  =========
    type                         on_hand    reserved
    ===========================  =========  =========
    RECEIPT                      +          =
    ADJUSTMENT                   +/-        =
    RESERVATION                  =          +
    RESERVATION_RELEASE          =          -
    RESERVATION_CONFIRMATION     -          -
    ===========================  =========  =========
    """

    RECEIPT = "RECEIPT"
    ADJUSTMENT = "ADJUSTMENT"
    RESERVATION = "RESERVATION"
    RESERVATION_RELEASE = "RESERVATION_RELEASE"
    RESERVATION_CONFIRMATION = "RESERVATION_CONFIRMATION"


class AdjustmentReason(StrEnum):
    """Why a manual adjustment happened (only meaningful for ADJUSTMENT/RECEIPT)."""

    GOODS_RECEIPT = "GOODS_RECEIPT"
    CUSTOMER_RETURN = "CUSTOMER_RETURN"
    CYCLE_COUNT = "CYCLE_COUNT"
    DAMAGE = "DAMAGE"
    MANUAL_CORRECTION = "MANUAL_CORRECTION"


class ReservationStatus(StrEnum):
    """Reservation lifecycle.

    ``ACTIVE``    - stock is held, the reservation can still be confirmed.
    ``CONFIRMED`` - stock left the building; terminal.
    ``RELEASED``  - caller gave the stock back (cancellation); terminal.
    ``EXPIRED``   - the hold timed out (Phase 2 sweeper); terminal.
    """

    ACTIVE = "ACTIVE"
    CONFIRMED = "CONFIRMED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"

    @property
    def is_terminal(self) -> bool:
        return self is not ReservationStatus.ACTIVE


class ReferenceType(StrEnum):
    """What an inventory movement refers to (for traceability)."""

    RESERVATION = "reservation"
    ADJUSTMENT = "adjustment"
    ORDER = "order"
    MIGRATION = "migration"


@dataclass(frozen=True, slots=True)
class ReservationAllocation(Entity):
    """One line of a reservation: a quantity held on one inventory item.

    Allocations are the reason a single order can be satisfied from several
    warehouses, and they are what fulfilment will later turn into work.
    """

    id: ReservationAllocationId
    inventory_item_id: InventoryItemId
    warehouse_id: WarehouseId
    sku_id: SkuId
    quantity: Quantity
    #: Order line this hold belongs to (``None`` for standalone reservations).
    order_line_id: OrderLineId | None = None
    created_at: datetime | None = None

    @classmethod
    def create(
        cls,
        *,
        inventory_item_id: InventoryItemId,
        warehouse_id: WarehouseId,
        sku_id: SkuId,
        quantity: Quantity,
        order_line_id: OrderLineId | None,
        now: datetime,
    ) -> ReservationAllocation:
        if not quantity.is_positive():
            raise InvalidValue(
                "a reservation allocation must hold a positive quantity",
                details={"inventory_item_id": str(inventory_item_id)},
            )
        return cls(
            id=ReservationAllocationId(new_id()),
            inventory_item_id=inventory_item_id,
            warehouse_id=warehouse_id,
            sku_id=sku_id,
            quantity=quantity,
            order_line_id=order_line_id,
            created_at=now,
        )


@dataclass(frozen=True, slots=True)
class StockRequirement:
    """What a caller needs: a quantity of one SKU (optionally for one order line)."""

    sku_id: SkuId
    quantity: Quantity
    order_line_id: OrderLineId | None = None


@dataclass(frozen=True, slots=True)
class AllocationCandidate:
    """What a warehouse currently offers for a SKU."""

    inventory_item_id: InventoryItemId
    warehouse_id: WarehouseId
    sku_id: SkuId
    available: Quantity


@dataclass(frozen=True, slots=True)
class StockShortfall:
    """The part of a requirement that could not be allocated."""

    sku_id: SkuId
    required: Quantity
    allocated: Quantity

    @property
    def missing(self) -> Quantity:
        return Quantity(max(0, self.required.value - self.allocated.value))


@dataclass(frozen=True, slots=True)
class AllocationPlan:
    """Result of running the allocation policy over requirements and candidates."""

    allocations: tuple[ReservationAllocation, ...] = ()
    shortfalls: tuple[StockShortfall, ...] = ()

    @property
    def is_complete(self) -> bool:
        return not self.shortfalls

    @property
    def is_empty(self) -> bool:
        return not self.allocations

    def quantity_for_item(self, inventory_item_id: InventoryItemId) -> Quantity:
        """Total quantity this plan holds on one inventory item."""
        total = 0
        for allocation in self.allocations:
            if allocation.inventory_item_id == inventory_item_id:
                total += allocation.quantity.value
        return Quantity(total)

    def covered_lines(self) -> dict[str, int]:
        """Quantity allocated per order line id (string keys for JSON friendliness)."""
        covered: dict[str, int] = {}
        for allocation in self.allocations:
            if allocation.order_line_id is None:
                continue
            key = str(allocation.order_line_id)
            covered[key] = covered.get(key, 0) + allocation.quantity.value
        return covered


def encode_details(payload: object) -> str:
    """Serialise structured details for the ``inventory_movements.details`` column."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str)


__all__ = [
    "AdjustmentReason",
    "AllocationCandidate",
    "AllocationPlan",
    "MovementType",
    "ReferenceType",
    "ReservationAllocation",
    "ReservationStatus",
    "StockRequirement",
    "StockShortfall",
    "encode_details",
]
