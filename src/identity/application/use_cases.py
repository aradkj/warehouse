"""Identity use cases.

Includes:
- Tenant provisioning and lookup (Phase 1)
- User identity, authentication, session lifecycle, memberships (Phase 4.1)
- Service accounts (Phase 4.1)
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Callable
from datetime import timedelta

from audit.ports.audit import AuditEntry
from identity.application.commands import (
    AssignRoleCommand,
    AuthenticateServiceAccountCommand,
    AuthenticateUserCommand,
    AuthTokensView,
    ChangePasswordCommand,
    CreateInvitationCommand,
    CreatePolicyCommand,
    CreateRoleCommand,
    CreateServiceAccountCommand,
    GetPolicyQuery,
    GetRoleQuery,
    GetTenantQuery,
    InvitationView,
    ListMembershipsQuery,
    ListPoliciesQuery,
    ListRolesQuery,
    PolicyView,
    ProvisionMembershipCommand,
    RefreshTokenCommand,
    RegisterTenantCommand,
    RegisterUserCommand,
    RevokeRoleCommand,
    RevokeServiceAccountCommand,
    RevokeSessionCommand,
    RoleView,
    RotateServiceAccountSecretCommand,
    SelectTenantCommand,
    ServiceAccountCreatedView,
    ServiceAccountTokensView,
    ServiceAccountView,
    TenantMembershipView,
    TenantView,
    UpdatePolicyStatusCommand,
    UserView,
)
from identity.domain.membership import MembershipRole, MembershipStatus, TenantMembership
from identity.domain.policy import AccessPolicy, PolicyEffect
from identity.domain.role import RESERVED_ROLE_NAMES, Role
from identity.domain.service_account import ServiceAccount, ServiceAccountStatus
from identity.domain.session import UserSession
from identity.domain.tenant import Tenant, TenantStatus
from identity.domain.user import User, UserStatus, normalize_email
from identity.infrastructure.security import (
    PasswordHasher,
    TokenService,
    generate_secure_secret,
    hash_token,
)
from identity.ports.unit_of_work import IdentityUnitOfWork
from shared.application.errors import (
    AccountDisabled,
    AuthenticationFailed,
    CannotRemoveLastAdmin,
    DuplicateResource,
    Forbidden,
    MembershipDisabled,
    NotFound,
    TenantSuspended,
)
from shared.application.events import EventDispatcher
from shared.application.use_case import Executed, QueryUseCase, UseCase, pull_events
from shared.domain.clock import Clock
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import PolicyId, RoleId, TenantId

DEFAULT_REFRESH_TOKEN_TTL = timedelta(days=7)


# ===========================================================================
# Tenant Use Cases (Phase 1)
# ===========================================================================
class RegisterTenant(UseCase[RegisterTenantCommand, TenantView, IdentityUnitOfWork]):
    """Register a new tenant."""

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

    permission = "access.roles.read"
    resource_type = "tenant"

    async def _execute(self, uow: IdentityUnitOfWork, command: GetTenantQuery) -> TenantView:
        tenant = await uow.tenants.get(command.tenant_id)
        if tenant is None:
            raise NotFound("tenant", command.tenant_id)
        return TenantView.of(tenant)


# ===========================================================================
# User Identity & Authentication Use Cases (Phase 4.1)
# ===========================================================================
class RegisterUser(UseCase[RegisterUserCommand, UserView, IdentityUnitOfWork]):
    """Register/provision a new user with an optional initial tenant membership."""

    operation = "identity.register_user"
    success_status = 201

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
        hasher: PasswordHasher,
        token_service: TokenService | None = None,
        events: EventDispatcher | None = None,
    ) -> None:
        super().__init__(uow_factory=uow_factory, clock=clock, events=events)
        self._hasher = hasher
        self._token_service = token_service

    async def _execute(
        self, uow: IdentityUnitOfWork, command: RegisterUserCommand
    ) -> Executed[UserView]:
        email = normalize_email(command.email)
        existing = await uow.users.get_by_email(email)
        if existing is not None:
            raise DuplicateResource(resource_type="user", field="email")

        target_tenant_id: TenantId | None = None
        target_role = MembershipRole.MEMBER

        if command.invitation_token is not None:
            if self._token_service is None:
                raise RuntimeError("token_service is required to verify invitation tokens")
            claims = self._token_service.verify_invitation_token(command.invitation_token)
            inv_tenant_id = TenantId(uuid.UUID(claims["tenant_id"]))
            if command.tenant_id is not None and command.tenant_id != inv_tenant_id:
                raise Forbidden("invitation token was issued for a different tenant")
            inv_email = claims.get("email")
            if inv_email and inv_email.strip().lower() != email:
                raise Forbidden("invitation token was issued for a different email address")
            target_tenant_id = inv_tenant_id
            target_role = MembershipRole(claims.get("role", "MEMBER"))
        elif command.tenant_id is not None:
            # An anonymous caller cannot join an arbitrary tenant by UUID without an invitation
            raise Forbidden("joining an existing tenant requires a valid invitation token")

        if target_tenant_id is not None:
            tenant = await uow.tenants.get(target_tenant_id)
            if tenant is None:
                raise NotFound("tenant", target_tenant_id)
            if tenant.status != TenantStatus.ACTIVE:
                raise TenantSuspended(
                    f"tenant is {tenant.status.value.lower()}",
                    details={"tenant_id": str(target_tenant_id)},
                )

        now = self._clock.now()
        password_hash = self._hasher.hash(command.password)
        user = User.create(email=email, password_hash=password_hash, now=now)
        await uow.users.add(user)

        if target_tenant_id is not None:
            membership = TenantMembership.create(
                user_id=user.id,
                tenant_id=target_tenant_id,
                role=target_role,
                now=now,
            )
            await uow.memberships.add(membership)

        return Executed(
            view=UserView.of(user),
            resource_type="user",
            resource_id=user.id,
            events=(),
        )


class AuthenticateUser(UseCase[AuthenticateUserCommand, AuthTokensView, IdentityUnitOfWork]):
    """Authenticate a user using email and password, returning access and refresh tokens."""

    operation = "identity.authenticate_user"
    success_status = 200

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
        hasher: PasswordHasher,
        token_service: TokenService,
        refresh_ttl: timedelta = DEFAULT_REFRESH_TOKEN_TTL,
        events: EventDispatcher | None = None,
    ) -> None:
        super().__init__(uow_factory=uow_factory, clock=clock, events=events)
        self._hasher = hasher
        self._token_service = token_service
        self._refresh_ttl = refresh_ttl

    async def _execute(
        self, uow: IdentityUnitOfWork, command: AuthenticateUserCommand
    ) -> Executed[AuthTokensView]:
        email = normalize_email(command.email)
        user = await uow.users.get_by_email(email)
        if user is None:
            raise AuthenticationFailed("invalid email or password")

        if not self._hasher.verify(command.password, user.password_hash):
            raise AuthenticationFailed("invalid email or password")

        now = self._clock.now()

        # Verify-and-rehash strategy for algorithm upgrades
        if self._hasher.needs_rehash(user.password_hash):
            new_hash = self._hasher.hash(command.password)
            user.change_password(new_hash, now=now)
            await uow.users.save(user, expected_version=user.version)

        if user.status != UserStatus.ACTIVE:
            raise AccountDisabled(f"user account is {user.status.value.lower()}")

        active_tenant_id = None
        active_membership_id = None

        if command.tenant_id is not None:
            tenant = await uow.tenants.get(command.tenant_id)
            if tenant is None:
                raise NotFound("tenant", command.tenant_id)
            if tenant.status != TenantStatus.ACTIVE:
                raise TenantSuspended(
                    f"tenant is {tenant.status.value.lower()}",
                    details={"tenant_id": str(command.tenant_id)},
                )

            membership = await uow.memberships.get_by_user_and_tenant(user.id, command.tenant_id)
            if membership is None:
                raise Forbidden("user is not a member of the requested tenant")
            if membership.status != MembershipStatus.ACTIVE:
                raise MembershipDisabled(f"tenant membership is {membership.status.value.lower()}")

            active_tenant_id = command.tenant_id
            active_membership_id = membership.id

        now = self._clock.now()
        raw_refresh_token = secrets.token_urlsafe(32)
        hashed_refresh = hash_token(raw_refresh_token)
        session_expires_at = now + self._refresh_ttl

        session = UserSession.create(
            user_id=user.id,
            token_hash=hashed_refresh,
            expires_at=session_expires_at,
            now=now,
        )
        await uow.sessions.add(session)

        claims: dict[str, str] = {
            "sub": str(user.id),
            "type": "access",
            "email": user.email,
        }
        if active_tenant_id is not None:
            claims["tenant_id"] = str(active_tenant_id)
        if active_membership_id is not None:
            claims["membership_id"] = str(active_membership_id)

        access_token = self._token_service.create_access_token(claims=claims)

        view = AuthTokensView(
            access_token=access_token,
            refresh_token=raw_refresh_token,
            token_type="bearer",
            expires_in=self._token_service.access_token_ttl_seconds,
            user=UserView.of(user),
            active_tenant_id=active_tenant_id,
            active_membership_id=active_membership_id,
        )
        return Executed(
            view=view,
            resource_type="session",
            resource_id=session.id,
            events=(),
        )


class RefreshToken(UseCase[RefreshTokenCommand, AuthTokensView, IdentityUnitOfWork]):
    """Refresh an access token using a refresh token, rotating the refresh token."""

    operation = "identity.refresh_token"
    success_status = 200

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
        token_service: TokenService,
        refresh_ttl: timedelta = DEFAULT_REFRESH_TOKEN_TTL,
        events: EventDispatcher | None = None,
    ) -> None:
        super().__init__(uow_factory=uow_factory, clock=clock, events=events)
        self._token_service = token_service
        self._refresh_ttl = refresh_ttl

    async def _execute(
        self, uow: IdentityUnitOfWork, command: RefreshTokenCommand
    ) -> Executed[AuthTokensView]:
        now = self._clock.now()
        hashed = hash_token(command.refresh_token)

        session = await uow.sessions.get_by_token_hash(hashed, for_update=True)
        if session is None:
            raise AuthenticationFailed("invalid or expired refresh token")

        # Reuse detection: if a revoked or already rotated session is used, revoke the whole family
        if session.is_revoked or session.rotated_at is not None:
            await uow.sessions.revoke_family(session.family_id)
            raise AuthenticationFailed("refresh token reuse detected; family revoked")

        if session.is_expired(now):
            raise AuthenticationFailed("refresh token has expired")

        user = await uow.users.get(session.user_id)
        if user is None:
            raise AuthenticationFailed("user not found")
        if user.status != UserStatus.ACTIVE:
            raise AccountDisabled(f"user account is {user.status.value.lower()}")

        # Mark current session as rotated
        session.rotate(now=now)
        await uow.sessions.save(session)

        # Create new session in same family
        new_raw_token = secrets.token_urlsafe(32)
        new_hashed = hash_token(new_raw_token)
        new_expires_at = now + self._refresh_ttl

        new_session = UserSession.create(
            user_id=user.id,
            token_hash=new_hashed,
            expires_at=new_expires_at,
            now=now,
            family_id=session.family_id,
        )
        await uow.sessions.add(new_session)

        claims: dict[str, str] = {
            "sub": str(user.id),
            "type": "access",
            "email": user.email,
        }

        access_token = self._token_service.create_access_token(claims=claims)

        view = AuthTokensView(
            access_token=access_token,
            refresh_token=new_raw_token,
            token_type="bearer",
            expires_in=self._token_service.access_token_ttl_seconds,
            user=UserView.of(user),
            active_tenant_id=None,
            active_membership_id=None,
        )
        return Executed(
            view=view,
            resource_type="session",
            resource_id=new_session.id,
            events=(),
        )


class RevokeSession(UseCase[RevokeSessionCommand, None, IdentityUnitOfWork]):
    """Revoke a session (logout)."""

    operation = "identity.revoke_session"
    success_status = 200

    async def _execute(
        self, uow: IdentityUnitOfWork, command: RevokeSessionCommand
    ) -> Executed[None]:
        hashed = hash_token(command.refresh_token)
        session = await uow.sessions.get_by_token_hash(hashed)
        if session is not None and not session.is_revoked:
            session.revoke()
            await uow.sessions.save(session)

        return Executed(
            view=None,
            resource_type="session",
            resource_id=session.id if session else None,
            events=(),
        )


class ListMemberships(
    QueryUseCase[ListMembershipsQuery, list[TenantMembershipView], IdentityUnitOfWork]
):
    """List memberships for a user, including tenant names/slugs."""

    bypass_rls: ClassVar[bool] = True

    async def _execute(
        self, uow: IdentityUnitOfWork, command: ListMembershipsQuery
    ) -> list[TenantMembershipView]:
        memberships = await uow.memberships.list_for_user(command.user_id)
        views: list[TenantMembershipView] = []
        for m in memberships:
            tenant = await uow.tenants.get(m.tenant_id)
            views.append(
                TenantMembershipView.of(
                    m,
                    tenant_name=tenant.name if tenant else None,
                    tenant_slug=tenant.slug if tenant else None,
                )
            )
        return views


class SelectTenant(UseCase[SelectTenantCommand, AuthTokensView, IdentityUnitOfWork]):
    """Select or switch active tenant for a user, returning new scoped tokens."""

    operation = "identity.select_tenant"
    success_status = 200

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
        token_service: TokenService,
        refresh_ttl: timedelta = DEFAULT_REFRESH_TOKEN_TTL,
        events: EventDispatcher | None = None,
    ) -> None:
        super().__init__(uow_factory=uow_factory, clock=clock, events=events)
        self._token_service = token_service
        self._refresh_ttl = refresh_ttl

    async def _execute(
        self, uow: IdentityUnitOfWork, command: SelectTenantCommand
    ) -> Executed[AuthTokensView]:
        user = await uow.users.get(command.user_id)
        if user is None:
            raise NotFound("user", command.user_id)
        if user.status != UserStatus.ACTIVE:
            raise AccountDisabled(f"user account is {user.status.value.lower()}")

        tenant = await uow.tenants.get(command.tenant_id)
        if tenant is None:
            raise NotFound("tenant", command.tenant_id)
        if tenant.status != TenantStatus.ACTIVE:
            raise TenantSuspended(
                f"tenant is {tenant.status.value.lower()}",
                details={"tenant_id": str(command.tenant_id)},
            )

        membership = await uow.memberships.get_by_user_and_tenant(user.id, command.tenant_id)
        if membership is None:
            raise Forbidden("user is not a member of the requested tenant")
        if membership.status != MembershipStatus.ACTIVE:
            raise MembershipDisabled(f"tenant membership is {membership.status.value.lower()}")

        now = self._clock.now()
        raw_refresh_token = secrets.token_urlsafe(32)
        hashed_refresh = hash_token(raw_refresh_token)
        session_expires_at = now + self._refresh_ttl

        session = UserSession.create(
            user_id=user.id,
            token_hash=hashed_refresh,
            expires_at=session_expires_at,
            now=now,
        )
        await uow.sessions.add(session)

        claims: dict[str, str] = {
            "sub": str(user.id),
            "type": "access",
            "email": user.email,
            "tenant_id": str(tenant.id),
            "membership_id": str(membership.id),
        }
        access_token = self._token_service.create_access_token(claims=claims)

        view = AuthTokensView(
            access_token=access_token,
            refresh_token=raw_refresh_token,
            token_type="bearer",
            expires_in=self._token_service.access_token_ttl_seconds,
            user=UserView.of(user),
            active_tenant_id=tenant.id,
            active_membership_id=membership.id,
        )
        return Executed(
            view=view,
            resource_type="session",
            resource_id=session.id,
            events=(),
        )


class ChangePassword(UseCase[ChangePasswordCommand, UserView, IdentityUnitOfWork]):
    """Change a user's password and revoke all existing sessions."""

    operation = "identity.change_password"
    success_status = 200

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
        hasher: PasswordHasher,
        events: EventDispatcher | None = None,
    ) -> None:
        super().__init__(uow_factory=uow_factory, clock=clock, events=events)
        self._hasher = hasher

    async def _execute(
        self, uow: IdentityUnitOfWork, command: ChangePasswordCommand
    ) -> Executed[UserView]:
        user = await uow.users.get(command.user_id)
        if user is None:
            raise NotFound("user", command.user_id)
        if user.status != UserStatus.ACTIVE:
            raise AccountDisabled(f"user account is {user.status.value.lower()}")

        if not self._hasher.verify(command.current_password, user.password_hash):
            raise AuthenticationFailed("current password is incorrect")

        now = self._clock.now()
        new_hash = self._hasher.hash(command.new_password)
        user.change_password(new_hash, now=now)
        await uow.users.save(user, expected_version=user.version)

        # Invalidate all existing sessions upon password change
        await uow.sessions.revoke_all_for_user(user.id)

        return Executed(
            view=UserView.of(user),
            resource_type="user",
            resource_id=user.id,
            events=(),
        )


