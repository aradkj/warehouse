"""The InventoryItem aggregate: the row that protects the platform's headline
invariant.

An inventory item is the stock of **one SKU in one warehouse**. It is the unit of
contention: every reservation, release, confirmation and adjustment for that
SKU/warehouse pair funnels through this single row, which is what makes
``SELECT ... FOR UPDATE`` an effective serialisation point and therefore makes
overselling impossible (see ADR-0007).

Two counters are kept:

``on_hand``   physical units in the warehouse,
``reserved``  units promised to reservations that are still active.

The invariant ``available = on_hand - reserved >= 0`` is enforced twice: here,
and by ``CHECK`` constraints in the database, which is the last line of defence
if a future adapter forgets to call the aggregate.

Every mutating method produces an immutable :class:`InventoryMovement` ledger
entry; the item holds them until the repository persists it, so a stock change
without a matching ledger entry cannot be written.
"""

from __future__ import annotations

from datetime import datetime

from inventory.domain.errors import (
    InsufficientInventory,
    InvalidAdjustment,
    StockAdjustmentBelowReserved,
)
from inventory.domain.events import (
    InventoryStockAdjusted,
    InventoryStockCommitted,
    InventoryStockReleased,
    InventoryStockReserved,
)
from inventory.domain.movement import InventoryMovement
from inventory.domain.value_objects import (
    AdjustmentReason,
    MovementType,
    ReferenceType,
    StockShortfall,
)
from shared.domain.aggregate import AggregateRoot
from shared.domain.errors import InvariantViolation
from shared.domain.identifiers import (
    InventoryItemId,
    InventoryMovementId,
    ReservationId,
    SkuId,
    TenantId,
    WarehouseId,
    new_id,
)
from shared.domain.value_objects import Quantity


