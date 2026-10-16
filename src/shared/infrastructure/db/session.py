"""Database session and transaction management.

**Transaction participation** (see ``docs/decisions/0007-transaction-participation.md``)
------------------------------------------------------------------------------
A single request must perform its writes in a single PostgreSQL transaction, even
when the work spans several bounded contexts (Ordering calls Inventory to
reserve stock). The rule implemented here:

* the *outermost* ``async with scope`` opens a session and issues ``BEGIN``;
* a *nested* ``async with scope`` (a cross-context call, or a second use case in
  the same request) participates in the outer transaction;
* ``commit()`` inside a nested scope is a no-op (the owner commits);
* an exception anywhere rolls the whole transaction back and propagates - no
  partial commits, no silently swallowed failures.

This gives the same guarantees as a connection-scoped transaction so that
"reserve stock and create the order" is atomic, while keeping the use-case API
identical whether a use case is called from HTTP or from another context.
"""

from __future__ import annotations

import logging
import uuid
from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

logger = logging.getLogger(__name__)


class Database:
    """Owns the engine and the session factory for one process."""

    __slots__ = ("_engine", "_session_factory")

    def __init__(
        self,
        url: str,
        *,
        pool_size: int = 10,
        max_overflow: int = 10,
        pool_timeout: float = 10.0,
        pool_recycle: int = 1800,
        statement_timeout_ms: int = 15_000,
        echo: bool = False,
    ) -> None:
        connect_args: dict[str, object] = {
            "server_settings": {"application_name": "arad-platform"},
            # Defence in depth: a runaway query cannot pin a connection forever.
            "command_timeout": statement_timeout_ms / 1000.0,
        }
        self._engine = create_async_engine(
            url,
            echo=echo,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout,
            pool_recycle=pool_recycle,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        self._session_factory = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            autoflush=False,
        )

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        return self._session_factory

    async def dispose(self) -> None:
        """Close all pooled connections (used on application shutdown)."""
        await self._engine.dispose()

    async def ping(self) -> None:
        """Raise if the database is unreachable (used by the readiness probe)."""
        from sqlalchemy import text

        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def check_migration_version(self) -> str | None:
        """Query the applied Alembic migration revision or return None."""
        from sqlalchemy import text

        async with self._engine.connect() as connection:
            res = await connection.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            row = res.first()
            if row is not None and row[0]:
                return str(row[0])
            return None

    def get_pool_stats(self) -> tuple[float, float]:
        """Return (pool_size, checked_out_connections)."""
        pool = self._engine.pool
        size_fn = getattr(pool, "size", None)
        checked_fn = getattr(pool, "checkedout", None)
        size = float(size_fn()) if callable(size_fn) else 0.0
        checked = float(checked_fn()) if callable(checked_fn) else 0.0
        return size, checked

    def new_scope(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        client_id: str | None = None,
        bypass_rls: bool = False,
    ) -> SqlAlchemyTransactionScope:
        """Create a fresh, per-request transaction scope."""
        return SqlAlchemyTransactionScope(
            self._session_factory,
            tenant_id=tenant_id,
            user_id=user_id,
            client_id=client_id,
            bypass_rls=bypass_rls,
        )


class RoleSwitchError(RuntimeError):
    """Raised when the session cannot establish the required unprivileged runtime role."""


