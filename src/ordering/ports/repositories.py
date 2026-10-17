"""Ordering repository ports."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ordering.domain.order import Order
from ordering.domain.value_objects import OrderStatus
from shared.application.pagination import Page
from shared.domain.identifiers import OrderId, TenantId


@runtime_checkable
class OrderRepository(Protocol):
    """Persistence port for the :class:`ordering.domain.order.Order` aggregate."""

    async def add(self, order: Order) -> None:
        """Insert an order together with its lines."""
        ...

    async def get(self, *, tenant_id: TenantId, order_id: OrderId) -> Order | None: ...

    async def get_for_update(self, *, tenant_id: TenantId, order_id: OrderId) -> Order | None:
        """Lock one order (mandatory before any state transition)."""
        ...

    async def save(self, order: Order, *, expected_version: int) -> None:
        """Update an order with an optimistic-concurrency guard."""
        ...

    async def page(
        self,
        *,
        tenant_id: TenantId,
        limit: int,
        cursor: str | None = None,
        status: OrderStatus | None = None,
    ) -> Page[Order]: ...


__all__ = ["OrderRepository", "OrderStatus"]
