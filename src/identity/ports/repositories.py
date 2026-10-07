"""Identity repository ports."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from identity.domain.tenant import Tenant
from shared.domain.identifiers import TenantId


@runtime_checkable
class TenantRepository(Protocol):
    """Persistence port for the :class:`identity.domain.tenant.Tenant` aggregate."""

    async def add(self, tenant: Tenant) -> None:
        """Insert a tenant (raises ``DuplicateResource`` on a duplicate slug)."""
        ...

    async def get(self, tenant_id: TenantId) -> Tenant | None:
        """Load a tenant by id."""
        ...

    async def get_by_slug(self, slug: str) -> Tenant | None:
        """Load a tenant by its unique slug."""
        ...

    async def save(self, tenant: Tenant, *, expected_version: int) -> None:
        """Update a tenant with an optimistic-concurrency guard."""
        ...


__all__ = ["TenantRepository"]
