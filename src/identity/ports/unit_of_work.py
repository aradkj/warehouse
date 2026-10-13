"""Identity unit-of-work port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from audit.ports.audit import AuditPort
from identity.ports.repositories import (
    MembershipRepository,
    PolicyRepository,
    RoleRepository,
    ServiceAccountRepository,
    SessionRepository,
    TenantRepository,
    UserRepository,
)
from shared.application.use_case import ApplicationUnitOfWork


@runtime_checkable
class IdentityUnitOfWork(ApplicationUnitOfWork, Protocol):
    """Transactional view over the identity persistence ports."""

    tenants: TenantRepository
    users: UserRepository
    memberships: MembershipRepository
    sessions: SessionRepository
    service_accounts: ServiceAccountRepository
    roles: RoleRepository
    policies: PolicyRepository
    audit: AuditPort


__all__ = ["IdentityUnitOfWork"]
