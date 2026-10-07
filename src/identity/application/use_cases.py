"""Identity use cases.

Phase 1 exposes only what the rest of the platform needs in order to exist:
tenant provisioning and tenant lookup. Authentication, users, roles, service
accounts and ABAC policies belong to Phase 4.
"""

from __future__ import annotations

from identity.application.commands import GetTenantQuery, RegisterTenantCommand, TenantView
from identity.domain.tenant import Tenant
from identity.ports.unit_of_work import IdentityUnitOfWork
from shared.application.errors import NotFound
from shared.application.use_case import Executed, QueryUseCase, UseCase, pull_events


class RegisterTenant(UseCase[RegisterTenantCommand, TenantView, IdentityUnitOfWork]):
    """Register a new tenant.

    Uniqueness of the slug is enforced by a database constraint, so two
    concurrent registrations of the same slug cannot both succeed (the loser
    receives ``409 duplicate_resource``).
    """

    operation = "identity.register_tenant"
    success_status = 201

    async def _execute(
        self, uow: IdentityUnitOfWork, command: RegisterTenantCommand
    ) -> Executed[TenantView]:
        tenant = Tenant.register(
            slug=command.slug.strip().lower(), name=command.name, now=self._clock.now()
        )
        await uow.tenants.add(tenant)
        return Executed(
            view=TenantView.of(tenant),
            resource_type="tenant",
            resource_id=tenant.id,
            events=pull_events(tenant),
        )


class GetTenant(QueryUseCase[GetTenantQuery, TenantView, IdentityUnitOfWork]):
    """Fetch one tenant."""

    async def _execute(self, uow: IdentityUnitOfWork, command: GetTenantQuery) -> TenantView:
        tenant = await uow.tenants.get(command.tenant_id)
        if tenant is None:
            raise NotFound("tenant", command.tenant_id)
        return TenantView.of(tenant)


__all__ = ["GetTenant", "RegisterTenant"]
