"""Request context: who is calling, for which tenant, correlated how.

Phase 1 does **not** implement authentication (that is Phase 4). It does,
however, implement the *foundation* every later phase needs:

* ``tenant_id`` is resolved once, at the edge, and passed explicitly into the
  application layer. Repositories take it as a required argument, which is what
  makes cross-tenant access impossible to write by accident. It is ``None`` only
  for platform-scoped operations (tenant provisioning); every tenant-scoped
  command carries its tenant explicitly as a non-optional field.
* ``correlation_id`` travels from the inbound request through the application
  layer into logs and (from Phase 2 on) into the transactional outbox envelope.
* ``actor`` is recorded but not yet trusted for authorization decisions.
* ``idempotency_key`` is lifted from the ``Idempotency-Key`` header so that use
  cases receive it as part of the command context instead of reaching into HTTP.

The context is immutable: a use case cannot silently switch tenant mid-flight.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from shared.domain.identifiers import TenantId


class ActorType(StrEnum):
    """Kind of principal that issued the request."""

    USER = "user"
    SERVICE_ACCOUNT = "service_account"
    SALES_CHANNEL = "sales_channel"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Immutable per-request context handed to every use case."""

    #: Tenant the request acts on. ``None`` only for platform-scoped operations
    #: (for example tenant provisioning). The HTTP layer refuses tenant-scoped
    #: requests without a tenant, and repositories always require one.
    tenant_id: TenantId | None = None
    #: Opaque actor reference. Until Phase 4 the value comes from a header and
    #: is used for audit/correlation only - never for authorization decisions.
    actor_id: str = "unknown"
    actor_type: ActorType = ActorType.SALES_CHANNEL
    principal: Any | None = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    causation_id: str | None = None
    idempotency_key: str | None = None
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def scoped_to(self, tenant_id: TenantId) -> RequestContext:
        """Return a copy of this context bound to a tenant."""
        import dataclasses

        return dataclasses.replace(self, tenant_id=tenant_id)

    def require_tenant(self) -> TenantId:
        """Return the tenant, refusing platform-scoped contexts.

        Used by infrastructure/wiring code, never by the domain: the domain never
        sees a :class:`RequestContext`.
        """
        if self.tenant_id is None:
            raise ValueError("this operation requires a tenant context")
        return self.tenant_id

    def child(self, **overrides: object) -> RequestContext:
        """Derive a context (e.g. for a nested use-case call in the same request)."""
        import dataclasses

        return dataclasses.replace(self, **overrides)  # type: ignore[arg-type]


__all__ = ["ActorType", "RequestContext"]
