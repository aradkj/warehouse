"""Application tests for Identity & Authentication use cases."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from tests.fakes.application import build_fake_application

from identity.application.commands import (
    AuthenticateServiceAccountCommand,
    AuthenticateUserCommand,
    ChangePasswordCommand,
    CreateInvitationCommand,
    CreateServiceAccountCommand,
    ListMembershipsQuery,
    ProvisionMembershipCommand,
    RefreshTokenCommand,
    RegisterTenantCommand,
    RegisterUserCommand,
    RevokeServiceAccountCommand,
    RevokeSessionCommand,
    RotateServiceAccountSecretCommand,
    SelectTenantCommand,
)
from identity.domain.membership import MembershipRole, MembershipStatus
from identity.domain.tenant import TenantStatus
from identity.domain.user import UserStatus
from shared.application.context import RequestContext
from shared.application.errors import (
    AccountDisabled,
    AuthenticationFailed,
    DuplicateResource,
    Forbidden,
    TenantSuspended,
)
from shared.domain.identifiers import TenantId


def _context(*, tenant_id: TenantId | None = None) -> RequestContext:
    return RequestContext(tenant_id=tenant_id, actor_id="test-actor")


class TestUserRegistration:
    async def test_register_user_success(self) -> None:
        app = build_fake_application()
        cmd = RegisterUserCommand(
            context=_context(),
            email="admin@arad.io",
            password="StrongPassword123!",
        )
        outcome = await app.use_cases.register_user(cmd)
        user = outcome.view
        assert user.email == "admin@arad.io"
        assert user.status == UserStatus.ACTIVE

        # Verify password in store is hashed, never plaintext
        stored = app.stores.users[user.id]
        assert stored.password_hash != "StrongPassword123!"
        assert len(stored.password_hash) > 20

    async def test_register_user_with_tenant_membership_via_invitation(self) -> None:
        app = build_fake_application()
        t_outcome = await app.use_cases.register_tenant(
            RegisterTenantCommand(context=_context(), slug="tenant-alpha", name="Alpha Corp")
        )
        tenant_id = t_outcome.view.id

        inv_outcome = await app.use_cases.create_invitation(
            CreateInvitationCommand(
                context=_context(tenant_id=tenant_id),
                tenant_id=tenant_id,
                email="member@arad.io",
                role="ADMIN",
            )
        )

        cmd = RegisterUserCommand(
            context=_context(),
            email="member@arad.io",
            password="StrongPassword123!",
            tenant_id=tenant_id,
            invitation_token=inv_outcome.view.invitation_token,
        )
        outcome = await app.use_cases.register_user(cmd)
        user = outcome.view

        # Membership was created with role ADMIN
        memberships = list(app.stores.memberships.values())
        assert len(memberships) == 1
        assert memberships[0].user_id == user.id
        assert memberships[0].tenant_id == tenant_id
        assert memberships[0].status == MembershipStatus.ACTIVE
        assert memberships[0].role == MembershipRole.ADMIN

    async def test_anonymous_user_cannot_join_tenant_without_invitation(self) -> None:
        app = build_fake_application()
        t_outcome = await app.use_cases.register_tenant(
            RegisterTenantCommand(context=_context(), slug="tenant-alpha", name="Alpha Corp")
        )
        tenant_id = t_outcome.view.id

        cmd = RegisterUserCommand(
            context=_context(),
            email="attacker@arad.io",
            password="StrongPassword123!",
            tenant_id=tenant_id,
        )
        with pytest.raises(
            Forbidden, match="joining an existing tenant requires a valid invitation token"
        ):
            await app.use_cases.register_user(cmd)

    async def test_invitation_with_email_mismatch_fails(self) -> None:
        app = build_fake_application()
        t_outcome = await app.use_cases.register_tenant(
            RegisterTenantCommand(context=_context(), slug="tenant-alpha", name="Alpha Corp")
        )
        tenant_id = t_outcome.view.id

        inv_outcome = await app.use_cases.create_invitation(
            CreateInvitationCommand(
                context=_context(tenant_id=tenant_id),
                tenant_id=tenant_id,
                email="alice@arad.io",
                role="MEMBER",
            )
        )

        cmd = RegisterUserCommand(
            context=_context(),
            email="bob@arad.io",
            password="StrongPassword123!",
            tenant_id=tenant_id,
            invitation_token=inv_outcome.view.invitation_token,
        )
        with pytest.raises(
            Forbidden, match="invitation token was issued for a different email address"
        ):
            await app.use_cases.register_user(cmd)

    async def test_duplicate_email_fails(self) -> None:
        app = build_fake_application()
        cmd = RegisterUserCommand(
            context=_context(),
            email="duplicate@arad.io",
            password="StrongPassword123!",
        )
        await app.use_cases.register_user(cmd)
        with pytest.raises(DuplicateResource):
            await app.use_cases.register_user(cmd)

    async def test_register_with_non_existent_tenant_fails(self) -> None:
        app = build_fake_application()
        cmd = RegisterUserCommand(
            context=_context(),
            email="user@arad.io",
            password="StrongPassword123!",
            tenant_id=TenantId(uuid.uuid4()),
        )
        with pytest.raises(
            Forbidden, match="joining an existing tenant requires a valid invitation token"
        ):
            await app.use_cases.register_user(cmd)

    async def test_register_with_suspended_tenant_fails(self) -> None:
        app = build_fake_application()
        t_outcome = await app.use_cases.register_tenant(
            RegisterTenantCommand(context=_context(), slug="tenant-beta", name="Beta Corp")
        )
        inv_outcome = await app.use_cases.create_invitation(
            CreateInvitationCommand(
                context=_context(tenant_id=t_outcome.view.id),
                tenant_id=t_outcome.view.id,
                email="user@arad.io",
            )
        )

        tenant = app.stores.tenants[t_outcome.view.id]
        tenant._status = TenantStatus.SUSPENDED

        cmd = RegisterUserCommand(
            context=_context(),
            email="user@arad.io",
            password="StrongPassword123!",
            tenant_id=tenant.id,
            invitation_token=inv_outcome.view.invitation_token,
        )
        with pytest.raises(TenantSuspended):
            await app.use_cases.register_user(cmd)


class TestAuthenticationAndSessions:
    async def test_authenticate_success_and_token_issue(self) -> None:
        app = build_fake_application()
        await app.use_cases.register_user(
            RegisterUserCommand(
                context=_context(),
                email="auth@arad.io",
                password="CorrectPassword123!",
            )
        )

        outcome = await app.use_cases.authenticate_user(
            AuthenticateUserCommand(
                context=_context(),
                email="auth@arad.io",
                password="CorrectPassword123!",
            )
        )
        tokens = outcome.view
        assert tokens.access_token is not None
        assert tokens.refresh_token is not None
        assert tokens.token_type == "bearer"
        assert tokens.expires_in == 900
        assert tokens.user.email == "auth@arad.io"

        # Verify a session was persisted in the store
        assert len(app.stores.sessions) == 1

    async def test_authenticate_invalid_credentials_rejected(self) -> None:
        app = build_fake_application()
        await app.use_cases.register_user(
            RegisterUserCommand(
                context=_context(),
                email="auth@arad.io",
                password="CorrectPassword123!",
            )
        )

        # Wrong password
        with pytest.raises(AuthenticationFailed, match="invalid email or password"):
            await app.use_cases.authenticate_user(
                AuthenticateUserCommand(
                    context=_context(),
                    email="auth@arad.io",
                    password="WrongPassword999!",
                )
            )

        # Non-existent user
        with pytest.raises(AuthenticationFailed, match="invalid email or password"):
            await app.use_cases.authenticate_user(
                AuthenticateUserCommand(
                    context=_context(),
                    email="unknown@arad.io",
                    password="AnyPassword123!",
                )
            )

    async def test_disabled_user_cannot_authenticate(self) -> None:
        app = build_fake_application()
        u_outcome = await app.use_cases.register_user(
            RegisterUserCommand(
                context=_context(),
                email="disabled@arad.io",
                password="CorrectPassword123!",
            )
        )
        user = app.stores.users[u_outcome.view.id]
        user.disable(now=datetime.now(UTC))

        with pytest.raises(AccountDisabled):
            await app.use_cases.authenticate_user(
                AuthenticateUserCommand(
                    context=_context(),
                    email="disabled@arad.io",
                    password="CorrectPassword123!",
                )
            )

    async def test_login_with_tenant_scoping_validation(self) -> None:
        app = build_fake_application()
        t1 = (
            await app.use_cases.register_tenant(
                RegisterTenantCommand(context=_context(), slug="tenant-1", name="Tenant 1")
            )
        ).view.id
        t2 = (
            await app.use_cases.register_tenant(
                RegisterTenantCommand(context=_context(), slug="tenant-2", name="Tenant 2")
            )
        ).view.id

        invitation = app.token_service.create_invitation_token(tenant_id=t1)
        await app.use_cases.register_user(
            RegisterUserCommand(
                context=_context(),
                email="multi@arad.io",
                password="Password123!",
                tenant_id=t1,
                invitation_token=invitation,
            )
        )

        # User is member of t1 -> login scoped to t1 succeeds
        outcome = await app.use_cases.authenticate_user(
            AuthenticateUserCommand(
                context=_context(),
                email="multi@arad.io",
                password="Password123!",
                tenant_id=t1,
            )
        )
        assert outcome.view.active_tenant_id == t1

        # User is NOT member of t2 -> login scoped to t2 is forbidden
        with pytest.raises(Forbidden, match="user is not a member of the requested tenant"):
            await app.use_cases.authenticate_user(
                AuthenticateUserCommand(
                    context=_context(),
                    email="multi@arad.io",
                    password="Password123!",
                    tenant_id=t2,
                )
            )

    async def test_refresh_token_rotation_and_reuse_detection(self) -> None:
        app = build_fake_application()
        await app.use_cases.register_user(
            RegisterUserCommand(
                context=_context(),
                email="refresh@arad.io",
                password="Password123!",
            )
        )
        login_view = (
            await app.use_cases.authenticate_user(
                AuthenticateUserCommand(
                    context=_context(),
                    email="refresh@arad.io",
                    password="Password123!",
                )
            )
        ).view

        raw_refresh_1 = login_view.refresh_token

        # First refresh: succeeds and rotates
        refresh_1_outcome = await app.use_cases.refresh_token(
            RefreshTokenCommand(context=_context(), refresh_token=raw_refresh_1)
        )
        tokens_2 = refresh_1_outcome.view
        raw_refresh_2 = tokens_2.refresh_token
        assert raw_refresh_2 != raw_refresh_1

        # Second refresh using new token: succeeds
        refresh_2_outcome = await app.use_cases.refresh_token(
            RefreshTokenCommand(context=_context(), refresh_token=raw_refresh_2)
        )
        assert refresh_2_outcome.view.refresh_token != raw_refresh_2

        # REUSE DETECTION: Attempting to use the already rotated raw_refresh_1 again!
        with pytest.raises(AuthenticationFailed, match="reuse detected"):
            await app.use_cases.refresh_token(
                RefreshTokenCommand(context=_context(), refresh_token=raw_refresh_1)
            )

        # Entire family revoked: even raw_refresh_2 or the latest token cannot be used now!
        with pytest.raises(AuthenticationFailed, match="revoked"):
            await app.use_cases.refresh_token(
                RefreshTokenCommand(context=_context(), refresh_token=raw_refresh_2)
            )

    async def test_logout_revokes_session(self) -> None:
        app = build_fake_application()
        await app.use_cases.register_user(
            RegisterUserCommand(
                context=_context(),
                email="logout@arad.io",
                password="Password123!",
            )
        )
        login_view = (
            await app.use_cases.authenticate_user(
                AuthenticateUserCommand(
                    context=_context(),
                    email="logout@arad.io",
                    password="Password123!",
                )
            )
        ).view

        # Logout
        await app.use_cases.revoke_session(
            RevokeSessionCommand(context=_context(), refresh_token=login_view.refresh_token)
        )

        # Session is now revoked; refresh fails
        with pytest.raises(AuthenticationFailed, match="revoked"):
            await app.use_cases.refresh_token(
                RefreshTokenCommand(context=_context(), refresh_token=login_view.refresh_token)
            )

    async def test_change_password_invalidates_all_sessions(self) -> None:
        app = build_fake_application()
        u = (
            await app.use_cases.register_user(
                RegisterUserCommand(
                    context=_context(),
                    email="pwd@arad.io",
                    password="OldPassword123!",
                )
            )
        ).view

        login_view = (
            await app.use_cases.authenticate_user(
                AuthenticateUserCommand(
                    context=_context(),
                    email="pwd@arad.io",
                    password="OldPassword123!",
                )
            )
        ).view

        # Wrong current password fails
        with pytest.raises(AuthenticationFailed, match="current password is incorrect"):
            await app.use_cases.change_password(
                ChangePasswordCommand(
                    context=_context(),
                    user_id=u.id,
                    current_password="WrongCurrentPassword!",
                    new_password="NewPassword123!",
                )
            )

        # Correct current password changes password
        await app.use_cases.change_password(
            ChangePasswordCommand(
                context=_context(),
                user_id=u.id,
                current_password="OldPassword123!",
                new_password="NewPassword123!",
            )
        )

        # Old password no longer works
        with pytest.raises(AuthenticationFailed):
            await app.use_cases.authenticate_user(
                AuthenticateUserCommand(
                    context=_context(),
                    email="pwd@arad.io",
                    password="OldPassword123!",
                )
            )

        # New password works
        new_login = await app.use_cases.authenticate_user(
            AuthenticateUserCommand(
                context=_context(),
                email="pwd@arad.io",
                password="NewPassword123!",
            )
        )
        assert new_login.view.user.email == "pwd@arad.io"

        # Previous sessions were revoked on password change
        with pytest.raises(AuthenticationFailed, match="revoked"):
            await app.use_cases.refresh_token(
                RefreshTokenCommand(context=_context(), refresh_token=login_view.refresh_token)
            )


class TestMembershipsAndTenantSelection:
    async def test_list_memberships_and_select_tenant(self) -> None:
        app = build_fake_application()
        t1 = (
            await app.use_cases.register_tenant(
                RegisterTenantCommand(context=_context(), slug="alpha", name="Alpha Corp")
            )
        ).view.id
        t2 = (
            await app.use_cases.register_tenant(
                RegisterTenantCommand(context=_context(), slug="beta", name="Beta Corp")
            )
        ).view.id

        u = (
            await app.use_cases.register_user(
                RegisterUserCommand(
                    context=_context(),
                    email="user@alpha-beta.io",
                    password="Password123!",
                )
            )
        ).view

        # Provision first membership in t1
        m1 = (
            await app.use_cases.provision_membership(
                ProvisionMembershipCommand(
                    context=_context(tenant_id=t1),
                    tenant_id=t1,
                    user_id=u.id,
                    role="ADMIN",
                )
            )
        ).view
        assert m1.role == "ADMIN"

        # Provision second membership in t2
        m2 = (
            await app.use_cases.provision_membership(
                ProvisionMembershipCommand(
                    context=_context(tenant_id=t2),
                    tenant_id=t2,
                    user_id=u.id,
                    role="MEMBER",
                )
            )
        ).view
        assert m2.role == "MEMBER"

        # List memberships
        memberships = await app.use_cases.list_memberships(
            ListMembershipsQuery(context=_context(), user_id=u.id)
        )
        assert len(memberships) == 2
        tenant_ids = {m.tenant_id for m in memberships}
        assert tenant_ids == {t1, t2}

        # Select tenant t2
        outcome = await app.use_cases.select_tenant(
            SelectTenantCommand(context=_context(), user_id=u.id, tenant_id=t2)
        )
        assert outcome.view.active_tenant_id == t2
        assert outcome.view.active_membership_id == m2.id


class TestServiceAccounts:
    async def test_service_account_lifecycle_and_rotation(self) -> None:
        app = build_fake_application()
        tenant_id = (
            await app.use_cases.register_tenant(
                RegisterTenantCommand(context=_context(), slug="sa-tenant", name="SA Tenant")
            )
        ).view.id

        # Create service account
        create_outcome = await app.use_cases.create_service_account(
            CreateServiceAccountCommand(
                tenant_id=tenant_id,
                context=_context(tenant_id=tenant_id),
                name="Fulfillment Machine",
                client_id="fulfill-bot",
            )
        )
        sa_view = create_outcome.view
        assert sa_view.client_id == "fulfill-bot"
        assert sa_view.client_secret is not None
        assert len(sa_view.client_secret) >= 32

        # Authenticate service account
        auth_outcome = await app.use_cases.authenticate_service_account(
            AuthenticateServiceAccountCommand(
                context=_context(),
                client_id="fulfill-bot",
                client_secret=sa_view.client_secret,
            )
        )
        tokens = auth_outcome.view
        assert tokens.access_token is not None
        assert tokens.service_account.client_id == "fulfill-bot"

        # Wrong secret fails
        with pytest.raises(AuthenticationFailed, match="invalid service account credentials"):
            await app.use_cases.authenticate_service_account(
                AuthenticateServiceAccountCommand(
                    context=_context(),
                    client_id="fulfill-bot",
                    client_secret="wrong-secret",
                )
            )

        # Rotate service account secret
        rotate_outcome = await app.use_cases.rotate_service_account(
            RotateServiceAccountSecretCommand(
                tenant_id=tenant_id,
                context=_context(tenant_id=tenant_id),
                service_account_id=sa_view.id,
            )
        )
        new_secret = rotate_outcome.view.client_secret
        assert new_secret != sa_view.client_secret

        # Old secret fails after rotation
        with pytest.raises(AuthenticationFailed, match="invalid service account credentials"):
            await app.use_cases.authenticate_service_account(
                AuthenticateServiceAccountCommand(
                    context=_context(),
                    client_id="fulfill-bot",
                    client_secret=sa_view.client_secret,
                )
            )

        # New secret succeeds
        new_auth = await app.use_cases.authenticate_service_account(
            AuthenticateServiceAccountCommand(
                context=_context(),
                client_id="fulfill-bot",
                client_secret=new_secret,
            )
        )
        assert new_auth.view.access_token is not None

        # Revoke service account
        await app.use_cases.revoke_service_account(
            RevokeServiceAccountCommand(
                tenant_id=tenant_id,
                context=_context(tenant_id=tenant_id),
                service_account_id=sa_view.id,
            )
        )

        # Authenticate revoked SA fails
        with pytest.raises(AccountDisabled, match="disabled"):
            await app.use_cases.authenticate_service_account(
                AuthenticateServiceAccountCommand(
                    context=_context(),
                    client_id="fulfill-bot",
                    client_secret=new_secret,
                )
            )


class TestPasswordRehashUpgrade:
    async def test_authenticate_rehashes_legacy_scrypt_to_argon2(self) -> None:
        import base64
        import hashlib
        import os

        app = build_fake_application()

        # Create user directly with a legacy $scrypt$ hash
        password = "LegacyPassword123!"
        salt = os.urandom(16)
        key = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=16384,
            r=8,
            p=1,
            maxmem=32 * 1024 * 1024,
        )
        salt_b64 = base64.b64encode(salt).decode("ascii")
        key_b64 = base64.b64encode(key).decode("ascii")
        scrypt_hash = f"$scrypt$16384$8$1${salt_b64}${key_b64}"

        from identity.domain.user import User

        now = datetime.now(UTC)
        user = User.create(
            email="legacy@arad.io",
            password_hash=scrypt_hash,
            now=now,
        )
        app.stores.users[user.id] = user
        app.stores.versions[user.id] = user.version
        assert app.stores.users[user.id].password_hash.startswith("$scrypt$")

        # Authenticate with the password
        outcome = await app.use_cases.authenticate_user(
            AuthenticateUserCommand(
                context=_context(),
                email="legacy@arad.io",
                password=password,
            )
        )
        assert outcome.view.user.email == "legacy@arad.io"

        # The stored password hash should have been upgraded to Argon2id
        upgraded_user = app.stores.users[user.id]
        assert upgraded_user.password_hash.startswith("$argon2")
