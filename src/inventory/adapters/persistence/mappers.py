"""Row <-> aggregate mapping for the inventory context."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy.engine import RowMapping

from inventory.domain.inventory_item import InventoryItem
from inventory.domain.movement import InventoryMovement
from inventory.domain.reservation import Reservation
from inventory.domain.value_objects import (
    AdjustmentReason,
    MovementType,
    ReservationAllocation,
    ReservationStatus,
)
from inventory.domain.warehouse import Warehouse
from shared.domain.identifiers import (
    InventoryItemId,
    InventoryMovementId,
    OrderId,
    OrderLineId,
    ReservationAllocationId,
    ReservationId,
    SkuId,
    TenantId,
    WarehouseId,
)
from shared.domain.value_objects import Quantity


# ---------------------------------------------------------------------------
# Warehouse
# ---------------------------------------------------------------------------
def warehouse_insert_values(warehouse: Warehouse) -> dict[str, Any]:
    return {
        "id": warehouse.id,
        "tenant_id": warehouse.tenant_id,
        "code": warehouse.code,
        "name": warehouse.name,
        "is_active": warehouse.is_active,
        "created_at": warehouse.created_at,
        "updated_at": warehouse.updated_at,
        "version": warehouse.version,
    }


def warehouse_update_values(warehouse: Warehouse) -> dict[str, Any]:
    return {
        "name": warehouse.name,
        "is_active": warehouse.is_active,
        "updated_at": warehouse.updated_at,
    }


def warehouse_to_domain(row: RowMapping) -> Warehouse:
    return Warehouse(
        id=WarehouseId(row["id"]),
        tenant_id=TenantId(row["tenant_id"]),
        code=row["code"],
        name=row["name"],
        is_active=row["is_active"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


# ---------------------------------------------------------------------------
# Inventory item
# ---------------------------------------------------------------------------
def inventory_item_insert_values(item: InventoryItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "tenant_id": item.tenant_id,
        "warehouse_id": item.warehouse_id,
        "sku_id": item.sku_id,
        "on_hand": int(item.on_hand),
        "reserved": int(item.reserved),
        "created_at": item.created_at,
        "updated_at": item.updated_at,
        "version": item.version,
    }


def inventory_item_update_values(item: InventoryItem) -> dict[str, Any]:
    return {
        "on_hand": int(item.on_hand),
        "reserved": int(item.reserved),
        "updated_at": item.updated_at,
    }


def inventory_item_to_domain(row: RowMapping) -> InventoryItem:
    return InventoryItem(
        id=InventoryItemId(row["id"]),
        tenant_id=TenantId(row["tenant_id"]),
        warehouse_id=WarehouseId(row["warehouse_id"]),
        sku_id=SkuId(row["sku_id"]),
        on_hand=row["on_hand"],
        reserved=row["reserved"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


# ---------------------------------------------------------------------------
# Movement (append-only)
# ---------------------------------------------------------------------------
def movement_insert_values(movement: InventoryMovement) -> dict[str, Any]:
    return {
        "id": movement.id,
        "tenant_id": movement.tenant_id,
        "inventory_item_id": movement.inventory_item_id,
        "warehouse_id": movement.warehouse_id,
        "sku_id": movement.sku_id,
        "movement_type": movement.movement_type.value,
        "reason": movement.reason.value if movement.reason else None,
        "quantity_delta": movement.quantity_delta,
        "on_hand_after": movement.on_hand_after,
        "reserved_after": movement.reserved_after,
        "reference_type": movement.reference_type,
        "reference_id": movement.reference_id,
        "note": movement.note,
        "occurred_at": movement.occurred_at,
    }


def movement_to_domain(row: RowMapping) -> InventoryMovement:
    return InventoryMovement(
        id=InventoryMovementId(row["id"]),
        tenant_id=TenantId(row["tenant_id"]),
        inventory_item_id=InventoryItemId(row["inventory_item_id"]),
        warehouse_id=WarehouseId(row["warehouse_id"]),
        sku_id=SkuId(row["sku_id"]),
        movement_type=MovementType(row["movement_type"]),
        reason=AdjustmentReason(row["reason"]) if row["reason"] else None,
        quantity_delta=row["quantity_delta"],
        on_hand_after=row["on_hand_after"],
        reserved_after=row["reserved_after"],
        reference_type=row["reference_type"],
        reference_id=row["reference_id"],
        note=row["note"],
        occurred_at=row["occurred_at"],
    )


# ---------------------------------------------------------------------------
# Reservation
# ---------------------------------------------------------------------------
def reservation_insert_values(reservation: Reservation) -> dict[str, Any]:
    return {
        "id": reservation.id,
        "tenant_id": reservation.tenant_id,
        "order_id": reservation.order_id,
        "status": reservation.status.value,
        "expires_at": reservation.expires_at,
        "confirmed_at": reservation.confirmed_at,
        "released_at": reservation.released_at,
        "release_reason": reservation.release_reason,
        "created_at": reservation.created_at,
        "updated_at": reservation.updated_at,
        "version": reservation.version,
    }


def reservation_update_values(reservation: Reservation) -> dict[str, Any]:
    return {
        "status": reservation.status.value,
        "confirmed_at": reservation.confirmed_at,
        "released_at": reservation.released_at,
        "release_reason": reservation.release_reason,
        "updated_at": reservation.updated_at,
    }


def allocation_insert_values(
    tenant_id: TenantId, reservation_id: ReservationId, allocation: ReservationAllocation
) -> dict[str, Any]:
    return {
        "id": allocation.id,
        "tenant_id": tenant_id,
        "reservation_id": reservation_id,
        "inventory_item_id": allocation.inventory_item_id,
        "warehouse_id": allocation.warehouse_id,
        "sku_id": allocation.sku_id,
        "order_line_id": allocation.order_line_id,
        "quantity": int(allocation.quantity),
        "created_at": allocation.created_at,
    }


def allocation_to_domain(row: RowMapping) -> ReservationAllocation:
    order_line_id: UUID | None = row["order_line_id"]
    return ReservationAllocation(
        id=ReservationAllocationId(row["id"]),
        inventory_item_id=InventoryItemId(row["inventory_item_id"]),
        warehouse_id=WarehouseId(row["warehouse_id"]),
        sku_id=SkuId(row["sku_id"]),
        quantity=Quantity(row["quantity"]),
        order_line_id=OrderLineId(order_line_id) if order_line_id is not None else None,
        created_at=row["created_at"],
    )


def reservation_to_domain(row: RowMapping, allocation_rows: Sequence[RowMapping]) -> Reservation:
    order_id: UUID | None = row["order_id"]
    return Reservation(
        id=ReservationId(row["id"]),
        tenant_id=TenantId(row["tenant_id"]),
        order_id=OrderId(order_id) if order_id is not None else None,
        status=ReservationStatus(row["status"]),
        expires_at=row["expires_at"],
        allocations=[allocation_to_domain(allocation) for allocation in allocation_rows],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        confirmed_at=row["confirmed_at"],
        released_at=row["released_at"],
        release_reason=row["release_reason"],
        version=row["version"],
    )


__all__ = [
    "allocation_insert_values",
    "allocation_to_domain",
    "inventory_item_insert_values",
    "inventory_item_to_domain",
    "inventory_item_update_values",
    "movement_insert_values",
    "movement_to_domain",
    "reservation_insert_values",
    "reservation_to_domain",
    "reservation_update_values",
    "warehouse_insert_values",
    "warehouse_to_domain",
    "warehouse_update_values",
]
