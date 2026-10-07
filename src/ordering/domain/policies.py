"""Pure ordering policies.

Nothing here touches I/O: these helpers translate a data structure that arrives
from another context (inventory's coverage report) into the shape the aggregate
requires, so no application service has to manipulate identifiers by hand.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from uuid import UUID

from ordering.domain.value_objects import OrderStatus
from shared.domain.identifiers import OrderLineId


class UnavailableStockPolicy(StrEnum):
    """What order submission does when stock cannot (fully) be reserved.

    Both outcomes are required by the specification ("failure causes the order to
    be rejected **or** placed into a configurable back-order state"); the choice
    is configuration, not code.

    ``BACKORDER`` accepts the order and records the shortage; a later confirmation
    can still secure the stock.
    ``REJECT``    refuses the submission outright: no order is created and any
    partial hold is rolled back with the transaction.
    """

    BACKORDER = "BACKORDER"
    REJECT = "REJECT"


def coverage_by_line(covered: Mapping[UUID, int]) -> dict[OrderLineId, int]:
    """Convert inventory's ``{order_line_id: quantity}`` report into domain keys."""
    return {OrderLineId(line_id): quantity for line_id, quantity in covered.items()}


def is_cancellable(status: OrderStatus) -> bool:
    """Whether Phase 1 may cancel an order in this state."""
    return status in (
        OrderStatus.PENDING,
        OrderStatus.BACKORDERED,
        OrderStatus.INVENTORY_RESERVED,
    )


def is_confirmed(status: OrderStatus) -> bool:
    return status is OrderStatus.CONFIRMED


__all__ = ["UnavailableStockPolicy", "coverage_by_line", "is_cancellable", "is_confirmed"]
