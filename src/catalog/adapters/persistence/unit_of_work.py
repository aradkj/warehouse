"""SQLAlchemy unit of work for the catalog context."""

from __future__ import annotations

from types import TracebackType
from typing import Self

from catalog.adapters.persistence.repositories import (
    SqlAlchemyProductRepository,
    SqlAlchemySkuRepository,
)
from catalog.ports.repositories import ProductRepository, SkuRepository
from shared.application.idempotency import IdempotencyStore
from shared.infrastructure.db.idempotency import SqlAlchemyIdempotencyStore
from shared.infrastructure.db.session import SqlAlchemyTransactionScope


class SqlAlchemyCatalogUnitOfWork:
    """Transaction-scoped access to catalog repositories."""

    __slots__ = ("_scope", "idempotency", "products", "skus")

    def __init__(self, scope: SqlAlchemyTransactionScope) -> None:
        self._scope = scope

    async def __aenter__(self) -> Self:
        await self._scope.__aenter__()
        session = self._scope.session
        self.products: ProductRepository = SqlAlchemyProductRepository(session)
        self.skus: SkuRepository = SqlAlchemySkuRepository(session)
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
        await self._scope.commit()

    async def rollback(self) -> None:
        await self._scope.rollback()


__all__ = ["SqlAlchemyCatalogUnitOfWork"]
