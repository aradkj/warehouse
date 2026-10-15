"""Fulfillment external carrier provider ports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from fulfillment.domain.fulfillment import Fulfillment


@dataclass(frozen=True, slots=True)
class CarrierShipmentResult:
    """Outcome of an external carrier dispatch operation."""

    success: bool
    tracking_number: str | None = None
    carrier: str = "INTERNAL"
    error_message: str | None = None
    is_transient: bool = False


@runtime_checkable
class FulfillmentProviderPort(Protocol):
    """Outbound port for communicating with external carriers."""

    async def dispatch_shipment(self, fulfillment: Fulfillment) -> CarrierShipmentResult:
        """Submit a fulfillment shipment to the carrier."""
        ...


__all__ = [
    "CarrierShipmentResult",
    "FulfillmentProviderPort",
]
