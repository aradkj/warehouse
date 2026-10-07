"""SQLAlchemy unit of work for the identity context.

The unit of work wraps the shared, participation-aware transaction scope and
exposes the repositories that must share its session.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from identity.adapters.persistence.repositories import SqlAlchemyTenantRepository
from identity.ports.repositories import TenantRepository
from shared.application.idempotency import IdempotencyStore
from shared.infrastructure.db.idempotency import SqlAlchemyIdempotencyStore
from shared.infrastructure.db.session import SqlAlchemyTransactionScope


class SqlAlchemyIdentityUnitOfWork:
    """Transaction-scoped access to identity repositories."""

    __slots__ = ("_scope", "idempotency", "tenants")

    def __init__(self, scope: SqlAlchemyTransactionScope) -> None:
        self._scope = scope

    async def __aenter__(self) -> Self:
        await self._scope.__aenter__()
        session = self._scope.session
        self.tenants: TenantRepository = SqlAlchemyTenantRepository(session)
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


__all__ = ["SqlAlchemyIdentityUnitOfWork"]
