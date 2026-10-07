"""Identity commands, queries and views."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from identity.domain.tenant import Tenant, TenantStatus
from shared.application.context import RequestContext
from shared.domain.identifiers import TenantId


@dataclass(frozen=True, slots=True)
class RegisterTenantCommand:
    """Platform-scoped operation: it carries no ``tenant_id`` on purpose.

    Idempotency for provisioning is provided by the unique tenant slug rather
    than by the tenant-scoped idempotency registry (which requires a tenant).
    """

    context: RequestContext
    slug: str
    name: str


@dataclass(frozen=True, slots=True)
class GetTenantQuery:
    context: RequestContext
    tenant_id: TenantId


@dataclass(frozen=True, slots=True)
class TenantView:
    id: TenantId
    slug: str
    name: str
    status: TenantStatus
    created_at: datetime
    updated_at: datetime
    version: int

    @classmethod
    def of(cls, tenant: Tenant) -> TenantView:
        return cls(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            status=tenant.status,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
            version=tenant.version,
        )


__all__ = ["GetTenantQuery", "RegisterTenantCommand", "TenantView"]
