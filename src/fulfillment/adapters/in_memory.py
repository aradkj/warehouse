"""In-memory fulfillment adapters for isolated unit and application tests."""

from __future__ import annotations

import copy

from fulfillment.domain.fulfillment import Fulfillment
from fulfillment.ports.providers import CarrierShipmentResult, FulfillmentProviderPort
from fulfillment.ports.repositories import FulfillmentRepository
from shared.application.errors import ConcurrencyConflict
from shared.domain.identifiers import FulfillmentId, OrderId, TenantId


class InMemoryFulfillmentRepository(FulfillmentRepository):
    """In-memory store for fulfillment aggregates."""

    def __init__(self) -> None:
        # (tenant_id, fulfillment_id) -> Fulfillment
        self._storage: dict[tuple[TenantId, FulfillmentId], Fulfillment] = {}

    async def add(self, fulfillment: Fulfillment) -> None:
        key = (fulfillment.tenant_id, fulfillment.id)
        self._storage[key] = copy.deepcopy(fulfillment)

    async def get(
        self,
        *,
        tenant_id: TenantId,
        fulfillment_id: FulfillmentId,
    ) -> Fulfillment | None:
        item = self._storage.get((tenant_id, fulfillment_id))
        return copy.deepcopy(item) if item is not None else None

    async def get_for_update(
        self,
        *,
        tenant_id: TenantId,
        fulfillment_id: FulfillmentId,
    ) -> Fulfillment | None:
        return await self.get(tenant_id=tenant_id, fulfillment_id=fulfillment_id)

    async def save(self, fulfillment: Fulfillment, *, expected_version: int) -> None:
        key = (fulfillment.tenant_id, fulfillment.id)
        current = self._storage.get(key)
        if current is not None and current.version != expected_version:
            raise ConcurrencyConflict(
                f"expected version {expected_version} but found {current.version}"
            )
        self._storage[key] = copy.deepcopy(fulfillment)

    async def list_by_order(
        self,
        *,
        tenant_id: TenantId,
        order_id: OrderId,
    ) -> list[Fulfillment]:
        return [
            copy.deepcopy(f)
            for (t_id, _), f in self._storage.items()
            if t_id == tenant_id and f.order_id == order_id
        ]


class MockFulfillmentProvider(FulfillmentProviderPort):
    """Configurable mock external carrier for tests."""

    def __init__(
        self,
        *,
        should_succeed: bool = True,
        is_transient: bool = False,
        carrier_name: str = "INTERNAL",
        error_message: str | None = None,
    ) -> None:
        self.should_succeed = should_succeed
        self.is_transient = is_transient
        self.carrier_name = carrier_name
        self.error_message = error_message
        self.dispatched_fulfillments: list[FulfillmentId] = []

    async def dispatch_shipment(self, fulfillment: Fulfillment) -> CarrierShipmentResult:
        self.dispatched_fulfillments.append(fulfillment.id)
        if self.should_succeed:
            return CarrierShipmentResult(
                success=True,
                tracking_number=f"TRK-{fulfillment.id.hex[:8].upper()}",
                carrier=self.carrier_name,
            )
        return CarrierShipmentResult(
            success=False,
            carrier=self.carrier_name,
            error_message=self.error_message or "Carrier dispatch failed",
            is_transient=self.is_transient,
        )


__all__ = [
    "InMemoryFulfillmentRepository",
    "MockFulfillmentProvider",
]