# ===========================================================================
# Service Account Use Cases (Phase 4.1)
# ===========================================================================
class CreateServiceAccount(
    UseCase[CreateServiceAccountCommand, ServiceAccountCreatedView, IdentityUnitOfWork]
):
    """Create a machine/service account scoped to a tenant."""

    operation = "identity.create_service_account"
    permission = "access.roles.manage"
    resource_type = "service_account"
    success_status = 201

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
        hasher: PasswordHasher,
        events: EventDispatcher | None = None,
        authorizer: Any | None = None,
    ) -> None:
        super().__init__(uow_factory=uow_factory, clock=clock, events=events, authorizer=authorizer)
        self._hasher = hasher

    async def _execute(
        self, uow: IdentityUnitOfWork, command: CreateServiceAccountCommand
    ) -> Executed[ServiceAccountCreatedView]:
        await self._authorize(
            command.context,
            action="access.roles.manage",
            resource_type="service_account",
            resource_tenant_id=command.tenant_id,
            resource_attributes={"name": command.name, "scopes": list(command.scopes)},
        )
        tenant = await uow.tenants.get(command.tenant_id)
        if tenant is None:
            raise NotFound("tenant", command.tenant_id)
        if tenant.status != TenantStatus.ACTIVE:
            raise TenantSuspended(
                f"tenant is {tenant.status.value.lower()}",
                details={"tenant_id": str(command.tenant_id)},
            )

        existing = await uow.service_accounts.get_by_client_id(command.client_id)
        if existing is not None:
            raise DuplicateResource(resource_type="service_account", field="client_id")

        now = self._clock.now()
        raw_secret = generate_secure_secret(32)
        secret_hash = self._hasher.hash(raw_secret)

        sa = ServiceAccount.create(
            tenant_id=command.tenant_id,
            name=command.name,
            client_id=command.client_id,
            secret_hash=secret_hash,
            scopes=command.scopes,
            now=now,
        )
        await uow.service_accounts.add(sa)
        await uow.audit.record(
            AuditEntry(
                audit_id=str(uuid.uuid4()),
                tenant_id=str(command.tenant_id),
                actor_id=str(command.context.actor_id),
                action="identity.service_account.created",
                resource_type="service_account",
                resource_id=str(sa.id),
                correlation_id=command.context.correlation_id,
                causation_id=getattr(command.context, "causation_id", None),
                occurred_at=now,
                before=None,
                after={"name": sa.name, "client_id": sa.client_id, "scopes": list(sa.scopes)},
            )
        )

        view = ServiceAccountCreatedView(
            id=sa.id,
            tenant_id=sa.tenant_id,
            name=sa.name,
            client_id=sa.client_id,
            client_secret=raw_secret,
            status=sa.status,
            created_at=sa.created_at,
            scopes=sorted(sa.scopes),
        )
        return Executed(
            view=view,
            resource_type="service_account",
            resource_id=sa.id,
            events=(),
        )


