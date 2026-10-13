"""Identity and authentication Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from identity.application.commands import (
    AuthTokensView,
    InvitationView,
    ServiceAccountCreatedView,
    ServiceAccountTokensView,
    ServiceAccountView,
    TenantMembershipView,
    UserView,
)
from identity.domain.principal import Principal


class RegisterUserRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    tenant_id: UUID | None = None
    invitation_token: str | None = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=128)
    tenant_id: UUID | None = None


class RefreshTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1)


class SelectTenantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class CreateServiceAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=160)
    client_id: str = Field(min_length=3, max_length=64)
    scopes: list[str] = Field(default_factory=list)


class AuthenticateServiceAccountRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=1, max_length=64)
    client_secret: str = Field(min_length=1)


class UserResponse(BaseModel):
    id: UUID
    email: str
    status: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_view(cls, view: UserView) -> UserResponse:
        return cls(
            id=view.id,
            email=view.email,
            status=view.status.value,
            created_at=view.created_at,
            updated_at=view.updated_at,
        )


class TenantMembershipResponse(BaseModel):
    id: UUID
    user_id: UUID
    tenant_id: UUID
    status: str
    role: str
    created_at: datetime
    tenant_name: str | None = None
    tenant_slug: str | None = None
    roles: list[str] = Field(default_factory=list)

    @classmethod
    def from_view(cls, view: TenantMembershipView) -> TenantMembershipResponse:
        return cls(
            id=view.id,
            user_id=view.user_id,
            tenant_id=view.tenant_id,
            status=view.status.value,
            role=view.role,
            created_at=view.created_at,
            tenant_name=view.tenant_name,
            tenant_slug=view.tenant_slug,
            roles=list(view.roles),
        )


class AuthTokensResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse
    active_tenant_id: UUID | None = None
    active_membership_id: UUID | None = None

    @classmethod
    def from_view(cls, view: AuthTokensView) -> AuthTokensResponse:
        return cls(
            access_token=view.access_token,
            refresh_token=view.refresh_token,
            token_type=view.token_type,
            expires_in=view.expires_in,
            user=UserResponse.from_view(view.user),
            active_tenant_id=view.active_tenant_id,
            active_membership_id=view.active_membership_id,
        )


class PrincipalResponse(BaseModel):
    id: str
    type: str
    email: str | None = None
    active_tenant_id: UUID | None = None
    active_membership_id: UUID | None = None
    account_status: str = "ACTIVE"
    role: str = "MEMBER"

    @classmethod
    def from_principal(cls, principal: Principal) -> PrincipalResponse:
        return cls(
            id=principal.id,
            type=principal.type.value,
            email=principal.email,
            active_tenant_id=principal.active_tenant_id,
            active_membership_id=principal.active_membership_id,
            account_status=principal.account_status,
            role=principal.role,
        )


class CreateInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str | None = None
    role: str = Field(default="MEMBER", pattern=r"^(ADMIN|MEMBER)$")


class InvitationResponse(BaseModel):
    invitation_token: str
    tenant_id: UUID
    email: str | None = None
    role: str
    expires_in_days: int = 7

    @classmethod
    def from_view(cls, view: InvitationView) -> InvitationResponse:
        return cls(
            invitation_token=view.invitation_token,
            tenant_id=view.tenant_id,
            email=view.email,
            role=view.role,
            expires_in_days=view.expires_in_days,
        )


class ProvisionMembershipRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID | None = None
    email: str | None = None
    role: str = Field(default="MEMBER", pattern=r"^(ADMIN|MEMBER)$")


class ServiceAccountResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    client_id: str
    status: str
    scopes: list[str] = Field(default_factory=list)
    created_at: datetime

    @classmethod
    def from_view(cls, view: ServiceAccountView) -> ServiceAccountResponse:
        return cls(
            id=view.id,
            tenant_id=view.tenant_id,
            name=view.name,
            client_id=view.client_id,
            status=view.status.value,
            scopes=sorted(view.scopes),
            created_at=view.created_at,
        )


class ServiceAccountCreatedResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    name: str
    client_id: str
    client_secret: str
    status: str
    scopes: list[str] = Field(default_factory=list)
    created_at: datetime

    @classmethod
    def from_view(cls, view: ServiceAccountCreatedView) -> ServiceAccountCreatedResponse:
        return cls(
            id=view.id,
            tenant_id=view.tenant_id,
            name=view.name,
            client_id=view.client_id,
            client_secret=view.client_secret,
            status=view.status.value,
            scopes=sorted(view.scopes),
            created_at=view.created_at,
        )


class ServiceAccountTokensResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    service_account: ServiceAccountResponse

    @classmethod
    def from_view(cls, view: ServiceAccountTokensView) -> ServiceAccountTokensResponse:
        return cls(
            access_token=view.access_token,
            token_type=view.token_type,
            expires_in=view.expires_in,
            service_account=ServiceAccountResponse.from_view(view.service_account),
        )


__all__ = [
    "AuthTokensResponse",
    "AuthenticateServiceAccountRequest",
    "ChangePasswordRequest",
    "CreateInvitationRequest",
    "CreateServiceAccountRequest",
    "InvitationResponse",
    "LoginRequest",
    "PrincipalResponse",
    "ProvisionMembershipRequest",
    "RefreshTokenRequest",
    "RegisterUserRequest",
    "SelectTenantRequest",
    "ServiceAccountCreatedResponse",
    "ServiceAccountResponse",
    "ServiceAccountTokensResponse",
    "TenantMembershipResponse",
    "UserResponse",
]
