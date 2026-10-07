"""Inventory repository ports.

Locking is expressed explicitly in the port, because only the caller knows
whether a read is part of a read-modify-write cycle:

* ``for_update=True``          - ``SELECT ... FOR UPDATE`` on one row,
* ``lock_available_for_skus`` - locks every candidate row of a set of SKUs in
  ``ORDER BY id`` (a deterministic order shared by all writers, which is what
  prevents deadlocks between concurrent reservations),
* ``get_or_register_for_update`` - upsert-and-lock for the first stock receipt of
  a SKU/warehouse pair.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from inventory.domain.inventory_item import InventoryItem
from inventory.domain.movement import InventoryMovement
from inventory.domain.reservation import Reservation
from inventory.domain.warehouse import Warehouse
from shared.application.pagination import Page
from shared.domain.identifiers import (
    InventoryItemId,
    OrderId,
    ReservationId,
    SkuId,
    TenantId,
    WarehouseId,
)


@runtime_checkable
class WarehouseRepository(Protocol):
    """Persistence port for the :class:`inventory.domain.warehouse.Warehouse` aggregate."""

    async def add(self, warehouse: Warehouse) -> None: ...

    async def get(
        self, *, tenant_id: TenantId, warehouse_id: WarehouseId, for_update: bool = False
    ) -> Warehouse | None: ...

    async def get_by_code(self, *, tenant_id: TenantId, code: str) -> Warehouse | None: ...

    async def save(self, warehouse: Warehouse, *, expected_version: int) -> None: ...

    async def page(
        self,
        *,
        tenant_id: TenantId,
        limit: int,
        cursor: str | None = None,
        include_inactive: bool = True,
    ) -> Page[Warehouse]: ...


@runtime_checkable
class InventoryItemRepository(Protocol):
    """Persistence port for the :class:`inventory.domain.inventory_item.InventoryItem` aggregate."""

    async def add(self, item: InventoryItem) -> None:
        """Insert a new stock row together with its pending movements."""
        ...

    async def get(
        self, *, tenant_id: TenantId, inventory_item_id: InventoryItemId
    ) -> InventoryItem | None: ...

    async def load_for_update(
        self, *, tenant_id: TenantId, inventory_item_ids: tuple[InventoryItemId, ...]
    ) -> tuple[InventoryItem, ...]:
        """Lock and load several stock rows in a deterministic order.

        Used when a reservation is confirmed or released: the caller already
        knows which rows it must touch.
        """
        ...

    async def lock_candidates_for_skus(
        self,
        *,
        tenant_id: TenantId,
        sku_ids: tuple[SkuId, ...],
        warehouse_ids: tuple[WarehouseId, ...] | None = None,
    ) -> tuple[InventoryItem, ...]:
        """Lock every stock row that could serve the given SKUs.

        Rows come back ordered by inventory item id, so concurrent reservations
        acquire locks in the same order and cannot deadlock each other.
        """
        ...

    async def get_or_register_for_update(
        self,
        *,
        tenant_id: TenantId,
        warehouse_id: WarehouseId,
        sku_id: SkuId,
        now: datetime,
    ) -> tuple[InventoryItem, bool]:
        """Return the stock row of a SKU/warehouse pair, locked; create it if absent.

        Returns ``(item, created)``. Implementations must be race-free (an
        ``INSERT ... ON CONFLICT DO NOTHING`` followed by a locking select), so
        two concurrent first receipts cannot both "create" the row.
        """
        ...

    async def save(self, item: InventoryItem, *, expected_version: int) -> None:
        """Update a stock row (and append its pending movements) with a version guard."""
        ...

    async def page(
        self,
        *,
        tenant_id: TenantId,
        limit: int,
        cursor: str | None = None,
        sku_id: SkuId | None = None,
        warehouse_id: WarehouseId | None = None,
    ) -> Page[InventoryItem]: ...


@runtime_checkable
class InventoryMovementRepository(Protocol):
    """Append-only persistence port for the stock ledger."""

    async def add(self, movement: InventoryMovement) -> None: ...

    async def list_for_item(
        self,
        *,
        tenant_id: TenantId,
        inventory_item_id: InventoryItemId,
        limit: int = 100,
    ) -> tuple[InventoryMovement, ...]: ...


@runtime_checkable
class ReservationRepository(Protocol):
    """Persistence port for the :class:`inventory.domain.reservation.Reservation` aggregate."""

    async def add(self, reservation: Reservation) -> None:
        """Insert a reservation together with its allocations."""
        ...

    async def get(
        self, *, tenant_id: TenantId, reservation_id: ReservationId
    ) -> Reservation | None: ...

    async def get_for_update(
        self, *, tenant_id: TenantId, reservation_id: ReservationId
    ) -> Reservation | None:
        """Lock one reservation (needed before confirm/release/expire)."""
        ...

    async def get_active_for_order(
        self, *, tenant_id: TenantId, order_id: OrderId
    ) -> Reservation | None: ...

    async def get_active_for_order_for_update(
        self, *, tenant_id: TenantId, order_id: OrderId
    ) -> Reservation | None: ...

    async def save(self, reservation: Reservation, *, expected_version: int) -> None: ...

    async def list_for_order(
        self, *, tenant_id: TenantId, order_id: OrderId
    ) -> tuple[Reservation, ...]: ...

    async def find_expired_active_for_update(
        self, *, now: datetime, limit: int = 50, tenant_id: TenantId | None = None
    ) -> tuple[Reservation, ...]: ...


__all__ = [
    "InventoryItemRepository",
    "InventoryMovementRepository",
    "ReservationRepository",
    "WarehouseRepository",
]