class AuthenticateServiceAccount(
    UseCase[AuthenticateServiceAccountCommand, ServiceAccountTokensView, IdentityUnitOfWork]
):
    """Authenticate a service account using client_id and client_secret."""

    operation = "identity.authenticate_service_account"
    success_status = 200
    bypass_rls: ClassVar[bool] = True

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
        hasher: PasswordHasher,
        token_service: TokenService,
        events: EventDispatcher | None = None,
    ) -> None:
        super().__init__(uow_factory=uow_factory, clock=clock, events=events)
        self._hasher = hasher
        self._token_service = token_service

    async def _execute(
        self, uow: IdentityUnitOfWork, command: AuthenticateServiceAccountCommand
    ) -> Executed[ServiceAccountTokensView]:
        sa = await uow.service_accounts.get_by_client_id(command.client_id)
        if sa is None:
            raise AuthenticationFailed("invalid service account credentials")

        if not self._hasher.verify(command.client_secret, sa.secret_hash):
            raise AuthenticationFailed("invalid service account credentials")

        if sa.status != ServiceAccountStatus.ACTIVE:
            raise AccountDisabled(f"service account is {sa.status.value.lower()}")

        tenant = await uow.tenants.get(sa.tenant_id)
        if tenant is None or tenant.status != TenantStatus.ACTIVE:
            raise TenantSuspended("tenant is suspended or not found")

        claims = {
            "sub": str(sa.id),
            "type": "service_account",
            "client_id": sa.client_id,
            "tenant_id": str(sa.tenant_id),
            "scopes": sorted(sa.scopes),
            "token_version": sa.version,
        }
        access_token = self._token_service.create_access_token(claims=claims)

        view = ServiceAccountTokensView(
            access_token=access_token,
            token_type="bearer",
            expires_in=self._token_service.access_token_ttl_seconds,
            service_account=ServiceAccountView.of(sa),
        )
        return Executed(
            view=view,
            resource_type="service_account",
            resource_id=sa.id,
            events=(),
        )


