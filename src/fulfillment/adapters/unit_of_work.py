"""Fulfillment unit of work implementations."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from fulfillment.adapters.in_memory import InMemoryFulfillmentRepository
from fulfillment.ports.repositories import FulfillmentRepository
from fulfillment.ports.unit_of_work import FulfillmentUnitOfWork
from shared.infrastructure.db.idempotency import SqlAlchemyIdempotencyStore
from shared.infrastructure.db.session import SqlAlchemyTransactionScope

_shared_fulfillment_repo = InMemoryFulfillmentRepository()


def get_shared_fulfillment_repository() -> InMemoryFulfillmentRepository:
    return _shared_fulfillment_repo


def reset_shared_fulfillment_repository() -> None:
    _shared_fulfillment_repo._storage.clear()


class SqlAlchemyFulfillmentUnitOfWork(FulfillmentUnitOfWork):
    """Unit of work for fulfillment context participating in SQLAlchemy transaction."""

    __slots__ = ("_scope", "fulfillments", "idempotency")

    def __init__(
        self,
        scope: SqlAlchemyTransactionScope,
        repository: FulfillmentRepository | None = None,
    ) -> None:
        self._scope = scope
        self.fulfillments: FulfillmentRepository = repository or _shared_fulfillment_repo

    async def __aenter__(self) -> Self:
        await self._scope.__aenter__()
        self.idempotency = SqlAlchemyIdempotencyStore(self._scope.session)
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
    "get_shared_fulfillment_repository",
    "reset_shared_fulfillment_repository",
]
