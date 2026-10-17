"""Fulfillment unit of work implementations."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from fulfillment.adapters.persistence.repositories import (
    SqlAlchemyFulfillmentRepository,
)
from fulfillment.ports.repositories import FulfillmentRepository
from fulfillment.ports.unit_of_work import FulfillmentUnitOfWork
from inventory.adapters.persistence.repositories import SqlAlchemyWarehouseRepository
from inventory.ports.repositories import WarehouseRepository
from ordering.adapters.persistence.repositories import SqlAlchemyOrderRepository
from ordering.ports.repositories import OrderRepository
from shared.infrastructure.db.idempotency import SqlAlchemyIdempotencyStore
from shared.infrastructure.db.session import SqlAlchemyTransactionScope


class SqlAlchemyFulfillmentUnitOfWork(FulfillmentUnitOfWork):
    """Unit of work for fulfillment context participating in SQLAlchemy transaction."""

    __slots__ = (
        "_explicit_orders",
        "_explicit_repo",
        "_explicit_warehouses",
        "_scope",
        "fulfillments",
        "idempotency",
        "orders",
        "warehouses",
    )

    def __init__(
        self,
        scope: SqlAlchemyTransactionScope,
        repository: FulfillmentRepository | None = None,
        orders: OrderRepository | None = None,
        warehouses: WarehouseRepository | None = None,
    ) -> None:
        self._scope = scope
        self._explicit_repo = repository
        self._explicit_orders = orders
        self._explicit_warehouses = warehouses

    async def __aenter__(self) -> Self:
        await self._scope.__aenter__()
        session = self._scope.session
        self.fulfillments: FulfillmentRepository = (
            self._explicit_repo or SqlAlchemyFulfillmentRepository(session)
        )
        self.orders: OrderRepository = self._explicit_orders or SqlAlchemyOrderRepository(session)
        self.warehouses: WarehouseRepository = (
            self._explicit_warehouses or SqlAlchemyWarehouseRepository(session)
        )
        self.idempotency = SqlAlchemyIdempotencyStore(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        return await self._scope.__aexit__(exc_type, exc, tb)

    async def commit(self) -> None:
        await self._scope.commit()

    async def rollback(self) -> None:
        await self._scope.rollback()


__all__ = [
    "SqlAlchemyFulfillmentUnitOfWork",
]