class RevokeServiceAccount(
    UseCase[RevokeServiceAccountCommand, ServiceAccountView, IdentityUnitOfWork]
):
    """Disable/revoke a service account."""

    operation = "identity.revoke_service_account"
    permission = "access.roles.manage"
    resource_type = "service_account"
    success_status = 200

    async def _execute(
        self, uow: IdentityUnitOfWork, command: RevokeServiceAccountCommand
    ) -> Executed[ServiceAccountView]:
        await self._authorize(
            command.context,
            action="access.roles.manage",
            resource_type="service_account",
            resource_id=str(command.service_account_id),
            resource_tenant_id=command.tenant_id,
        )
        sa = await uow.service_accounts.get(command.service_account_id)
        if sa is None or sa.tenant_id != command.tenant_id:
            raise NotFound("service_account", command.service_account_id)

        now = self._clock.now()
        sa.disable(now=now)
        await uow.service_accounts.save(sa, expected_version=sa.version)
        await uow.audit.record(
            AuditEntry(
                audit_id=str(uuid.uuid4()),
                tenant_id=str(command.tenant_id),
                actor_id=str(command.context.actor_id),
                action="identity.service_account.revoked",
                resource_type="service_account",
                resource_id=str(sa.id),
                correlation_id=command.context.correlation_id,
                causation_id=getattr(command.context, "causation_id", None),
                occurred_at=now,
                before=None,
                after={"status": sa.status.value},
            )
        )

        return Executed(
            view=ServiceAccountView.of(sa),
            resource_type="service_account",
            resource_id=sa.id,
            events=(),
        )


