"""Inventory's *published* contract for other bounded contexts.

Ordering never touches inventory tables or aggregates: it asks for a reservation
through this port and receives application views back. The DTOs below are the
in-process equivalent of an HTTP/RPC contract, which is exactly what makes the
extraction in a later phase a matter of writing one adapter instead of rewriting
the ordering domain.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from inventory.domain.allocation import AllocationStrategy
from inventory.domain.reservation import Reservation
from inventory.domain.value_objects import (
    ReservationAllocation,
    ReservationStatus,
    StockRequirement,
    StockShortfall,
)
from shared.application.context import RequestContext
from shared.domain.identifiers import (
    InventoryItemId,
    OrderId,
    OrderLineId,
    ReservationId,
    SkuId,
    TenantId,
    WarehouseId,
)


@dataclass(frozen=True, slots=True)
class ReservationRequest:
    """A request to hold stock, expressed in the language of the caller."""

    tenant_id: TenantId
    requirements: tuple[StockRequirement, ...]
    order_id: OrderId | None = None
    warehouse_ids: tuple[WarehouseId, ...] | None = None
    strategy: AllocationStrategy = AllocationStrategy.ALL_OR_NOTHING
    ttl_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class AllocationView:
    """One warehouse's contribution to a reservation, as seen from outside."""

    id: UUID
    inventory_item_id: InventoryItemId
    warehouse_id: WarehouseId
    sku_id: SkuId
    order_line_id: OrderLineId | None
    quantity: int
    created_at: datetime | None

    @classmethod
    def of(cls, allocation: ReservationAllocation) -> AllocationView:
        return cls(
            id=allocation.id,
            inventory_item_id=allocation.inventory_item_id,
            warehouse_id=allocation.warehouse_id,
            sku_id=allocation.sku_id,
            order_line_id=allocation.order_line_id,
            quantity=int(allocation.quantity),
            created_at=allocation.created_at,
        )


@dataclass(frozen=True, slots=True)
class ReservationView:
    """A reservation as other contexts are allowed to see it.

    Deliberately a view, not the aggregate: ordering needs the status, the
    expiry and the per-line coverage, and nothing else about how inventory keeps
    its books.
    """

    id: ReservationId
    order_id: OrderId | None
    status: ReservationStatus
    expires_at: datetime
    created_at: datetime
    updated_at: datetime
    confirmed_at: datetime | None
    released_at: datetime | None
    release_reason: str | None
    total_quantity: int
    allocations: tuple[AllocationView, ...]
    version: int

    @classmethod
    def of(cls, reservation: Reservation) -> ReservationView:
        return cls(
            id=reservation.id,
            order_id=reservation.order_id,
            status=reservation.status,
            expires_at=reservation.expires_at,
            created_at=reservation.created_at,
            updated_at=reservation.updated_at,
            confirmed_at=reservation.confirmed_at,
            released_at=reservation.released_at,
            release_reason=reservation.release_reason,
            total_quantity=int(reservation.total_quantity()),
            allocations=tuple(
                AllocationView.of(allocation) for allocation in reservation.allocations
            ),
            version=reservation.version,
        )

    @property
    def is_active(self) -> bool:
        """Whether the hold still promises stock (status ACTIVE)."""
        return self.status is ReservationStatus.ACTIVE

    @property
    def is_consumed(self) -> bool:
        """Whether the stock was already taken (status CONFIRMED)."""
        return self.status is ReservationStatus.CONFIRMED

    def covered_lines(self) -> dict[UUID, int]:
        """Quantity held per order line, used by ordering's coverage check."""
        covered: dict[UUID, int] = {}
        for allocation in self.allocations:
            if allocation.order_line_id is None:
                continue
            covered[allocation.order_line_id] = (
                covered.get(allocation.order_line_id, 0) + allocation.quantity
            )
        return covered


@dataclass(frozen=True, slots=True)
class ShortfallView:
    """Why stock could not be held, in the caller's language."""

    sku_id: SkuId
    required: int
    allocated: int
    missing: int

    @classmethod
    def of(cls, shortfall: StockShortfall) -> ShortfallView:
        return cls(
            sku_id=shortfall.sku_id,
            required=int(shortfall.required),
            allocated=int(shortfall.allocated),
            missing=int(shortfall.missing),
        )


@dataclass(frozen=True, slots=True)
class ReservationOutcome:
    """What happened to a :class:`ReservationRequest`."""

    reservation: ReservationView | None
    shortfalls: tuple[ShortfallView, ...] = field(default_factory=tuple)
    is_complete: bool = False

    @property
    def has_reservation(self) -> bool:
        return self.reservation is not None

    def covered_lines(self) -> dict[UUID, int]:
        return self.reservation.covered_lines() if self.reservation is not None else {}


@runtime_checkable
class InventoryOperations(Protocol):
    """What other contexts may ask inventory to do (in-process, one transaction).

    Implementations never commit: the caller owns the transaction, so a
    reservation and the order that needs it commit together or not at all.
    """

    async def reserve(
        self, request: ReservationRequest, context: RequestContext
    ) -> ReservationOutcome:
        """Hold stock; ``reservation`` is ``None`` when nothing could be held
        under the ``PARTIAL`` strategy, and :class:`InsufficientInventory` is
        raised under ``ALL_OR_NOTHING``."""
        ...

    async def confirm_reservation(
        self, *, tenant_id: TenantId, reservation_id: ReservationId, context: RequestContext
    ) -> ReservationView:
        """Consume a hold (stock leaves the warehouse)."""
        ...

    async def release_reservation(
        self,
        *,
        tenant_id: TenantId,
        reservation_id: ReservationId,
        reason: str,
        context: RequestContext,
    ) -> ReservationView:
        """Return held stock to the available pool."""
        ...

    async def reservation_for_order(
        self, *, tenant_id: TenantId, order_id: OrderId, context: RequestContext
    ) -> ReservationView | None:
        """The most recent reservation of an order, whatever its state.

        Ordering uses it to render an order and to prove coverage before
        confirming; the reservation itself remains owned by inventory.
        """
        ...


__all__ = [
    "AllocationStrategy",
    "AllocationView",
    "InventoryOperations",
    "ReservationOutcome",
    "ReservationRequest",
    "ReservationView",
    "ShortfallView",
    "StockRequirement",
]
