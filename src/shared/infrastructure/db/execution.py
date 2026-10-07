"""Helpers that keep repository code small and honest.

Two concerns recur in every adapter:

* translating driver-level ``IntegrityError`` into application errors, and
* guarding updates with optimistic concurrency and reporting a conflict instead
  of silently overwriting somebody else's write.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Update, UpdateBase
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from shared.application.errors import ConcurrencyConflict
from shared.infrastructure.db.errors import translate_integrity_error


async def execute(
    session: AsyncSession,
    statement: Any,
    *,
    duplicates: Mapping[str, tuple[str, str]] | None = None,
) -> Any:
    """Execute a statement, translating integrity violations.

    ``duplicates`` maps constraint names to ``(resource_type, field)`` so the
    resulting :class:`~shared.application.errors.DuplicateResource` is meaningful.
    """
    try:
        return await session.execute(statement)
    except IntegrityError as exc:
        raise translate_integrity_error(exc, duplicates=duplicates) from exc


async def execute_guarded_update(
    session: AsyncSession,
    statement: Update,
    *,
    resource_type: str,
    resource_id: Any,
    expected_version: int,
) -> None:
    """Run an ``UPDATE`` and fail loudly when the row changed under us.

    ``optimistic concurrency in SQL``:

        UPDATE <table> SET ..., version = :expected + 1
         WHERE id = :id AND tenant_id = :tenant AND version = :expected

    Zero affected rows means one of three things: the row is gone, it belongs to
    another tenant, or another transaction bumped the version first. All three
    are reported as ``ConcurrencyConflict`` - the caller must reload and retry
    with fresh state rather than overwrite a concurrent write.
    """
    result = await execute(session, statement)
    if result.rowcount != 1:
        raise ConcurrencyConflict(
            f"{resource_type} was modified by another transaction",
            details={
                "resource_type": resource_type,
                "resource_id": str(resource_id),
                "expected_version": expected_version,
            },
        )


__all__ = ["UpdateBase", "execute", "execute_guarded_update"]