class CreateInvitation(UseCase[CreateInvitationCommand, InvitationView, IdentityUnitOfWork]):
    """Issue a signed invitation token for tenant membership onboarding."""

    operation = "identity.create_invitation"
    permission = "access.roles.manage"
    resource_type = "invitation"
    success_status = 201

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
        token_service: TokenService,
        events: EventDispatcher | None = None,
        authorizer: Any | None = None,
    ) -> None:
        super().__init__(uow_factory=uow_factory, clock=clock, events=events, authorizer=authorizer)
        self._token_service = token_service

    async def _execute(
        self, uow: IdentityUnitOfWork, command: CreateInvitationCommand
    ) -> Executed[InvitationView]:
        await self._authorize(
            command.context,
            action="access.roles.manage",
            resource_type="invitation",
            resource_tenant_id=command.tenant_id,
            resource_attributes={"role": command.role},
        )
        tenant = await uow.tenants.get(command.tenant_id)
        if tenant is None:
            raise NotFound("tenant", command.tenant_id)
        if tenant.status != TenantStatus.ACTIVE:
            raise TenantSuspended(
                f"tenant is {tenant.status.value.lower()}",
                details={"tenant_id": str(command.tenant_id)},
            )

        email = normalize_email(command.email) if command.email else None
        role = MembershipRole(command.role)
        token = self._token_service.create_invitation_token(
            tenant_id=command.tenant_id,
            email=email,
            role=role.value,
        )
        view = InvitationView(
            invitation_token=token,
            tenant_id=command.tenant_id,
            email=email,
            role=role.value,
            expires_in_days=7,
        )
        return Executed(
            view=view,
            resource_type="invitation",
            resource_id=None,
            events=(),
        )


class ProvisionMembership(
    UseCase[ProvisionMembershipCommand, TenantMembershipView, IdentityUnitOfWork]
):
    """Directly provision a membership for an existing user in a tenant."""

    operation = "identity.provision_membership"
    permission = "access.roles.manage"
    resource_type = "tenant_membership"
    success_status = 201

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
        events: EventDispatcher | None = None,
        authorizer: Any | None = None,
    ) -> None:
        super().__init__(uow_factory=uow_factory, clock=clock, events=events, authorizer=authorizer)

    async def _execute(
        self, uow: IdentityUnitOfWork, command: ProvisionMembershipCommand
    ) -> Executed[TenantMembershipView]:
        await self._authorize(
            command.context,
            action="access.roles.manage",
            resource_type="tenant_membership",
            resource_tenant_id=command.tenant_id,
            resource_attributes={"role": command.role},
        )
        tenant = await uow.tenants.get(command.tenant_id)
        if tenant is None:
            raise NotFound("tenant", command.tenant_id)
        if tenant.status != TenantStatus.ACTIVE:
            raise TenantSuspended(
                f"tenant is {tenant.status.value.lower()}",
                details={"tenant_id": str(command.tenant_id)},
            )

        target_user: User | None = None
        if command.user_id is not None:
            target_user = await uow.users.get(command.user_id)
        elif command.email is not None:
            email = normalize_email(command.email)
            target_user = await uow.users.get_by_email(email)

        if target_user is None:
            raise NotFound("user", command.user_id or command.email or "unknown")

        existing_membership = await uow.memberships.get_by_user_and_tenant(
            target_user.id, command.tenant_id
        )
        if existing_membership is not None:
            raise DuplicateResource(resource_type="tenant_membership", field="user_id, tenant_id")

        now = self._clock.now()
        role = MembershipRole(command.role)
        membership = TenantMembership.create(
            user_id=target_user.id,
            tenant_id=command.tenant_id,
            role=role,
            now=now,
        )
        await uow.memberships.add(membership)
        await uow.audit.record(
            AuditEntry(
                audit_id=str(uuid.uuid4()),
                tenant_id=str(command.tenant_id),
                actor_id=str(command.context.actor_id),
                action="identity.membership.provisioned",
                resource_type="membership",
                resource_id=str(membership.id),
                correlation_id=command.context.correlation_id,
                causation_id=getattr(command.context, "causation_id", None),
                occurred_at=now,
                before=None,
                after={"user_id": str(membership.user_id), "role": membership.role.value},
            )
        )

        return Executed(
            view=TenantMembershipView.of(
                membership,
                tenant_name=tenant.name,
                tenant_slug=tenant.slug,
            ),
            resource_type="tenant_membership",
            resource_id=membership.id,
            events=(),
        )


class RotateServiceAccountSecret(
    UseCase[RotateServiceAccountSecretCommand, ServiceAccountCreatedView, IdentityUnitOfWork]
):
    """Rotate credentials for a service account."""

    operation = "identity.rotate_service_account"
    permission = "access.roles.manage"
    resource_type = "service_account"
    success_status = 200

    def __init__(
        self,
        *,
        uow_factory: Callable[[], IdentityUnitOfWork],
        clock: Clock,
        hasher: PasswordHasher,
        events: EventDispatcher | None = None,
        authorizer: Any | None = None,
    ) -> None:
        super().__init__(uow_factory=uow_factory, clock=clock, events=events, authorizer=authorizer)
        self._hasher = hasher

    async def _execute(
        self, uow: IdentityUnitOfWork, command: RotateServiceAccountSecretCommand
    ) -> Executed[ServiceAccountCreatedView]:
        await self._authorize(
            command.context,
            action="access.roles.manage",
            resource_type="service_account",
            resource_id=str(command.service_account_id),
            resource_tenant_id=command.tenant_id,
        )
        sa = await uow.service_accounts.get(command.service_account_id)
        if sa is None or sa.tenant_id != command.tenant_id:
            raise NotFound("service_account", command.service_account_id)

        now = self._clock.now()
        raw_secret = generate_secure_secret(32)
        secret_hash = self._hasher.hash(raw_secret)
        current_version = sa.version
        sa.rotate_secret(secret_hash, now=now)
        await uow.service_accounts.save(sa, expected_version=current_version)
        await uow.audit.record(
            AuditEntry(
                audit_id=str(uuid.uuid4()),
                tenant_id=str(command.tenant_id),
                actor_id=str(command.context.actor_id),
                action="identity.service_account.secret_rotated",
                resource_type="service_account",
                resource_id=str(sa.id),
                correlation_id=command.context.correlation_id,
                causation_id=getattr(command.context, "causation_id", None),
                occurred_at=now,
                before=None,
                after={"token_version": sa.version},
            )
        )

        view = ServiceAccountCreatedView(
            id=sa.id,
            tenant_id=sa.tenant_id,
            name=sa.name,
            client_id=sa.client_id,
            client_secret=raw_secret,
            status=sa.status,
            created_at=sa.created_at,
        )
        return Executed(
            view=view,
            resource_type="service_account",
            resource_id=sa.id,
            events=(),
        )


