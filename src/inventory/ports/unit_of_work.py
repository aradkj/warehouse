"""Inventory unit-of-work port.

Beyond the repositories, this unit of work exposes one read-only helper:
``sku_exists``. Inventory must not read catalog tables, but it does need to know
whether a SKU exists before creating a stock row for it. The catalog adapter
implements the check; the *interface* lives here, where the need arises.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from inventory.ports.repositories import (
    InventoryItemRepository,
    InventoryMovementRepository,
    ReservationRepository,
    WarehouseRepository,
)
from shared.application.use_case import ApplicationUnitOfWork
from shared.domain.identifiers import SkuId, TenantId


@runtime_checkable
class InventoryUnitOfWork(ApplicationUnitOfWork, Protocol):
    """Transactional view over the inventory persistence ports."""

    warehouses: WarehouseRepository
    inventory_items: InventoryItemRepository
    movements: InventoryMovementRepository
    reservations: ReservationRepository

    async def sku_exists(self, *, tenant_id: TenantId, sku_id: SkuId) -> bool:
        """Whether the SKU exists in the tenant (catalog is the authority)."""
        ...


__all__ = ["InventoryUnitOfWork"]