class InventoryItem(AggregateRoot):
    """Stock of one SKU in one warehouse."""

    __slots__ = (
        "_created_at",
        "_id",
        "_on_hand",
        "_pending_movements",
        "_reserved",
        "_sku_id",
        "_updated_at",
        "_warehouse_id",
    )

    def __init__(
        self,
        *,
        id: InventoryItemId,
        tenant_id: TenantId,
        warehouse_id: WarehouseId,
        sku_id: SkuId,
        on_hand: int,
        reserved: int,
        created_at: datetime,
        updated_at: datetime,
        version: int = 0,
    ) -> None:
        super().__init__(tenant_id=tenant_id, version=version)
        if on_hand < 0 or reserved < 0 or reserved > on_hand:
            raise InvariantViolation(
                "an inventory item cannot hold negative stock or reserve more than is on hand",
                details={"on_hand": on_hand, "reserved": reserved},
            )
        self._id = id
        self._warehouse_id = warehouse_id
        self._sku_id = sku_id
        self._on_hand = on_hand
        self._reserved = reserved
        self._created_at = created_at
        self._updated_at = updated_at
        self._pending_movements: list[InventoryMovement] = []

    # -- creation ----------------------------------------------------------
    @classmethod
    def register(
        cls,
        *,
        tenant_id: TenantId,
        warehouse_id: WarehouseId,
        sku_id: SkuId,
        now: datetime,
    ) -> InventoryItem:
        """Register an empty stock row for a SKU/warehouse pair.

        Registering writes no movement: nothing moved yet. The first adjustment
        produces the first ledger entry.
        """
        return cls(
            id=InventoryItemId(new_id()),
            tenant_id=tenant_id,
            warehouse_id=warehouse_id,
            sku_id=sku_id,
            on_hand=0,
            reserved=0,
            created_at=now,
            updated_at=now,
            version=0,
        )

    # -- state -------------------------------------------------------------
    @property
    def id(self) -> InventoryItemId:
        return self._id

    @property
    def warehouse_id(self) -> WarehouseId:
        return self._warehouse_id

    @property
    def sku_id(self) -> SkuId:
        return self._sku_id

    @property
    def on_hand(self) -> Quantity:
        return Quantity(self._on_hand)

    @property
    def reserved(self) -> Quantity:
        return Quantity(self._reserved)

    @property
    def available(self) -> Quantity:
        """Units that may still be reserved. Never negative (invariant)."""
        return Quantity(self._on_hand - self._reserved)

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    # -- ledger ------------------------------------------------------------
    @property
    def pending_movements(self) -> tuple[InventoryMovement, ...]:
        """Movements produced by the last mutations, not yet persisted."""
        return tuple(self._pending_movements)

    def pull_movements(self) -> tuple[InventoryMovement, ...]:
        """Take ownership of the pending movements (used by the repository)."""
        movements = tuple(self._pending_movements)
        self._pending_movements.clear()
        return movements

    # -- behaviour ---------------------------------------------------------
    def adjust(
        self,
        *,
        delta: int,
        reason: AdjustmentReason,
        now: datetime,
        reference_type: ReferenceType | None = None,
        reference_id: object | None = None,
        note: str | None = None,
    ) -> InventoryMovement:
        """Apply a signed stock correction (receipt, cycle count, damage, ...).

        Raises when the adjustment would make physical stock negative **or** would
        push it below the quantity already promised to reservations.
        """
        if delta == 0:
            raise InvalidAdjustment(
                "an inventory adjustment must not be zero",
                details={"inventory_item_id": str(self._id)},
            )
        new_on_hand = self._on_hand + delta
        if new_on_hand < 0:
            raise InvariantViolation(
                "on-hand stock cannot become negative",
                details={
                    "inventory_item_id": str(self._id),
                    "on_hand": self._on_hand,
                    "requested_delta": delta,
                },
            )
        if new_on_hand < self._reserved:
            raise StockAdjustmentBelowReserved(
                "the adjustment would leave less stock than is currently reserved",
                details={
                    "inventory_item_id": str(self._id),
                    "on_hand": self._on_hand,
                    "reserved": self._reserved,
                    "requested_delta": delta,
                },
            )
        self._on_hand = new_on_hand
        self._updated_at = now
        movement_type = MovementType.RECEIPT if delta > 0 else MovementType.ADJUSTMENT
        movement = self._movement(
            movement_type=movement_type,
            reason=reason,
            quantity_delta=delta,
            reference_type=reference_type,
            reference_id=reference_id,
            note=note,
            occurred_at=now,
        )
        self._record_event(
            InventoryStockAdjusted(
                tenant_id=self.tenant_id,
                inventory_item_id=self._id,
                warehouse_id=self._warehouse_id,
                sku_id=self._sku_id,
                movement_id=movement.id,
                quantity_delta=delta,
                on_hand=self._on_hand,
                reserved=self._reserved,
                reason=reason.value,
            )
        )
        return movement

    def reserve(
        self,
        *,
        quantity: Quantity,
        reservation_id: ReservationId,
        now: datetime,
    ) -> InventoryMovement:
        """Hold ``quantity`` units for a reservation.

        This is the operation the oversell test drives concurrently: it fails with
        :class:`~inventory.domain.errors.InsufficientInventory` unless the
        *currently available* quantity (re-read under a row lock) is sufficient.
        """
        if not quantity.is_positive():
            raise InvalidAdjustment(
                "a reservation must hold a positive quantity",
                details={"inventory_item_id": str(self._id)},
            )
        if quantity > self.available:
            raise InsufficientInventory(
                [
                    StockShortfall(
                        sku_id=self._sku_id,
                        required=quantity,
                        allocated=self.available,
                    )
                ]
            )
        self._reserved += quantity.value
        self._updated_at = now
        movement = self._movement(
            movement_type=MovementType.RESERVATION,
            reason=None,
            quantity_delta=quantity.value,
            reference_type=ReferenceType.RESERVATION,
            reference_id=reservation_id,
            note=None,
            occurred_at=now,
        )
        self._record_event(
            InventoryStockReserved(
                tenant_id=self.tenant_id,
                inventory_item_id=self._id,
                warehouse_id=self._warehouse_id,
                sku_id=self._sku_id,
                movement_id=movement.id,
                reservation_id=reservation_id,
                quantity=quantity.value,
                on_hand=self._on_hand,
                reserved=self._reserved,
            )
        )
        return movement

    def release(
        self,
        *,
        quantity: Quantity,
        reservation_id: ReservationId,
        reason: str,
        now: datetime,
    ) -> InventoryMovement:
        """Return held units to the available pool (cancellation, expiry)."""
        if not quantity.is_positive():
            raise InvalidAdjustment(
                "a release must return a positive quantity",
                details={"inventory_item_id": str(self._id)},
            )
        if quantity.value > self._reserved:
            raise InvariantViolation(
                "cannot release more units than are currently reserved",
                details={
                    "inventory_item_id": str(self._id),
                    "reserved": self._reserved,
                    "requested_release": quantity.value,
                },
            )
        self._reserved -= quantity.value
        self._updated_at = now
        movement = self._movement(
            movement_type=MovementType.RESERVATION_RELEASE,
            reason=None,
            quantity_delta=-quantity.value,
            reference_type=ReferenceType.RESERVATION,
            reference_id=reservation_id,
            note=reason,
            occurred_at=now,
        )
        self._record_event(
            InventoryStockReleased(
                tenant_id=self.tenant_id,
                inventory_item_id=self._id,
                warehouse_id=self._warehouse_id,
                sku_id=self._sku_id,
                movement_id=movement.id,
                reservation_id=reservation_id,
                quantity=quantity.value,
                reason=reason,
                on_hand=self._on_hand,
                reserved=self._reserved,
            )
        )
        return movement

    def commit_reservation(
        self,
        *,
        quantity: Quantity,
        reservation_id: ReservationId,
        now: datetime,
    ) -> InventoryMovement:
        """Turn held units into shipped units: both counters decrease.

        Called when an order is confirmed and the reservation is consumed.
        """
        if not quantity.is_positive():
            raise InvalidAdjustment(
                "a confirmation must consume a positive quantity",
                details={"inventory_item_id": str(self._id)},
            )
        if quantity.value > self._reserved:
            raise InvariantViolation(
                "cannot confirm more units than are currently reserved",
                details={
                    "inventory_item_id": str(self._id),
                    "reserved": self._reserved,
                    "requested": quantity.value,
                },
            )
        self._on_hand -= quantity.value
        self._reserved -= quantity.value
        self._updated_at = now
        movement = self._movement(
            movement_type=MovementType.RESERVATION_CONFIRMATION,
            reason=None,
            quantity_delta=-quantity.value,
            reference_type=ReferenceType.RESERVATION,
            reference_id=reservation_id,
            note=None,
            occurred_at=now,
        )
        self._record_event(
            InventoryStockCommitted(
                tenant_id=self.tenant_id,
                inventory_item_id=self._id,
                warehouse_id=self._warehouse_id,
                sku_id=self._sku_id,
                movement_id=movement.id,
                reservation_id=reservation_id,
                quantity=quantity.value,
                on_hand=self._on_hand,
                reserved=self._reserved,
            )
        )
        return movement

    # -- internals ---------------------------------------------------------
    def _movement(
        self,
        *,
        movement_type: MovementType,
        reason: AdjustmentReason | None,
        quantity_delta: int,
        reference_type: ReferenceType | None,
        reference_id: object | None,
        note: str | None,
        occurred_at: datetime,
    ) -> InventoryMovement:
        movement = InventoryMovement(
            id=InventoryMovementId(new_id()),
            tenant_id=self.tenant_id,
            inventory_item_id=self._id,
            warehouse_id=self._warehouse_id,
            sku_id=self._sku_id,
            movement_type=movement_type,
            reason=reason,
            quantity_delta=quantity_delta,
            on_hand_after=self._on_hand,
            reserved_after=self._reserved,
            reference_type=reference_type.value if reference_type else None,
            reference_id=reference_id,  # type: ignore[arg-type]
            note=note,
            occurred_at=occurred_at,
        )
        self._pending_movements.append(movement)
        return movement


__all__ = ["InventoryItem"]
