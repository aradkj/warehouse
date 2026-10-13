"""Identity repository ports."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from identity.domain.membership import TenantMembership
from identity.domain.policy import AccessPolicy
from identity.domain.role import Role
from identity.domain.service_account import ServiceAccount
from identity.domain.session import UserSession
from identity.domain.tenant import Tenant
from identity.domain.user import User
from shared.domain.identifiers import (
    MembershipId,
    PolicyId,
    RoleId,
    ServiceAccountId,
    TenantId,
    UserId,
)


@runtime_checkable
class RoleRepository(Protocol):
    """Persistence port for Role aggregate."""

    async def add(self, role: Role) -> None:
        """Insert role (raises ``DuplicateResource`` on duplicate name within tenant)."""
        ...

    async def get(self, role_id: RoleId) -> Role | None:
        """Load role by id."""
        ...

    async def get_by_name(self, name: str, tenant_id: TenantId | None = None) -> Role | None:
        """Load role by name (tenant-specific or system)."""
        ...

    async def list_for_tenant(
        self, tenant_id: TenantId | None, include_system: bool = True
    ) -> Sequence[Role]:
        """List roles accessible by tenant (including system roles if requested)."""
        ...

    async def save(self, role: Role, *, expected_version: int) -> None:
        """Update role with an optimistic-concurrency guard."""
        ...

    async def delete(self, role_id: RoleId) -> None:
        """Delete custom tenant role."""
        ...


@runtime_checkable
class PolicyRepository(Protocol):
    """Persistence port for AccessPolicy aggregate."""

    async def add(self, policy: AccessPolicy) -> None:
        """Insert access policy."""
        ...

    async def get(self, policy_id: PolicyId) -> AccessPolicy | None:
        """Load policy by id."""
        ...

    async def list_for_tenant(
        self, tenant_id: TenantId | None, active_only: bool = False
    ) -> Sequence[AccessPolicy]:
        """List policies for a tenant (including global system policies)."""
        ...

    async def save(self, policy: AccessPolicy, *, expected_version: int) -> None:
        """Update policy with optimistic-concurrency guard."""
        ...

    async def delete(self, policy_id: PolicyId) -> None:
        """Delete policy."""
        ...


__all__ = [
    "MembershipRepository",
    "PolicyRepository",
    "RoleRepository",
    "ServiceAccountRepository",
    "SessionRepository",
    "TenantRepository",
    "UserRepository",
]


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


@runtime_checkable
class UserRepository(Protocol):
    """Persistence port for User aggregate."""

    async def add(self, user: User) -> None:
        """Insert a user (raises ``DuplicateResource`` on duplicate email)."""
        ...

    async def get(self, user_id: UserId) -> User | None:
        """Load user by id."""
        ...

    async def get_by_email(self, email: str) -> User | None:
        """Load user by normalized email."""
        ...

    async def save(self, user: User, *, expected_version: int) -> None:
        """Update a user with an optimistic-concurrency guard."""
        ...


@runtime_checkable
class MembershipRepository(Protocol):
    """Persistence port for TenantMembership association."""

    async def add(self, membership: TenantMembership) -> None:
        """Insert membership (raises ``DuplicateResource`` on duplicate (user, tenant))."""
        ...

    async def get(self, membership_id: MembershipId) -> TenantMembership | None:
        """Load membership by id."""
        ...

    async def get_by_user_and_tenant(
        self, user_id: UserId, tenant_id: TenantId
    ) -> TenantMembership | None:
        """Load membership for specific user and tenant."""
        ...

    async def list_for_user(self, user_id: UserId) -> Sequence[TenantMembership]:
        """List all memberships for a user."""
        ...

    async def list_for_tenant(self, tenant_id: TenantId) -> Sequence[TenantMembership]:
        """List all memberships for a tenant."""
        ...

    async def save(self, membership: TenantMembership, *, expected_version: int) -> None:
        """Update membership with an optimistic-concurrency guard."""
        ...


@runtime_checkable
class SessionRepository(Protocol):
    """Persistence port for UserSession tracking."""

    async def add(self, session: UserSession) -> None:
        """Insert session record."""
        ...

    async def get(self, session_id: uuid.UUID) -> UserSession | None:
        """Load session by id."""
        ...

    async def get_by_token_hash(
        self, token_hash: str, *, for_update: bool = False
    ) -> UserSession | None:
        """Load session by refresh token hash (optionally locking row)."""
        ...

    async def revoke_family(self, family_id: uuid.UUID) -> None:
        """Revoke all sessions in a refresh token family."""
        ...

    async def revoke_all_for_user(self, user_id: UserId) -> None:
        """Revoke all sessions for a user (e.g. on password change)."""
        ...

    async def save(self, session: UserSession) -> None:
        """Update session state."""
        ...


@runtime_checkable
class ServiceAccountRepository(Protocol):
    """Persistence port for ServiceAccount entities."""

    async def add(self, service_account: ServiceAccount) -> None:
        """Insert service account (raises ``DuplicateResource`` on duplicate client_id)."""
        ...

    async def get(self, service_account_id: ServiceAccountId) -> ServiceAccount | None:
        """Load service account by id."""
        ...

    async def get_by_client_id(self, client_id: str) -> ServiceAccount | None:
        """Load service account by client_id."""
        ...

    async def list_for_tenant(self, tenant_id: TenantId) -> Sequence[ServiceAccount]:
        """List all service accounts for a tenant."""
        ...

    async def save(self, service_account: ServiceAccount, *, expected_version: int) -> None:
        """Update service account with an optimistic-concurrency guard."""
        ...


__all__ = [
    "MembershipRepository",
    "ServiceAccountRepository",
    "SessionRepository",
    "TenantRepository",
    "UserRepository",
]
