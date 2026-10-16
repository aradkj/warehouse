"""Fulfillment domain errors."""

from __future__ import annotations


class FulfillmentError(Exception):
    """Base class for fulfillment errors."""


class InvalidFulfillment(FulfillmentError):
    """Raised when fulfillment creation parameters are invalid."""


class InvalidFulfillmentTransition(FulfillmentError):
    """Raised when an illegal lifecycle transition is attempted."""

    def __init__(self, current_status: str, target_status: str) -> None:
        super().__init__(f"cannot transition fulfillment from {current_status} to {target_status}")
        self.current_status = current_status
        self.target_status = target_status


class FulfillmentNotFound(FulfillmentError):
    """Raised when a fulfillment aggregate cannot be found."""


class CarrierError(FulfillmentError):
    """Base error for external carrier communication failures."""


class CarrierTemporaryError(CarrierError):
    """Transient carrier communication failure (retryable with backoff)."""


class CarrierPermanentError(CarrierError):
    """Permanent carrier failure (non-retryable, triggers compensation)."""


class CompensationError(FulfillmentError):
    """Raised when compensation workflow encounters an invalid state."""


__all__ = [
    "CarrierError",
    "CarrierPermanentError",
    "CarrierTemporaryError",
    "CompensationError",
    "FulfillmentError",
    "FulfillmentNotFound",
    "InvalidFulfillment",
    "InvalidFulfillmentTransition",
]
