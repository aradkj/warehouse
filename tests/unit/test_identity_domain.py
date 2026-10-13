"""Unit tests for Identity domain entities and value objects."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from identity.domain.membership import MembershipStatus, TenantMembership
from identity.domain.principal import Principal
from identity.domain.service_account import ServiceAccount, ServiceAccountStatus
from identity.domain.session import UserSession
from identity.domain.user import User, UserStatus, normalize_email
from shared.application.context import ActorType
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import MembershipId, TenantId, UserId


def test_normalize_email() -> None:
    assert normalize_email("  Test.User@Example.COM  ") == "test.user@example.com"
    with pytest.raises(InvalidValue):
        normalize_email("not-an-email")
    with pytest.raises(InvalidValue):
        normalize_email("")
    with pytest.raises(InvalidValue):
        normalize_email("a" * 250 + "@example.com")


def test_user_creation_and_state_transitions() -> None:
    now = datetime.now(UTC)
    user = User.create(
        email="operator@arad.io",
        password_hash="hashed_secret",
        now=now,
    )
    assert user.email == "operator@arad.io"
    assert user.password_hash == "hashed_secret"
    assert user.status == UserStatus.ACTIVE
    assert user.is_active is True
    assert user.version == 1

    # Disable
    now_later = now + timedelta(minutes=5)
    user.disable(now=now_later)
    assert user.status == UserStatus.DISABLED
    assert user.is_active is False
    assert user.updated_at == now_later

    # Lock
    user.lock(now=now_later)
    assert user.status == UserStatus.LOCKED
    assert user.is_active is False

    # Reactivate
    user.activate(now=now_later)
    assert user.status == UserStatus.ACTIVE
    assert user.is_active is True

    # Change password
    user.change_password("new_hashed_secret", now=now_later)
    assert user.password_hash == "new_hashed_secret"


def test_membership_creation_and_transitions() -> None:
    now = datetime.now(UTC)
    user_id = UserId(uuid.uuid4())
    tenant_id = TenantId(uuid.uuid4())

    membership = TenantMembership.create(user_id=user_id, tenant_id=tenant_id, now=now)
    assert membership.user_id == user_id
    assert membership.tenant_id == tenant_id
    assert membership.status == MembershipStatus.ACTIVE
    assert membership.is_active is True
    assert membership.version == 1

    later = now + timedelta(minutes=10)
    membership.disable(now=later)
    assert membership.status == MembershipStatus.DISABLED
    assert membership.is_active is False
    assert membership.updated_at == later

    membership.activate(now=later)
    assert membership.status == MembershipStatus.ACTIVE
    assert membership.is_active is True


def test_service_account_creation_and_transitions() -> None:
    now = datetime.now(UTC)
    tenant_id = TenantId(uuid.uuid4())

    sa = ServiceAccount.create(
        tenant_id=tenant_id,
        name="Inventory Sync Service",
        client_id="inv-sync-01",
        secret_hash="hashed_sa_secret",
        now=now,
    )
    assert sa.tenant_id == tenant_id
    assert sa.name == "Inventory Sync Service"
    assert sa.client_id == "inv-sync-01"
    assert sa.secret_hash == "hashed_sa_secret"
    assert sa.status == ServiceAccountStatus.ACTIVE
    assert sa.is_active is True

    later = now + timedelta(hours=1)
    sa.disable(now=later)
    assert sa.status == ServiceAccountStatus.DISABLED
    assert sa.is_active is False
    assert sa.updated_at == later

    sa.activate(now=later)
    assert sa.status == ServiceAccountStatus.ACTIVE
    assert sa.is_active is True


def test_service_account_invalid_inputs() -> None:
    now = datetime.now(UTC)
    tenant_id = TenantId(uuid.uuid4())
    with pytest.raises(InvalidValue):
        ServiceAccount.create(
            tenant_id=tenant_id,
            name="",
            client_id="valid-id",
            secret_hash="secret",
            now=now,
        )
    with pytest.raises(InvalidValue):
        ServiceAccount.create(
            tenant_id=tenant_id,
            name="Valid Name",
            client_id="id with spaces!",
            secret_hash="secret",
            now=now,
        )


def test_user_session_lifecycle() -> None:
    now = datetime.now(UTC)
    user_id = UserId(uuid.uuid4())
    expires_at = now + timedelta(days=7)

    session = UserSession.create(
        user_id=user_id,
        token_hash="hashed_refresh_token",
        expires_at=expires_at,
        now=now,
    )
    assert session.user_id == user_id
    assert session.token_hash == "hashed_refresh_token"
    assert session.is_revoked is False
    assert session.rotated_at is None
    assert session.is_valid(now) is True
    assert session.is_expired(now) is False
    assert session.is_expired(expires_at + timedelta(seconds=1)) is True

    # Rotate session
    rotated_time = now + timedelta(days=1)
    session.rotate(now=rotated_time)
    assert session.rotated_at == rotated_time

    # Revoke session
    session.revoke()
    assert session.is_revoked is True
    assert session.is_valid(now) is False


def test_principal_to_request_context() -> None:
    user_id = str(uuid.uuid4())
    tenant_id = TenantId(uuid.uuid4())
    membership_id = MembershipId(uuid.uuid4())

    principal = Principal(
        id=user_id,
        type=ActorType.USER,
        email="test@arad.io",
        active_tenant_id=tenant_id,
        active_membership_id=membership_id,
    )

    ctx = principal.to_request_context(correlation_id="corr-123", idempotency_key="idemp-abc")
    assert ctx.actor_id == user_id
    assert ctx.actor_type == ActorType.USER
    assert ctx.tenant_id == tenant_id
    assert ctx.correlation_id == "corr-123"
    assert ctx.idempotency_key == "idemp-abc"
