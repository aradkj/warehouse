"""Identity commands, queries and views."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from identity.domain.membership import MembershipStatus, TenantMembership
from identity.domain.service_account import ServiceAccount, ServiceAccountStatus
from identity.domain.tenant import Tenant, TenantStatus
from identity.domain.user import User, UserStatus
from shared.application.context import RequestContext
from shared.domain.identifiers import MembershipId, ServiceAccountId, TenantId, UserId


# ===========================================================================
# Tenant (Phase 1)
# ===========================================================================
@dataclass(frozen=True, slots=True)
class RegisterTenantCommand:
    """Platform-scoped operation: it carries no ``tenant_id`` on purpose."""

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


# ===========================================================================
# User & Membership (Phase 4.1)
# ===========================================================================
@dataclass(frozen=True, slots=True)
class UserView:
    id: UserId
    email: str
    status: UserStatus
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, user: User) -> UserView:
        return cls(
            id=user.id,
            email=user.email,
            status=user.status,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )


@dataclass(frozen=True, slots=True)
class TenantMembershipView:
    id: MembershipId
    user_id: UserId
    tenant_id: TenantId
    status: MembershipStatus
    role: str
    created_at: datetime
    tenant_name: str | None = None
    tenant_slug: str | None = None
    roles: tuple[str, ...] = ()

    @classmethod
    def of(
        cls,
        membership: TenantMembership,
        *,
        tenant_name: str | None = None,
        tenant_slug: str | None = None,
    ) -> TenantMembershipView:
        return cls(
            id=membership.id,
            user_id=membership.user_id,
            tenant_id=membership.tenant_id,
            status=membership.status,
            role=membership.role.value,
            created_at=membership.created_at,
            tenant_name=tenant_name,
            tenant_slug=tenant_slug,
            roles=tuple(membership.roles),
        )


@dataclass(frozen=True, slots=True)
class RegisterUserCommand:
    context: RequestContext
    email: str
    password: str
    tenant_id: TenantId | None = None
    invitation_token: str | None = None


@dataclass(frozen=True, slots=True)
class AuthenticateUserCommand:
    context: RequestContext
    email: str
    password: str
    tenant_id: TenantId | None = None


@dataclass(frozen=True, slots=True)
class AuthTokensView:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    user: UserView
    active_tenant_id: TenantId | None = None
    active_membership_id: MembershipId | None = None


@dataclass(frozen=True, slots=True)
class RefreshTokenCommand:
    context: RequestContext
    refresh_token: str


@dataclass(frozen=True, slots=True)
class RevokeSessionCommand:
    context: RequestContext
    refresh_token: str


@dataclass(frozen=True, slots=True)
class ListMembershipsQuery:
    context: RequestContext
    user_id: UserId


@dataclass(frozen=True, slots=True)
class SelectTenantCommand:
    context: RequestContext
    user_id: UserId
    tenant_id: TenantId


@dataclass(frozen=True, slots=True)
class ChangePasswordCommand:
    context: RequestContext
    user_id: UserId
    current_password: str
    new_password: str


# ===========================================================================
# Service Account (Phase 4.1)
# ===========================================================================
@dataclass(frozen=True, slots=True)
class ServiceAccountView:
    id: ServiceAccountId
    tenant_id: TenantId
    name: str
    client_id: str
    status: ServiceAccountStatus
    created_at: datetime
    scopes: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, sa: ServiceAccount) -> ServiceAccountView:
        return cls(
            id=sa.id,
            tenant_id=sa.tenant_id,
            name=sa.name,
            client_id=sa.client_id,
            status=sa.status,
            created_at=sa.created_at,
            scopes=sorted(sa.scopes),
        )


@dataclass(frozen=True, slots=True)
class ServiceAccountCreatedView:
    id: ServiceAccountId
    tenant_id: TenantId
    name: str
    client_id: str
    client_secret: str
    status: ServiceAccountStatus
    created_at: datetime
    scopes: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CreateServiceAccountCommand:
    tenant_id: TenantId
    context: RequestContext
    name: str
    client_id: str
    scopes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AuthenticateServiceAccountCommand:
    context: RequestContext
    client_id: str
    client_secret: str


@dataclass(frozen=True, slots=True)
class ServiceAccountTokensView:
    access_token: str
    token_type: str
    expires_in: int
    service_account: ServiceAccountView


@dataclass(frozen=True, slots=True)
class CreateInvitationCommand:
    context: RequestContext
    tenant_id: TenantId
    email: str | None = None
    role: str = "MEMBER"


@dataclass(frozen=True, slots=True)
class InvitationView:
    invitation_token: str
    tenant_id: TenantId
    email: str | None
    role: str
    expires_in_days: int = 7


@dataclass(frozen=True, slots=True)
class ProvisionMembershipCommand:
    context: RequestContext
    tenant_id: TenantId
    user_id: UserId | None = None
    email: str | None = None
    role: str = "MEMBER"


@dataclass(frozen=True, slots=True)
class RotateServiceAccountSecretCommand:
    tenant_id: TenantId
    context: RequestContext
    service_account_id: ServiceAccountId


@dataclass(frozen=True, slots=True)
class RevokeServiceAccountCommand:
    tenant_id: TenantId
    context: RequestContext
    service_account_id: ServiceAccountId


# ===========================================================================
# Roles & Policies (Phase 4.2)
# ===========================================================================
@dataclass(frozen=True, slots=True)
class RoleView:
    id: str
    tenant_id: str | None
    name: str
    description: str
    is_system: bool
    permissions: list[str]
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True, slots=True)
class PolicyView:
    id: str
    tenant_id: str | None
    name: str
    description: str
    effect: str
    actions: list[str]
    resources: list[str]
    conditions: dict[str, Any] | None
    status: str
    created_at: datetime
    updated_at: datetime
    version: int


@dataclass(frozen=True, slots=True)
class CreateRoleCommand:
    context: RequestContext
    tenant_id: TenantId
    name: str
    description: str
    permissions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GetRoleQuery:
    context: RequestContext
    role_id: str


@dataclass(frozen=True, slots=True)
class ListRolesQuery:
    context: RequestContext
    tenant_id: TenantId | None = None


@dataclass(frozen=True, slots=True)
class AssignRoleCommand:
    context: RequestContext
    tenant_id: TenantId
    membership_id: MembershipId
    role_name: str


@dataclass(frozen=True, slots=True)
class RevokeRoleCommand:
    context: RequestContext
    tenant_id: TenantId
    membership_id: MembershipId
    role_name: str


@dataclass(frozen=True, slots=True)
class CreatePolicyCommand:
    context: RequestContext
    tenant_id: TenantId
    name: str
    description: str
    effect: str
    actions: tuple[str, ...]
    resources: tuple[str, ...]
    conditions: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class GetPolicyQuery:
    context: RequestContext
    policy_id: str


@dataclass(frozen=True, slots=True)
class ListPoliciesQuery:
    context: RequestContext
    tenant_id: TenantId | None = None
    active_only: bool = False


@dataclass(frozen=True, slots=True)
class UpdatePolicyStatusCommand:
    context: RequestContext
    tenant_id: TenantId
    policy_id: str
    status: str


__all__ = [
    "AssignRoleCommand",
    "AuthTokensView",
    "AuthenticateServiceAccountCommand",
    "AuthenticateUserCommand",
    "ChangePasswordCommand",
    "CreateInvitationCommand",
    "CreatePolicyCommand",
    "CreateRoleCommand",
    "CreateServiceAccountCommand",
    "GetPolicyQuery",
    "GetRoleQuery",
    "GetTenantQuery",
    "InvitationView",
    "ListMembershipsQuery",
    "ListPoliciesQuery",
    "ListRolesQuery",
    "PolicyView",
    "ProvisionMembershipCommand",
    "RefreshTokenCommand",
    "RegisterTenantCommand",
    "RegisterUserCommand",
    "RevokeRoleCommand",
    "RevokeServiceAccountCommand",
    "RevokeSessionCommand",
    "RoleView",
    "RotateServiceAccountSecretCommand",
    "SelectTenantCommand",
    "ServiceAccountCreatedView",
    "ServiceAccountTokensView",
    "ServiceAccountView",
    "TenantMembershipView",
    "TenantView",
    "UpdatePolicyStatusCommand",
    "UserView",
]