class SqlAlchemyTransactionScope:
    """A re-entrant, participation-aware unit of work over one ``AsyncSession``.

    The instance is created once per unit of work (per HTTP request typically) and
    may be entered several times; only the outermost entry owns the transaction.
    """

    __slots__ = (
        "_client_id",
        "_depth",
        "_rollback_only",
        "_session",
        "_session_factory",
        "_tenant_id",
        "_user_id",
    )

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        client_id: str | None = None,
        bypass_rls: bool = False,
    ) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._depth = 0
        self._rollback_only = False
        self._tenant_id = str(tenant_id) if tenant_id is not None else None
        self._user_id = str(user_id) if user_id is not None else None
        self._client_id = str(client_id) if client_id is not None else None

    # -- transaction scope protocol --------------------------------------
    async def __aenter__(self) -> Self:
        if self._depth == 0:
            self._session = self._session_factory()
            # ``begin`` issues BEGIN explicitly instead of relying on autobegin,
            # which keeps the transaction boundary visible in the code.
            await self._session.begin()
            self._rollback_only = False
            await self._apply_rls_context()
        self._depth += 1
        return self

    async def _apply_rls_context(self) -> None:
        if self._session is None:
            return
        from sqlalchemy import text
        bind = self._session.bind
        dialect_name = bind.dialect.name if bind is not None else "postgresql"
        if dialect_name != "postgresql":
            return

        # 1. Enforce least-privilege runtime role arad_app.
        # Fail closed: never swallow role-switching errors.
        try:
            await self._session.execute(text("SET LOCAL ROLE arad_app"))
        except Exception as exc:
            logger.error("Failed to enforce arad_app role: %s", exc)
            self._rollback_only = True
            raise RoleSwitchError("Failed to establish secure runtime database role") from exc

        # 2. Establish transaction-local tenant, user, and client context.
        # is_local=true ensures PostgreSQL automatically discards on commit/rollback.
        tid = ""
        if self._tenant_id:
            try:
                uuid.UUID(str(self._tenant_id))
                tid = str(self._tenant_id)
            except ValueError:
                tid = ""
        await self._session.execute(
            text("SELECT set_config('app.current_tenant_id', :tid, true)"),
            {"tid": tid},
        )
        uid = ""
        if self._user_id:
            try:
                uuid.UUID(str(self._user_id))
                uid = str(self._user_id)
            except ValueError:
                uid = ""
        await self._session.execute(
            text("SELECT set_config('app.current_user_id', :uid, true)"),
            {"uid": uid},
        )
        cid = str(self._client_id) if self._client_id is not None else ""
        await self._session.execute(
            text("SELECT set_config('app.current_client_id', :cid, true)"),
            {"cid": cid},
        )

    async def set_tenant_context(
        self,
        tenant_id: object | None,
        *,
        user_id: object | None = None,
        client_id: object | None = None,
    ) -> None:
        """Establish or update transaction-local tenant context for RLS."""
        self._tenant_id = str(tenant_id) if tenant_id is not None else None
        if user_id is not None:
            self._user_id = str(user_id)
        if client_id is not None:
            self._client_id = str(client_id)
        await self._apply_rls_context()

    async def set_bypass_rls(self, bypass: bool = True) -> None:
        """Deprecated: retained for interface compatibility, has no effect on RLS policies."""
        pass

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        self._depth -= 1
        if self._depth == 0 and self._session is not None:
            if exc is not None or self._rollback_only:
                await self._rollback_unconditionally()
            await self._close()
        # Returning None lets the exception propagate: failures are never
        # swallowed by the transaction layer.
        return None

    async def commit(self) -> None:
        if self._rollback_only:
            raise RuntimeError("cannot commit: this unit of work was rolled back")
        if self._depth > 1:
            # Participation: the outermost owner is responsible for COMMIT.
            # Flushing here would be harmless but is unnecessary - the nested
            # and outer code share one session and therefore one view of state.
            return
        if self._session is None:  # pragma: no cover - programming error
            raise RuntimeError("no active transaction to commit")
        await self._session.commit()
        if self._depth > 0:
            await self._session.begin()
            await self._apply_rls_context()

    async def rollback(self) -> None:
        self._rollback_only = True
        if self._session is not None:
            await self._rollback_unconditionally()

    # -- accessors --------------------------------------------------------
    @property
    def session(self) -> AsyncSession:
        """The session bound to this scope (valid only inside a ``with`` block)."""
        if self._session is None:  # pragma: no cover - programming error
            raise RuntimeError("transaction scope has not been entered")
        return self._session

    @property
    def is_participating(self) -> bool:
        """True when this entry is nested inside an outer transaction."""
        return self._depth > 1

    # -- internals --------------------------------------------------------
    async def _rollback_unconditionally(self) -> None:
        if self._session is None:  # pragma: no cover - guarded by callers
            return
        if self._session.in_transaction():
            await self._session.rollback()

    async def _close(self) -> None:
        if self._session is None:  # pragma: no cover - guarded by callers
            return
        await self._session.close()
        self._session = None
        self._depth = 0


__all__ = ["Database", "RoleSwitchError", "SqlAlchemyTransactionScope"]
