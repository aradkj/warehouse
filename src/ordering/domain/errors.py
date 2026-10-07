"""Ordering domain errors."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from shared.domain.errors import DomainError


class InvalidOrder(DomainError):
    """The order could not be constructed as requested."""

    code = "invalid_order"

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = dict(details or {})
        if field is not None:
            merged["field"] = field
        super().__init__(message, details=merged)
        self.field = field


class InvalidOrderTransition(DomainError):
    """The requested order lifecycle transition is not allowed.

    Orders may not move backwards: leaving CONFIRMED requires an explicit
    compensating workflow (Phase 2), which is why it is not simply a transition
    to CANCELLED here.
    """

    code = "invalid_order_transition"

    def __init__(self, current: str, requested: str, *, reason: str | None = None) -> None:
        details: dict[str, Any] = {"from": current, "to": requested}
        if reason is not None:
            details["reason"] = reason
        super().__init__(f"an order in state {current} cannot move to {requested}", details=details)


class OrderLinesNotReserved(DomainError):
    """The order cannot be confirmed because not every line has a valid hold.

    This is the aggregate-level expression of the invariant *"a confirmed order
    must have valid reservations for all required lines"*.
    """

    code = "order_lines_not_reserved"

    def __init__(self, uncovered: Sequence[Mapping[str, Any]]) -> None:
        super().__init__(
            "the order cannot be confirmed: some lines are not fully reserved",
            details={"lines": list(uncovered)},
        )
        self.uncovered = tuple(uncovered)


class DuplicateOrderLine(DomainError):
    """An order may hold at most one line per SKU in Phase 1."""

    code = "duplicate_order_line"


__all__ = [
    "DuplicateOrderLine",
    "InvalidOrder",
    "InvalidOrderTransition",
    "OrderLinesNotReserved",
]