# ===========================================================================
# Roles & Policies Use Cases (Phase 4.2)
# ===========================================================================
class CreateRole(UseCase[CreateRoleCommand, RoleView, IdentityUnitOfWork]):
    """Create a custom tenant-scoped role."""

    operation = "identity.create_role"
    permission = "access.roles.manage"
    resource_type = "role"
    success_status = 201

    async def _execute(
        self, uow: IdentityUnitOfWork, command: CreateRoleCommand
    ) -> Executed[RoleView]:
        await self._authorize(
            command.context,
            action="access.roles.manage",
            resource_type="role",
            resource_tenant_id=command.tenant_id,
            resource_attributes={"name": command.name},
        )
        tenant = await uow.tenants.get(command.tenant_id)
        if tenant is None:
            raise NotFound("tenant", command.tenant_id)
        if tenant.status != TenantStatus.ACTIVE:
            raise TenantSuspended(
                f"tenant is {tenant.status.value.lower()}",
                details={"tenant_id": str(command.tenant_id)},
            )

        clean_name = command.name.strip().upper()
        if clean_name in RESERVED_ROLE_NAMES:
            raise InvalidValue(
                f"role name '{command.name}' is reserved for built-in system roles",
                details={"name": command.name, "reserved": sorted(RESERVED_ROLE_NAMES)},
            )

        existing = await uow.roles.get_by_name(command.name, tenant_id=command.tenant_id)
        if existing is not None:
            raise DuplicateResource(resource_type="role", field="name")

        now = self._clock.now()
        role = Role.create(
            name=command.name,
            description=command.description,
            permissions=command.permissions,
            tenant_id=command.tenant_id,
            is_system=False,
            now=now,
        )
        await uow.roles.add(role)
        await uow.audit.record(
            AuditEntry(
                audit_id=str(uuid.uuid4()),
                tenant_id=str(command.tenant_id),
                actor_id=str(command.context.actor_id),
                action="access.role.created",
                resource_type="role",
                resource_id=str(role.id),
                correlation_id=command.context.correlation_id,
                causation_id=getattr(command.context, "causation_id", None),
                occurred_at=now,
                before=None,
                after={"name": role.name, "description": role.description, "permissions": list(role.permissions)},
            )
        )

        view = RoleView(
            id=str(role.id),
            tenant_id=str(role.tenant_id) if role.tenant_id is not None else None,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            permissions=sorted(role.permissions),
            created_at=role.created_at,
            updated_at=role.updated_at,
            version=role.version,
        )
        return Executed(
            view=view,
            resource_type="role",
            resource_id=role.id,
            events=(),
        )


class GetRole(QueryUseCase[GetRoleQuery, RoleView, IdentityUnitOfWork]):
    """Fetch a role by ID."""

    permission = "access.roles.read"
    resource_type = "role"

    async def _execute(self, uow: IdentityUnitOfWork, command: GetRoleQuery) -> RoleView:
        try:
            role_uuid = uuid.UUID(command.role_id)
        except ValueError as exc:
            raise NotFound("role", command.role_id) from exc

        role = await uow.roles.get(RoleId(role_uuid))
        if role is None:
            raise NotFound("role", command.role_id)

        if (
            role.tenant_id is not None
            and command.context.tenant_id is not None
            and role.tenant_id != command.context.tenant_id
        ):
            raise NotFound("role", command.role_id)

        return RoleView(
            id=str(role.id),
            tenant_id=str(role.tenant_id) if role.tenant_id is not None else None,
            name=role.name,
            description=role.description,
            is_system=role.is_system,
            permissions=sorted(role.permissions),
            created_at=role.created_at,
            updated_at=role.updated_at,
            version=role.version,
        )


class ListRoles(QueryUseCase[ListRolesQuery, list[RoleView], IdentityUnitOfWork]):
    """List system and tenant-scoped roles."""

    permission = "access.roles.read"
    resource_type = "role"

    async def _execute(self, uow: IdentityUnitOfWork, command: ListRolesQuery) -> list[RoleView]:
        roles = await uow.roles.list_for_tenant(command.tenant_id, include_system=True)
        return [
            RoleView(
                id=str(role.id),
                tenant_id=str(role.tenant_id) if role.tenant_id is not None else None,
                name=role.name,
                description=role.description,
                is_system=role.is_system,
                permissions=sorted(role.permissions),
                created_at=role.created_at,
                updated_at=role.updated_at,
                version=role.version,
            )
            for role in roles
        ]


