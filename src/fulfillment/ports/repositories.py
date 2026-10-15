"""Fulfillment repository ports."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fulfillment.domain.fulfillment import Fulfillment
from shared.domain.identifiers import FulfillmentId, OrderId, TenantId


@runtime_checkable
class FulfillmentRepository(Protocol):
    """Persistence port for Fulfillment aggregate."""

    async def add(self, fulfillment: Fulfillment) -> None:
        """Insert a new fulfillment with tasks."""
        ...

    async def get(
        self,
        *,
        tenant_id: TenantId,
        fulfillment_id: FulfillmentId,
    ) -> Fulfillment | None:
        """Fetch a fulfillment by ID."""
        ...

    async def get_for_update(
        self,
        *,
        tenant_id: TenantId,
        fulfillment_id: FulfillmentId,
    ) -> Fulfillment | None:
        """Fetch and lock a fulfillment for state transitions."""
        ...

    async def save(self, fulfillment: Fulfillment, *, expected_version: int) -> None:
        """Save fulfillment changes with optimistic concurrency check."""
        ...

    async def list_by_order(
        self,
        *,
        tenant_id: TenantId,
        order_id: OrderId,
    ) -> list[Fulfillment]:
        """List fulfillments belonging to an order."""
        ...


__all__ = ["FulfillmentRepository"]
