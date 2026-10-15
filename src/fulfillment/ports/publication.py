"""Fulfillment's published contract and ports.

Phase 2 introduces the fulfillment context: allocation of warehouse work,
split shipments, external provider operations, retries, and compensations.
"""

from __future__ import annotations

from fulfillment.ports.providers import CarrierShipmentResult, FulfillmentProviderPort
from fulfillment.ports.repositories import FulfillmentRepository

#: Phase 2 contract definition.
PLANNED_PHASE = 2

__all__ = [
    "PLANNED_PHASE",
    "CarrierShipmentResult",
    "FulfillmentProviderPort",
    "FulfillmentRepository",
]
