"""Translation of database errors into application errors.

The application layer must never see ``asyncpg.PostgresError`` or
``sqlalchemy.exc.IntegrityError``: those are driver details. Adapters call
:func:`translate_integrity_error` and raise the resulting application error.

Classification is done through SQLSTATE codes rather than driver exception
classes, so swapping the driver would not break the mapping:

* ``23505`` unique violation
* ``23503`` foreign key violation
* ``23514`` check constraint violation
* ``40001`` serialization failure / ``40P01`` deadlock detected
"""

from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.exc import IntegrityError, OperationalError

from shared.application.errors import DuplicateResource, MissingReference

UNIQUE_VIOLATION = "23505"
FOREIGN_KEY_VIOLATION = "23503"
CHECK_VIOLATION = "23514"
SERIALIZATION_FAILURE = "40001"
DEADLOCK_DETECTED = "40P01"


def sqlstate_of(exc: BaseException) -> str | None:
    """Return the SQLSTATE of a wrapped driver error, if available."""
    orig = getattr(exc, "orig", None) or exc
    state = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if state:
        return str(state)
    cause = getattr(orig, "__cause__", None)
    if cause is not None:
        state = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        if state:
            return str(state)
    return None


def constraint_name_of(exc: BaseException) -> str | None:
    """Return the violated constraint name, if the driver exposes it."""
    orig = getattr(exc, "orig", None) or exc
    name = getattr(orig, "constraint_name", None)
    if name:
        return str(name)
    cause = getattr(orig, "__cause__", None)
    if cause is not None:
        name = getattr(cause, "constraint_name", None)
        if name:
            return str(name)
    return None


def translate_integrity_error(
    exc: IntegrityError,
    *,
    duplicates: Mapping[str, tuple[str, str]] | None = None,
) -> Exception:
    """Map an ``IntegrityError`` onto a stable application error.

    ``duplicates`` maps a constraint name to ``(resource_type, field)``, which is
    how a caller learns *what* was duplicated without inspecting SQL.
    """
    state = sqlstate_of(exc)
    constraint = constraint_name_of(exc)
    if state == UNIQUE_VIOLATION:
        if constraint and duplicates and constraint in duplicates:
            resource_type, field = duplicates[constraint]
            return DuplicateResource(resource_type=resource_type, field=field)
        return DuplicateResource(resource_type="resource", field=constraint or "unknown")
    if state == FOREIGN_KEY_VIOLATION:
        return MissingReference(
            "a referenced resource does not exist",
            details={"constraint": constraint} if constraint else {},
        )
    return exc


def is_transient_conflict(exc: BaseException) -> bool:
    """True for errors that PostgreSQL asks the client to retry.

    Phase 1 does not retry automatically (the API returns 409 and the client
    retries), but the classification is centralised here so Phase 2 workers can
    reuse it.
    """
    if isinstance(exc, OperationalError):
        return sqlstate_of(exc) in {SERIALIZATION_FAILURE, DEADLOCK_DETECTED}
    return sqlstate_of(exc) in {SERIALIZATION_FAILURE, DEADLOCK_DETECTED}


__all__ = [
    "CHECK_VIOLATION",
    "DEADLOCK_DETECTED",
    "FOREIGN_KEY_VIOLATION",
    "SERIALIZATION_FAILURE",
    "UNIQUE_VIOLATION",
    "constraint_name_of",
    "is_transient_conflict",
    "sqlstate_of",
    "translate_integrity_error",
]
