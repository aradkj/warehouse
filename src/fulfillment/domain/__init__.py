"""Fulfillment domain package."""

from fulfillment.domain.errors import (
    CarrierError,
    CarrierPermanentError,
    CarrierTemporaryError,
    CompensationError,
    FulfillmentError,
    FulfillmentNotFound,
    InvalidFulfillment,
    InvalidFulfillmentTransition,
)
from fulfillment.domain.events import (
    FulfillmentAllocated,
    FulfillmentCompensated,
    FulfillmentCreated,
    FulfillmentFailed,
    FulfillmentShipped,
)
from fulfillment.domain.fulfillment import Fulfillment
from fulfillment.domain.value_objects import (
    Carrier,
    FulfillmentStatus,
    FulfillmentTask,
)

__all__ = [
    "Carrier",
    "CarrierError",
    "CarrierPermanentError",
    "CarrierTemporaryError",
    "CompensationError",
    "Fulfillment",
    "FulfillmentAllocated",
    "FulfillmentCompensated",
    "FulfillmentCreated",
    "FulfillmentError",
    "FulfillmentFailed",
    "FulfillmentNotFound",
    "FulfillmentShipped",
    "FulfillmentStatus",
    "FulfillmentTask",
    "InvalidFulfillment",
    "InvalidFulfillmentTransition",
]
