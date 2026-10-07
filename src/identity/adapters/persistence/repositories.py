"""SQLAlchemy implementation of :class:`identity.ports.repositories.TenantRepository`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from identity.adapters.persistence.tables import tenants
from identity.domain.tenant import Tenant, TenantStatus
from shared.domain.identifiers import TenantId
from shared.infrastructure.db.execution import execute, execute_guarded_update

_DUPLICATE_CONSTRAINTS: Mapping[str, tuple[str, str]] = {
    "uq_tenants_slug": ("tenant", "slug"),
}


class SqlAlchemyTenantRepository:
    """Tenant persistence adapter."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, tenant: Tenant) -> None:
        await execute(
            self._session,
            insert(tenants).values(**_insert_values(tenant)),
            duplicates=_DUPLICATE_CONSTRAINTS,
        )

    async def get(self, tenant_id: TenantId) -> Tenant | None:
        result = await self._session.execute(select(tenants).where(tenants.c.id == tenant_id))
        row = result.mappings().first()
        return _to_domain(row) if row is not None else None

    async def get_by_slug(self, slug: str) -> Tenant | None:
        result = await self._session.execute(select(tenants).where(tenants.c.slug == slug))
        row = result.mappings().first()
        return _to_domain(row) if row is not None else None

    async def save(self, tenant: Tenant, *, expected_version: int) -> None:
        statement = (
            update(tenants)
            .where(
                tenants.c.id == tenant.id,
                tenants.c.version == expected_version,
            )
            .values(**_update_values(tenant), version=expected_version + 1)
        )
        await execute_guarded_update(
            self._session,
            statement,
            resource_type="tenant",
            resource_id=tenant.id,
            expected_version=expected_version,
        )


def _insert_values(tenant: Tenant) -> dict[str, Any]:
    return {
        "id": tenant.id,
        "slug": tenant.slug,
        "name": tenant.name,
        "status": tenant.status.value,
        "created_at": tenant.created_at,
        "updated_at": tenant.updated_at,
        "version": tenant.version,
    }


def _update_values(tenant: Tenant) -> dict[str, Any]:
    return {
        "name": tenant.name,
        "status": tenant.status.value,
        "updated_at": tenant.updated_at,
    }


def _to_domain(row: RowMapping) -> Tenant:
    return Tenant(
        id=TenantId(row["id"]),
        slug=row["slug"],
        name=row["name"],
        status=TenantStatus(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        version=row["version"],
    )


__all__ = ["SqlAlchemyTenantRepository"]
