"""Inventory domain errors.

Every error carries a stable ``code`` (used verbatim by the HTTP layer, see
``bootstrap/errors.py``) and structured ``details`` written for an API consumer:
an ``insufficient_inventory`` response names each short SKU with the required,
allocated and missing quantities, so a channel can react without a second call.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from shared.domain.errors import DomainError


class InventoryError(DomainError):
    """Base class for inventory rule violations."""


class InsufficientInventory(InventoryError):
    """A reservation could not be satisfied from available stock.

    ``shortfalls`` is the allocation policy's report: one :class:`Shortfall`-shaped
    entry per SKU that could not be fully covered. Under ``ALL_OR_NOTHING`` the
    error means *nothing* was held; under ``PARTIAL`` it means the caller received
    a reservation for the fully coverable lines only (partial holds are never
    created, because a half-covered line could never be confirmed exactly).
    """

    code = "insufficient_inventory"

    def __init__(self, shortfalls: Sequence[object], *, message: str | None = None) -> None:
        rendered = [_render_shortfall(shortfall) for shortfall in shortfalls]
        super().__init__(
            message or "the requested quantities are not available",
            details={"shortfalls": rendered},
        )
        self.shortfalls = tuple(shortfalls)


class InvalidAdjustment(InventoryError):
    """The requested stock adjustment is not a legal movement."""

    code = "invalid_adjustment"


class StockAdjustmentBelowReserved(InventoryError):
    """An adjustment would leave less physical stock than is already promised."""

    code = "stock_adjustment_below_reserved"


class InvalidReservation(InventoryError):
    """A reservation was built or changed in a way its invariants forbid."""

    code = "invalid_reservation"


class InvalidReservationTransition(InventoryError):
    """A reservation state change the lifecycle does not allow.

    The platform never moves a reservation backwards (``CONFIRMED`` -> ``ACTIVE``,
    ``RELEASED`` -> ``CONFIRMED``): returning the same stock twice, or confirming
    a released hold, is exactly the class of bug that oversells.
    """

    code = "invalid_reservation_transition"

    def __init__(self, current: str, requested: str, *, reason: str | None = None) -> None:
        details: dict[str, Any] = {"current_status": current, "requested_status": requested}
        if reason is not None:
            details["reason"] = reason
        super().__init__(
            f"a reservation in state {current} cannot move to {requested}", details=details
        )


class ReservationExpired(InventoryError):
    """The reservation's hold has timed out and can no longer be consumed."""

    code = "reservation_expired"


class WarehouseNotActive(InventoryError):
    """A stock operation was attempted against a deactivated warehouse."""

    code = "warehouse_not_active"


class InvalidWarehouseTransition(InventoryError):
    """A warehouse state change the lifecycle does not allow."""

    code = "invalid_warehouse_transition"

    def __init__(self, current: str, requested: str) -> None:
        super().__init__(
            f"a warehouse in state {current} cannot move to {requested}",
            details={"current_status": current, "requested_status": requested},
        )


def _render_shortfall(shortfall: object) -> dict[str, Any]:
    """Render a shortfall for the wire without leaking domain objects.

    The inventory shortfall type is a plain dataclass; a defensive ``getattr``
    keeps this module free of a circular import at type level while still
    producing a fully structured payload.
    """
    sku_id = getattr(shortfall, "sku_id", None)
    required = getattr(shortfall, "required", None)
    allocated = getattr(shortfall, "allocated", None)
    missing = getattr(shortfall, "missing", None)
    return {
        "sku_id": str(sku_id) if sku_id is not None else None,
        "required": int(required) if required is not None else None,
        "allocated": int(allocated) if allocated is not None else None,
        "missing": int(missing) if missing is not None else None,
    }


__all__ = [
    "InsufficientInventory",
    "InvalidAdjustment",
    "InvalidReservation",
    "InvalidReservationTransition",
    "InvalidWarehouseTransition",
    "InventoryError",
    "ReservationExpired",
    "StockAdjustmentBelowReserved",
    "WarehouseNotActive",
]
