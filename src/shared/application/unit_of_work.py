"""Transaction boundary port.

Use cases own transaction boundaries; repositories never commit. The port below
is the smallest thing an application service needs in order to say "this unit of
work is atomic": enter, commit, roll back.

Each bounded context declares its own richer protocol (for example
``ordering.ports.unit_of_work.OrderingUnitOfWork``) that adds the repositories
that context composes. Use cases therefore depend on *interfaces*, and the
concrete SQLAlchemy implementation lives in ``*/adapters/persistence``.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self, runtime_checkable


@runtime_checkable
class TransactionScope(Protocol):
    """An atomic unit of work over one database transaction."""

    async def __aenter__(self) -> Self:
        """Begin the transaction."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        """Roll back on error; never swallow it.

        Implementations roll back when ``exc`` is not ``None`` and always release
        the connection back to the pool. Returning ``None``/``False`` means the
        exception propagates - use cases must not silently swallow failures.
        """
        ...

    async def commit(self) -> None:
        """Commit the unit of work atomically."""
        ...

    async def rollback(self) -> None:
        """Discard all work performed inside this unit of work."""
        ...


__all__ = ["TransactionScope"]