class AssignRole(UseCase[AssignRoleCommand, TenantMembershipView, IdentityUnitOfWork]):
    """Assign a role to a tenant membership."""

    operation = "identity.assign_role"
    permission = "access.roles.manage"
    resource_type = "tenant_membership"
    success_status = 200

    async def _execute(
        self, uow: IdentityUnitOfWork, command: AssignRoleCommand
    ) -> Executed[TenantMembershipView]:
        await self._authorize(
            command.context,
            action="access.roles.manage",
            resource_type="tenant_membership",
            resource_id=str(command.membership_id),
            resource_tenant_id=command.tenant_id,
            resource_attributes={"role": command.role_name},
        )
        tenant = await uow.tenants.get(command.tenant_id)
        if tenant is None:
            raise NotFound("tenant", command.tenant_id)

        membership = await uow.memberships.get(command.membership_id)
        if membership is None or membership.tenant_id != command.tenant_id:
            raise NotFound("tenant_membership", command.membership_id)

        role = await uow.roles.get_by_name(command.role_name, tenant_id=command.tenant_id)
        if role is None:
            raise NotFound("role", command.role_name)

        now = self._clock.now()
        membership.assign_role(role.name, now=now)
        await uow.memberships.save(membership, expected_version=membership.version)
        await uow.audit.record(
            AuditEntry(
                audit_id=str(uuid.uuid4()),
                tenant_id=str(command.tenant_id),
                actor_id=str(command.context.actor_id),
                action="access.role.assigned",
                resource_type="membership",
                resource_id=str(membership.id),
                correlation_id=command.context.correlation_id,
                causation_id=getattr(command.context, "causation_id", None),
                occurred_at=now,
                before=None,
                after={"role": role.name},
            )
        )

        view = TenantMembershipView.of(
            membership,
            tenant_name=tenant.name,
            tenant_slug=tenant.slug,
        )
        return Executed(
            view=view,
            resource_type="tenant_membership",
            resource_id=membership.id,
            events=(),
        )


class RevokeRole(UseCase[RevokeRoleCommand, TenantMembershipView, IdentityUnitOfWork]):
    """Revoke a role from a tenant membership."""

    operation = "identity.revoke_role"
    permission = "access.roles.manage"
    resource_type = "tenant_membership"
    success_status = 200

    async def _execute(
        self, uow: IdentityUnitOfWork, command: RevokeRoleCommand
    ) -> Executed[TenantMembershipView]:
        await self._authorize(
            command.context,
            action="access.roles.manage",
            resource_type="tenant_membership",
            resource_id=str(command.membership_id),
            resource_tenant_id=command.tenant_id,
            resource_attributes={"role": command.role_name},
        )
        tenant = await uow.tenants.get(command.tenant_id)
        if tenant is None:
            raise NotFound("tenant", command.tenant_id)

        membership = await uow.memberships.get(command.membership_id)
        if membership is None or membership.tenant_id != command.tenant_id:
            raise NotFound("tenant_membership", command.membership_id)

        clean_role = command.role_name.strip().upper()
        if clean_role == "ADMIN" and membership.is_admin and membership.is_active:
            all_memberships = await uow.memberships.list_for_tenant(command.tenant_id)
            other_active_admins = [
                m
                for m in all_memberships
                if m.id != membership.id and m.is_active and m.is_admin
            ]
            if not other_active_admins:
                raise CannotRemoveLastAdmin("cannot remove the last active administrator of a tenant")

        now = self._clock.now()
        membership.revoke_role(command.role_name, now=now)
        await uow.memberships.save(membership, expected_version=membership.version)
        await uow.audit.record(
            AuditEntry(
                audit_id=str(uuid.uuid4()),
                tenant_id=str(command.tenant_id),
                actor_id=str(command.context.actor_id),
                action="access.role.revoked",
                resource_type="membership",
                resource_id=str(membership.id),
                correlation_id=command.context.correlation_id,
                causation_id=getattr(command.context, "causation_id", None),
                occurred_at=now,
                before=None,
                after={"role": command.role_name},
            )
        )

        view = TenantMembershipView.of(
            membership,
            tenant_name=tenant.name,
            tenant_slug=tenant.slug,
        )
        return Executed(
            view=view,
            resource_type="tenant_membership",
            resource_id=membership.id,
            events=(),
        )


class CreatePolicy(UseCase[CreatePolicyCommand, PolicyView, IdentityUnitOfWork]):
    """Create an ABAC policy."""

    operation = "identity.create_policy"
    permission = "access.policies.manage"
    resource_type = "policy"
    success_status = 201

    async def _execute(
        self, uow: IdentityUnitOfWork, command: CreatePolicyCommand
    ) -> Executed[PolicyView]:
        await self._authorize(
            command.context,
            action="access.policies.manage",
            resource_type="policy",
            resource_tenant_id=command.tenant_id,
            resource_attributes={"name": command.name, "effect": command.effect},
        )
        tenant = await uow.tenants.get(command.tenant_id)
        if tenant is None:
            raise NotFound("tenant", command.tenant_id)
        if tenant.status != TenantStatus.ACTIVE:
            raise TenantSuspended(
                f"tenant is {tenant.status.value.lower()}",
                details={"tenant_id": str(command.tenant_id)},
            )

        now = self._clock.now()
        policy = AccessPolicy.create(
            name=command.name,
            description=command.description,
            effect=PolicyEffect(command.effect.upper()),
            actions=command.actions,
            resources=command.resources,
            conditions=command.conditions,
            tenant_id=command.tenant_id,
            now=now,
        )
        await uow.policies.add(policy)
        await uow.audit.record(
            AuditEntry(
                audit_id=str(uuid.uuid4()),
                tenant_id=str(command.tenant_id),
                actor_id=str(command.context.actor_id),
                action="access.policy.created",
                resource_type="policy",
                resource_id=str(policy.id),
                correlation_id=command.context.correlation_id,
                causation_id=getattr(command.context, "causation_id", None),
                occurred_at=now,
                before=None,
                after={
                    "name": policy.name,
                    "effect": policy.effect.value,
                    "actions": list(policy.actions),
                    "resources": list(policy.resources),
                },
            )
        )

        view = PolicyView(
            id=str(policy.id),
            tenant_id=str(policy.tenant_id) if policy.tenant_id is not None else None,
            name=policy.name,
            description=policy.description,
            effect=policy.effect.value,
            actions=list(policy.actions),
            resources=list(policy.resources),
            conditions=policy.conditions,
            status=policy.status.value,
            created_at=policy.created_at,
            updated_at=policy.updated_at,
            version=policy.version,
        )
        return Executed(
            view=view,
            resource_type="access_policy",
            resource_id=policy.id,
            events=(),
        )


