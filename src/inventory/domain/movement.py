"""The InventoryMovement entity: the append-only stock ledger.

Movements are *immutable* and are never updated or deleted. They answer the
questions an operations team always ends up asking: why did stock change, when,
because of which reservation or adjustment, and what were the counters
afterwards. ``on_hand_after``/``reserved_after`` make the ledger self-contained,
so a stock discrepancy can be reconstructed without replaying history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from inventory.domain.value_objects import AdjustmentReason, MovementType
from shared.domain.aggregate import Entity
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import (
    InventoryItemId,
    InventoryMovementId,
    SkuId,
    TenantId,
    WarehouseId,
)

MAX_NOTE_LENGTH = 256


@dataclass(frozen=True, slots=True)
class InventoryMovement(Entity):
    """One immutable entry in the stock ledger."""

    id: InventoryMovementId
    tenant_id: TenantId
    inventory_item_id: InventoryItemId
    warehouse_id: WarehouseId
    sku_id: SkuId
    movement_type: MovementType
    reason: AdjustmentReason | None
    #: Signed change this movement applied: ``on_hand`` delta for receipts and
    #: adjustments, ``reserved`` delta for holds and releases.
    quantity_delta: int
    on_hand_after: int
    reserved_after: int
    reference_type: str | None
    reference_id: UUID | None
    note: str | None
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.quantity_delta == 0:
            raise InvalidValue("a movement must change stock by a non-zero amount")
        if self.on_hand_after < 0 or self.reserved_after < 0:
            raise InvalidValue("a movement cannot leave negative counters")
        if self.note is not None and len(self.note) > MAX_NOTE_LENGTH:
            raise InvalidValue(f"a movement note may hold at most {MAX_NOTE_LENGTH} characters")


__all__ = ["MAX_NOTE_LENGTH", "InventoryMovement"]
