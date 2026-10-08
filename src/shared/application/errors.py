"""Application-layer error hierarchy.

Application errors describe failures of *orchestration* rather than violated
business rules: a missing aggregate, a lost optimistic-concurrency race, a
transaction that could not be committed. They carry a stable ``code`` and
structured ``details`` exactly like domain errors so the HTTP layer can map both
families with one mechanism (see ``bootstrap/errors.py``).
"""

from __future__ import annotations

from typing import Any


class ApplicationError(Exception):
    """Base class for every application-layer failure."""

    code: str = "application_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def __str__(self) -> str:
        return self.message


class NotFound(ApplicationError):
    """The requested resource does not exist *within the caller's tenant*.

    Tenant scoping happens in the repository, so a resource owned by another
    tenant is reported as "not found" rather than "forbidden": the API must not
    leak the existence of other tenants' data.
    """

    #: Overridden per instance so clients see ``order_not_found``,
    #: ``reservation_not_found``, ... rather than a generic code.
    code = "not_found"

    def __init__(
        self, resource_type: str, resource_id: Any, *, details: dict[str, Any] | None = None
    ) -> None:
        merged: dict[str, Any] = {
            "resource_type": resource_type,
            "resource_id": str(resource_id),
        }
        merged.update(details or {})
        super().__init__(f"{resource_type} {resource_id} was not found", details=merged)
        self.code = f"{resource_type}_not_found"
        self.resource_type = resource_type
        self.resource_id = str(resource_id)


class InvalidRequest(ApplicationError):
    """The request is structurally valid but semantically unusable."""

    code = "invalid_request"


class ConcurrencyConflict(ApplicationError):
    """An optimistic-concurrency check or a row lock revealed a conflicting write.

    Raised when an ``UPDATE ... WHERE version = :expected`` affects zero rows, or
    when a caller supplied a stale ``If-Match`` version.
    """

    code = "concurrency_conflict"


class DuplicateIdempotencyKey(ApplicationError):
    """The same ``(tenant, Idempotency-Key)`` was reused for a different payload."""

    code = "idempotency_key_reused"


class DuplicateResource(ApplicationError):
    """A uniqueness constraint was violated.

    Raised by repository adapters (which inspect the driver's SQLSTATE) so that
    application code never has to catch ``IntegrityError`` or parse SQL. It is
    raised *by the database*, not by a pre-flight ``SELECT``: a check-then-insert
    is a race, a unique index is not.
    """

    code = "duplicate_resource"

    def __init__(self, *, resource_type: str, field: str) -> None:
        super().__init__(
            f"a {resource_type} with the same {field} already exists",
            details={"resource_type": resource_type, "field": field},
        )
        self.resource_type = resource_type
        self.field = field


class MissingReference(ApplicationError):
    """A foreign key referenced a row that does not exist.

    Reaching this error means a referential check was missing earlier in the use
    case (for example a SKU that was deleted between two statements). It is kept
    as a defensive translation instead of leaking a driver exception.
    """

    code = "referenced_resource_missing"


class IdempotencyOperationInProgress(ApplicationError):
    """A concurrent request with the same idempotency key is still running.

    The client should retry with the same key: once the first request commits,
    the retry replays the stored response instead of repeating the operation.
    """

    code = "idempotency_in_progress"


class IdempotencyKeyRequired(ApplicationError):
    """A mutation endpoint configured to require an idempotency key received none."""

    code = "idempotency_key_required"


class SearchUnavailable(ApplicationError):
    """The search backend is temporarily unavailable or failing health probes."""

    code = "search_unavailable"


__all__ = [
    "ApplicationError",
    "ConcurrencyConflict",
    "DuplicateIdempotencyKey",
    "DuplicateResource",
    "IdempotencyKeyRequired",
    "IdempotencyOperationInProgress",
    "InvalidRequest",
    "MissingReference",
    "NotFound",
    "SearchUnavailable",
]