class GetPolicy(QueryUseCase[GetPolicyQuery, PolicyView, IdentityUnitOfWork]):
    """Fetch an ABAC policy by ID."""

    permission = "access.policies.read"
    resource_type = "policy"

    async def _execute(self, uow: IdentityUnitOfWork, command: GetPolicyQuery) -> PolicyView:
        try:
            policy_uuid = uuid.UUID(command.policy_id)
        except ValueError as exc:
            raise NotFound("access_policy", command.policy_id) from exc

        policy = await uow.policies.get(PolicyId(policy_uuid))
        if policy is None:
            raise NotFound("access_policy", command.policy_id)

        if (
            policy.tenant_id is not None
            and command.context.tenant_id is not None
            and policy.tenant_id != command.context.tenant_id
        ):
            raise NotFound("access_policy", command.policy_id)

        return PolicyView(
            id=str(policy.id),
            tenant_id=str(policy.tenant_id) if policy.tenant_id is not None else None,
            name=policy.name,
            description=policy.description,
            effect=policy.effect.value,
            actions=list(policy.actions),
            resources=list(policy.resources),
            conditions=policy.conditions,
            status=policy.status.value,
            created_at=policy.created_at,
            updated_at=policy.updated_at,
            version=policy.version,
        )


class ListPolicies(QueryUseCase[ListPoliciesQuery, list[PolicyView], IdentityUnitOfWork]):
    """List ABAC policies for a tenant."""

    permission = "access.policies.read"
    resource_type = "policy"

    async def _execute(
        self, uow: IdentityUnitOfWork, command: ListPoliciesQuery
    ) -> list[PolicyView]:
        policies = await uow.policies.list_for_tenant(
            command.tenant_id, active_only=command.active_only
        )
        return [
            PolicyView(
                id=str(policy.id),
                tenant_id=str(policy.tenant_id) if policy.tenant_id is not None else None,
                name=policy.name,
                description=policy.description,
                effect=policy.effect.value,
                actions=list(policy.actions),
                resources=list(policy.resources),
                conditions=policy.conditions,
                status=policy.status.value,
                created_at=policy.created_at,
                updated_at=policy.updated_at,
                version=policy.version,
            )
            for policy in policies
        ]


class UpdatePolicyStatus(UseCase[UpdatePolicyStatusCommand, PolicyView, IdentityUnitOfWork]):
    """Activate or deactivate an ABAC policy."""

    operation = "identity.update_policy_status"
    permission = "access.policies.manage"
    resource_type = "policy"
    success_status = 200

    async def _execute(
        self, uow: IdentityUnitOfWork, command: UpdatePolicyStatusCommand
    ) -> Executed[PolicyView]:
        await self._authorize(
            command.context,
            action="access.policies.manage",
            resource_type="policy",
            resource_id=str(command.policy_id),
            resource_tenant_id=command.tenant_id,
            resource_attributes={"status": command.status},
        )
        try:
            policy_uuid = uuid.UUID(command.policy_id)
        except ValueError as exc:
            raise NotFound("access_policy", command.policy_id) from exc

        policy = await uow.policies.get(PolicyId(policy_uuid))
        if policy is None or policy.tenant_id != command.tenant_id:
            raise NotFound("access_policy", command.policy_id)

        now = self._clock.now()
        target_status = command.status.upper()
        if target_status == "ACTIVE":
            policy.activate(now=now)
        elif target_status == "INACTIVE":
            policy.deactivate(now=now)
        else:
            from shared.domain.errors import InvalidValue

            raise InvalidValue(
                "status must be ACTIVE or INACTIVE",
                details={"status": command.status},
            )

        await uow.policies.save(policy, expected_version=policy.version)
        await uow.audit.record(
            AuditEntry(
                audit_id=str(uuid.uuid4()),
                tenant_id=str(command.tenant_id),
                actor_id=str(command.context.actor_id),
                action="access.policy.status_updated",
                resource_type="policy",
                resource_id=str(policy.id),
                correlation_id=command.context.correlation_id,
                causation_id=getattr(command.context, "causation_id", None),
                occurred_at=now,
                before=None,
                after={"status": policy.status.value},
            )
        )

        view = PolicyView(
            id=str(policy.id),
            tenant_id=str(policy.tenant_id),
            name=policy.name,
            description=policy.description,
            effect=policy.effect.value,
            actions=list(policy.actions),
            resources=list(policy.resources),
            conditions=policy.conditions,
            status=policy.status.value,
            created_at=policy.created_at,
            updated_at=policy.updated_at,
            version=policy.version,
        )
        return Executed(
            view=view,
            resource_type="access_policy",
            resource_id=policy.id,
            events=(),
        )


__all__ = [
    "AssignRole",
    "AuthenticateServiceAccount",
    "AuthenticateUser",
    "ChangePassword",
    "CreateInvitation",
    "CreatePolicy",
    "CreateRole",
    "CreateServiceAccount",
    "GetPolicy",
    "GetRole",
    "GetTenant",
    "ListMemberships",
    "ListPolicies",
    "ListRoles",
    "ProvisionMembership",
    "RefreshToken",
    "RegisterTenant",
    "RegisterUser",
    "RevokeRole",
    "RevokeServiceAccount",
    "RevokeSession",
    "RotateServiceAccountSecret",
    "SelectTenant",
    "UpdatePolicyStatus",
]
