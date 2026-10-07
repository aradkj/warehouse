"""Domain error hierarchy.

Domain errors describe *violated business rules*, not transport concerns. They
carry a stable, machine-readable ``code`` and structured ``details`` so that the
inbound adapter (FastAPI) can map them onto an HTTP contract without the domain
ever knowing that HTTP exists.

Rules of thumb:

* Domain code raises these; it never raises ``HTTPException``.
* Messages are safe to return to clients: they must never contain SQL, stack
  traces or other internals.
* The same ``code`` must always mean the same thing (see
  ``docs/architecture/error-model.md``).
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for every business-rule violation."""

    code: str = "domain_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = details or {}

    def __str__(self) -> str:
        return self.message


class InvariantViolation(DomainError):
    """An aggregate invariant was about to be broken.

    Raising this means the caller attempted something the business model
    forbids (for example driving available stock below zero).
    """

    code = "invariant_violation"


class CrossTenantAccess(DomainError):
    """An entity belonging to tenant A was used in tenant B's context.

    Cross-tenant references are always invalid; this error is the last line of
    defence behind the repository-level tenant scoping.
    """

    code = "cross_tenant_access"


class InvalidValue(DomainError):
    """A value object was constructed from an invalid representation."""

    code = "invalid_value"


__all__ = [
    "CrossTenantAccess",
    "DomainError",
    "InvalidValue",
    "InvariantViolation",
]
