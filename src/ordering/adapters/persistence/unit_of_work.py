"""SQLAlchemy unit of work for the ordering context.

This is the composition point of the Phase 1 modular monolith: one transaction
scope, three contexts' adapters attached to it.

    ordering.orders    -> this context's repository
    ordering.catalog   -> catalog's published query adapter
    ordering.inventory -> inventory's published operations adapter

Everything is wired through *ports*; the use cases never learn that catalog and
inventory happen to live in the same process and the same database.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from catalog.adapters.persistence.publication import SqlAlchemyCatalogQuery
from catalog.ports.publication import CatalogQuery
from inventory.adapters.in_process import InProcessInventoryOperations
from inventory.adapters.persistence.unit_of_work import SqlAlchemyInventoryUnitOfWork
from inventory.ports.publication import InventoryOperations
from ordering.adapters.persistence.repositories import SqlAlchemyOrderRepository
from ordering.ports.repositories import OrderRepository
from shared.application.idempotency import IdempotencyStore
from shared.domain.clock import Clock
from shared.infrastructure.db.idempotency import SqlAlchemyIdempotencyStore
from shared.infrastructure.db.session import SqlAlchemyTransactionScope


class SqlAlchemyOrderingUnitOfWork:
    """Transaction-scoped access to the ordering context's collaborators."""

    __slots__ = (
        "_clock",
        "_default_reservation_ttl_seconds",
        "_max_reservation_ttl_seconds",
        "_scope",
        "catalog",
        "idempotency",
        "inventory",
        "orders",
    )

    def __init__(
        self,
        scope: SqlAlchemyTransactionScope,
        *,
        clock: Clock,
        default_reservation_ttl_seconds: int = 1800,
        max_reservation_ttl_seconds: int = 86_400,
    ) -> None:
        self._scope = scope
        self._clock = clock
        self._default_reservation_ttl_seconds = default_reservation_ttl_seconds
        self._max_reservation_ttl_seconds = max_reservation_ttl_seconds

    async def __aenter__(self) -> Self:
        await self._scope.__aenter__()
        session = self._scope.session
        self.orders: OrderRepository = SqlAlchemyOrderRepository(session)
        self.catalog: CatalogQuery = SqlAlchemyCatalogQuery(session)
        scope = self._scope
        self.inventory: InventoryOperations = InProcessInventoryOperations(
            uow_factory=lambda: SqlAlchemyInventoryUnitOfWork(scope),
            clock=self._clock,
            default_ttl_seconds=self._default_reservation_ttl_seconds,
            max_ttl_seconds=self._max_reservation_ttl_seconds,
        )
        self.idempotency: IdempotencyStore = SqlAlchemyIdempotencyStore(session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        return await self._scope.__aexit__(exc_type, exc, tb)

    async def commit(self) -> None:
        """Commit the whole distributed-in-one-transaction change."""
        await self._scope.commit()

    async def rollback(self) -> None:
        await self._scope.rollback()


__all__ = ["SqlAlchemyOrderingUnitOfWork"]
