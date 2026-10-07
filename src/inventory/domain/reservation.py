"""The Reservation aggregate: a time-bounded hold on stock.

A reservation is what stands between "a channel wants to buy" and "stock left the
warehouse". It holds quantities on specific inventory items, possibly in several
warehouses, and it has an expiry so that an abandoned checkout cannot lock stock
forever.

Lifecycle (terminal states have no outgoing transitions):

    ACTIVE --confirm--> CONFIRMED
    ACTIVE --release--> RELEASED
    ACTIVE --expire---> EXPIRED

``confirm`` is refused when the reservation is no longer ACTIVE **and** when the
hold has already passed ``expires_at``: a hold that expired is not a promise,
even if the Phase 2 sweeper has not marked it EXPIRED yet. That is the invariant
"released or expired reservations must not be confirmable", enforced by the
aggregate rather than by a scheduled job.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from uuid import UUID

from inventory.domain.errors import (
    InvalidReservation,
    InvalidReservationTransition,
    ReservationExpired,
)
from inventory.domain.events import (
    ReservationConfirmed,
    ReservationCreated,
    ReservationReleased,
)
from inventory.domain.events import (
    ReservationExpired as ReservationExpiredEvent,
)
from inventory.domain.value_objects import ReservationAllocation, ReservationStatus
from shared.domain.aggregate import AggregateRoot
from shared.domain.errors import InvariantViolation
from shared.domain.identifiers import (
    InventoryItemId,
    OrderId,
    OrderLineId,
    ReservationId,
    TenantId,
    WarehouseId,
    new_id,
)
from shared.domain.value_objects import Quantity


class Reservation(AggregateRoot):
    """Reservation aggregate root."""

    __slots__ = (
        "_allocations",
        "_confirmed_at",
        "_created_at",
        "_expires_at",
        "_id",
        "_order_id",
        "_release_reason",
        "_released_at",
        "_status",
        "_updated_at",
    )

    def __init__(
        self,
        *,
        id: ReservationId,
        tenant_id: TenantId,
        order_id: OrderId | None,
        status: ReservationStatus,
        expires_at: datetime,
        allocations: Sequence[ReservationAllocation],
        created_at: datetime,
        updated_at: datetime,
        confirmed_at: datetime | None = None,
        released_at: datetime | None = None,
        release_reason: str | None = None,
        version: int = 0,
    ) -> None:
        super().__init__(tenant_id=tenant_id, version=version)
        if not allocations:
            raise InvalidReservation("a reservation must hold at least one allocation")
        if expires_at.tzinfo is None:
            raise InvalidReservation("reservation expiry must be timezone-aware")
        self._id = id
        self._order_id = order_id
        self._status = status
        self._expires_at = expires_at
        self._allocations = list(allocations)
        self._created_at = created_at
        self._updated_at = updated_at
        self._confirmed_at = confirmed_at
        self._released_at = released_at
        self._release_reason = release_reason

    # -- creation ----------------------------------------------------------
    @classmethod
    def create(
        cls,
        *,
        tenant_id: TenantId,
        order_id: OrderId | None,
        allocations: Sequence[ReservationAllocation],
        expires_at: datetime,
        now: datetime,
    ) -> Reservation:
        if expires_at <= now:
            raise InvalidReservation(
                "a reservation must expire in the future",
                details={"expires_at": expires_at.isoformat(), "now": now.isoformat()},
            )
        reservation_id = ReservationId(new_id())
        reservation = cls(
            id=reservation_id,
            tenant_id=tenant_id,
            order_id=order_id,
            status=ReservationStatus.ACTIVE,
            expires_at=expires_at,
            allocations=allocations,
            created_at=now,
            updated_at=now,
            version=0,
        )
        reservation._record_event(
            ReservationCreated(
                tenant_id=tenant_id,
                reservation_id=reservation_id,
                order_id=order_id,
                expires_at=expires_at.isoformat(),
                allocated_quantity=int(reservation.total_quantity()),
                allocation_count=len(reservation._allocations),
            )
        )
        return reservation

    # -- state -------------------------------------------------------------
    @property
    def id(self) -> ReservationId:
        return self._id

    @property
    def order_id(self) -> OrderId | None:
        return self._order_id

    @property
    def status(self) -> ReservationStatus:
        return self._status

    @property
    def expires_at(self) -> datetime:
        return self._expires_at

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def confirmed_at(self) -> datetime | None:
        return self._confirmed_at

    @property
    def released_at(self) -> datetime | None:
        return self._released_at

    @property
    def release_reason(self) -> str | None:
        return self._release_reason

    @property
    def allocations(self) -> tuple[ReservationAllocation, ...]:
        return tuple(self._allocations)

    def total_quantity(self) -> Quantity:
        return Quantity(sum(allocation.quantity.value for allocation in self._allocations))

    def quantities_by_item(self) -> Mapping[InventoryItemId, Quantity]:
        """How much this reservation holds on each inventory item."""
        totals: dict[InventoryItemId, int] = {}
        for allocation in self._allocations:
            totals[allocation.inventory_item_id] = (
                totals.get(allocation.inventory_item_id, 0) + allocation.quantity.value
            )
        return {item_id: Quantity(quantity) for item_id, quantity in totals.items()}

    def warehouse_ids(self) -> tuple[WarehouseId, ...]:
        seen: list[WarehouseId] = []
        for allocation in self._allocations:
            if allocation.warehouse_id not in seen:
                seen.append(allocation.warehouse_id)
        return tuple(seen)

    def covered_lines(self) -> dict[UUID, int]:
        """Quantity held per order line (keyed by the order line identifier)."""
        covered: dict[UUID, int] = {}
        for allocation in self._allocations:
            if allocation.order_line_id is None:
                continue
            covered[allocation.order_line_id] = (
                covered.get(allocation.order_line_id, 0) + allocation.quantity.value
            )
        return covered

    def covered_quantity_for_line(self, order_line_id: OrderLineId) -> Quantity:
        return Quantity(
            sum(
                allocation.quantity.value
                for allocation in self._allocations
                if allocation.order_line_id == order_line_id
            )
        )

    def is_active(self, *, now: datetime) -> bool:
        """Active *and* not past its expiry instant."""
        return self._status is ReservationStatus.ACTIVE and now < self._expires_at

    def is_expired_at(self, now: datetime) -> bool:
        return self._status is ReservationStatus.ACTIVE and now >= self._expires_at

    # -- behaviour ---------------------------------------------------------
    def confirm(self, *, now: datetime) -> None:
        """Consume the hold (the order is being confirmed).

        Refuses when the reservation is not ACTIVE, and refuses when the hold has
        expired even if no sweeper has marked it yet.
        """
        if self._status is ReservationStatus.ACTIVE and now >= self._expires_at:
            raise ReservationExpired(
                "the reservation expired and can no longer be confirmed",
                details={
                    "reservation_id": str(self._id),
                    "expires_at": self._expires_at.isoformat(),
                    "now": now.isoformat(),
                },
            )
        self._require_status(ReservationStatus.CONFIRMED)
        self._status = ReservationStatus.CONFIRMED
        self._confirmed_at = now
        self._updated_at = now
        self._record_event(
            ReservationConfirmed(
                tenant_id=self.tenant_id,
                reservation_id=self._id,
                order_id=self._order_id,
                allocated_quantity=int(self.total_quantity()),
            )
        )

    def release(self, *, reason: str, now: datetime) -> None:
        """Give the held stock back (cancellation, manual release)."""
        self._require_status(ReservationStatus.RELEASED)
        self._status = ReservationStatus.RELEASED
        self._released_at = now
        self._release_reason = reason
        self._updated_at = now
        self._record_event(
            ReservationReleased(
                tenant_id=self.tenant_id,
                reservation_id=self._id,
                order_id=self._order_id,
                allocated_quantity=int(self.total_quantity()),
                reason=reason,
            )
        )

    def expire(self, *, now: datetime) -> None:
        """Mark a timed-out hold as EXPIRED.

        Only legal once the expiry instant has passed: expiring early would
        silently break a promise the platform made to a customer.
        """
        if now < self._expires_at:
            raise InvariantViolation(
                "a reservation cannot be expired before its expiry instant",
                details={
                    "reservation_id": str(self._id),
                    "expires_at": self._expires_at.isoformat(),
                    "now": now.isoformat(),
                },
            )
        self._require_status(ReservationStatus.EXPIRED)
        self._status = ReservationStatus.EXPIRED
        self._updated_at = now
        self._record_event(
            ReservationExpiredEvent(
                tenant_id=self.tenant_id,
                reservation_id=self._id,
                order_id=self._order_id,
                allocated_quantity=int(self.total_quantity()),
            )
        )

    # -- internals ---------------------------------------------------------
    def _require_status(self, target: ReservationStatus) -> None:
        if self._status is not ReservationStatus.ACTIVE:
            raise InvalidReservationTransition(self._status.value, target.value)
        if self._status is target:  # pragma: no cover - unreachable from ACTIVE
            raise InvalidReservationTransition(self._status.value, target.value)


__all__ = ["Reservation"]
