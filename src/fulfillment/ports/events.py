"""Published fulfillment integration events for cross-context subscribers."""

from __future__ import annotations

from fulfillment.domain.events import (
    FulfillmentAllocated,
    FulfillmentCompensated,
    FulfillmentCreated,
    FulfillmentFailed,
    FulfillmentShipped,
)

__all__ = [
    "FulfillmentAllocated",
    "FulfillmentCompensated",
    "FulfillmentCreated",
    "FulfillmentFailed",
    "FulfillmentShipped",
]
