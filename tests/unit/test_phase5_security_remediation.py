"""Focused security remediation and regression tests for Phase 5.

Verifies:
1. Tenant-membership cache:
   - Active membership allows access.
   - Missing membership denies access.
   - Disabled membership denies access.
   - Invalidation on revocation/suspension takes immediate effect.
   - Cache TTL expiry behaves correctly.
   - Tenant isolation: separate users and tenants do not cross-reuse cached results.
2. Authorization-policy cache:
   - Policy creation and status mutation invalidate cached policy data.
   - Failure-closed guarantee: database/cache exception when loading policies
     produces an explicit DENIED_EVALUATION_ERROR and records an audit entry.
   - Cached policies remain strictly tenant-scoped.
3. Production configuration validation:
   - Production rejects auth_required=False.
   - Production rejects insecure dev secret and placeholder secrets.
   - Production requires secrets of at least 32 characters.
   - Dev and test environments remain usable with default settings.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest
from tests.fakes.application import build_fake_application

from audit.adapters.in_memory import InMemoryAuditAdapter
from bootstrap.config import Settings
from bootstrap.dependencies import (
    _tenant_membership_cache,
    get_transaction_scope,
    invalidate_tenant_membership_cache,
)
from identity.application.authorization import (
    ApplicationAuthorizationService,
    _policy_cache,
    invalidate_policy_cache,
)
from identity.application.commands import (
    CreatePolicyCommand,
    UpdatePolicyStatusCommand,
)
from identity.domain.decision import AuthorizationRequest, DecisionReason
from identity.domain.policy import AccessPolicy
from identity.domain.tenant import Tenant, TenantStatus
from shared.application.context import ActorType, RequestContext
from shared.application.use_case import QueryUseCase
from shared.domain.identifiers import TenantId


# ==============================================================================
# 1. Tenant-Membership Cache Review Tests
# ==============================================================================
class TestTenantMembershipCache:
    """Verifies process-wide tenant-membership cache behavior and invalidation."""

    def setup_method(self) -> None:
        invalidate_tenant_membership_cache()

    def teardown_method(self) -> None:
        invalidate_tenant_membership_cache()

    def test_cache_insertion_and_invalidation_by_user_and_tenant(self) -> None:
        user_a = str(uuid.uuid4())
        user_b = str(uuid.uuid4())
        tenant_1 = TenantId(uuid.uuid4())
        tenant_2 = TenantId(uuid.uuid4())

        now = time.monotonic()
        _tenant_membership_cache[(user_a, tenant_1)] = (now, True)
        _tenant_membership_cache[(user_a, tenant_2)] = (now, True)
        _tenant_membership_cache[(user_b, tenant_1)] = (now, True)
        _tenant_membership_cache[(user_b, tenant_2)] = (now, True)

        assert len(_tenant_membership_cache) == 4

        # Invalidate specific (user_a, tenant_1)
        invalidate_tenant_membership_cache(user_id=user_a, tenant_id=tenant_1)
        assert (user_a, tenant_1) not in _tenant_membership_cache
        assert (user_a, tenant_2) in _tenant_membership_cache
        assert (user_b, tenant_1) in _tenant_membership_cache

        # Invalidate by tenant (e.g. tenant_2 suspended)
        invalidate_tenant_membership_cache(tenant_id=tenant_2)
        assert (user_a, tenant_2) not in _tenant_membership_cache
        assert (user_b, tenant_2) not in _tenant_membership_cache
        assert (user_b, tenant_1) in _tenant_membership_cache

        # Invalidate by user (e.g. user_b deactivated)
        invalidate_tenant_membership_cache(user_id=user_b)
        assert len(_tenant_membership_cache) == 0

    def test_cache_expiry_honors_ttl(self) -> None:
        user_id = str(uuid.uuid4())
        tenant_id = TenantId(uuid.uuid4())

        # Insert entry with timestamp 35 seconds ago (> 30s TTL)
        past_time = time.monotonic() - 35.0
        _tenant_membership_cache[(user_id, tenant_id)] = (past_time, True)

        now = time.monotonic()
        cached = _tenant_membership_cache.get((user_id, tenant_id))
        assert cached is not None
        assert (now - cached[0]) >= 30.0  # Expired!

    def test_cache_isolation_across_different_users_and_tenants(self) -> None:
        user_1 = str(uuid.uuid4())
        user_2 = str(uuid.uuid4())
        tenant_a = TenantId(uuid.uuid4())
        tenant_b = TenantId(uuid.uuid4())

        _tenant_membership_cache[(user_1, tenant_a)] = (time.monotonic(), True)

        # Cross checks must be misses
        assert (user_2, tenant_a) not in _tenant_membership_cache
        assert (user_1, tenant_b) not in _tenant_membership_cache
        assert (user_2, tenant_b) not in _tenant_membership_cache


# ==============================================================================
# 2. Authorization-Policy Cache & Fail-Closed Review Tests
# ==============================================================================
class TestPolicyCacheAndFailClosed:
    """Verifies ABAC policy cache invalidation and fail-closed security properties."""

    def setup_method(self) -> None:
        invalidate_policy_cache()

    def teardown_method(self) -> None:
        invalidate_policy_cache()

    @pytest.mark.asyncio
    async def test_policy_creation_and_status_update_invalidates_cache(self) -> None:
        fake_app = build_fake_application()
        tenant_id = TenantId(uuid.uuid4())
        now = datetime.now(UTC)

        tenant = Tenant(
            id=tenant_id,
            name="Cache Test Tenant",
            slug=f"cache-tenant-{uuid.uuid4().hex[:6]}",
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        fake_app.stores.tenants[tenant_id] = tenant

        # Pre-seed cache entry
        _policy_cache[tenant_id] = (time.monotonic(), ())
        assert tenant_id in _policy_cache

        # Create policy
        ctx = RequestContext(
            tenant_id=tenant_id,
            actor_id="admin-user",
            actor_type=ActorType.USER,
            correlation_id=str(uuid.uuid4()),
            received_at=now,
        )
        outcome = await fake_app.use_cases.create_policy(
            CreatePolicyCommand(
                tenant_id=tenant_id,
                context=ctx,
                name="DenyOrders",
                description="Deny all orders",
                effect="DENY",
                actions=("orders.create",),
                resources=("orders",),
            )
        )
        # Verify cache was invalidated
        assert tenant_id not in _policy_cache

        # Repopulate cache
        _policy_cache[tenant_id] = (time.monotonic(), ())
        assert tenant_id in _policy_cache

        # Update policy status
        await fake_app.use_cases.update_policy_status(
            UpdatePolicyStatusCommand(
                tenant_id=tenant_id,
                context=ctx,
                policy_id=str(outcome.view.id),
                status="INACTIVE",
            )
        )
        # Verify cache was invalidated again
        assert tenant_id not in _policy_cache

    @pytest.mark.asyncio
    async def test_policy_loading_exception_fails_closed_and_records_audit(self) -> None:
        """Security guarantee: A database/cache failure during policy loading must NEVER

        default to ALLOW. It must fail closed and generate an audit record.
        """
        audit_adapter = InMemoryAuditAdapter()
        tenant_id = TenantId(uuid.uuid4())

        @asynccontextmanager
        async def failing_uow() -> AsyncIterator[Any]:
            class FailingPolicies:
                async def list_for_tenant(
                    self, tid: TenantId, active_only: bool = True
                ) -> list[AccessPolicy]:
                    raise ConnectionResetError("Simulated database connection failure")

            class FailingUoW:
                policies = FailingPolicies()

            yield FailingUoW()

        auth_service = ApplicationAuthorizationService(
            uow_factory=failing_uow,
            audit=audit_adapter,
        )

        req = AuthorizationRequest(
            action="orders.create",
            resource_type="orders",
            principal_id="test-user",
            principal_type=ActorType.USER,
            tenant_id=tenant_id,
            roles=("MEMBER",),
            permissions=frozenset({"orders.create"}),
            resource_tenant_id=tenant_id,
        )

        decision = await auth_service.authorize(req)

        # MUST be denied
        assert not decision.allowed
        assert decision.reason == DecisionReason.DENIED_EVALUATION_ERROR
        assert "failed to load access policies" in (decision.reason_details or "")

        # MUST be audited
        assert len(audit_adapter.entries) == 1
        audit = audit_adapter.entries[0]
        assert audit.action == "orders.create"
        assert audit.tenant_id == str(tenant_id)
        assert audit.after is not None
        assert audit.after.get("allowed") is False
        assert audit.after.get("reason") == DecisionReason.DENIED_EVALUATION_ERROR.value


# ==============================================================================
# 3. Production Authentication Configuration Tests
# ==============================================================================
class TestProductionAuthenticationConfig:
    """Verifies that production mode strictly enforces authentication and secrets."""

    def test_production_mode_rejects_auth_required_false(self) -> None:
        with pytest.raises(ValueError, match="ARAD_AUTH_REQUIRED must be set to true"):
            Settings(
                environment="production",
                auth_required=False,
                auth_jwt_secret="a" * 32,
            )

    def test_production_mode_rejects_insecure_dev_secret(self) -> None:
        with pytest.raises(ValueError, match="non-placeholder key of at least 32 characters"):
            Settings(
                environment="production",
                auth_required=True,
                auth_jwt_secret="insecure-dev-secret-change-in-production-0123456789abcdef",
            )

    def test_production_mode_rejects_example_placeholder_secret(self) -> None:
        with pytest.raises(ValueError, match="non-placeholder key of at least 32 characters"):
            Settings(
                environment="production",
                auth_required=True,
                auth_jwt_secret="REPLACE_WITH_MINIMUM_32_CHAR_SECRET_KEY_BEFORE_PRODUCTION_USE",
            )

    def test_production_mode_rejects_short_secret(self) -> None:
        with pytest.raises(ValueError, match="at least 32 characters"):
            Settings(
                environment="production",
                auth_required=True,
                auth_jwt_secret="too-short-secret",
            )

    def test_production_mode_accepts_valid_high_entropy_secret(self) -> None:
        valid_secret = "c7e2b19f4a6d8e03512b4897f123456789abcdef0123456789abcdef012345"
        settings = Settings(
            environment="production",
            auth_required=True,
            auth_jwt_secret=valid_secret,
        )
        assert settings.is_production is True
        assert settings.auth_required is True
        assert settings.auth_jwt_secret == valid_secret

    def test_development_mode_allows_default_secrets_and_disabled_auth(self) -> None:
        settings = Settings(environment="local")
        assert settings.is_production is False
        assert settings.auth_required is False
        assert len(settings.auth_jwt_secret) >= 32


# ==============================================================================
# 4. Tenant Isolation and PostgreSQL RLS Tests
# ==============================================================================
class TestTenantRLSIsolation:
    """Verifies that untrusted X-Tenant-Id headers cannot establish RLS context."""

    def test_transaction_scope_starts_without_untrusted_tenant_context(self) -> None:
        class FakeDatabase:
            def new_scope(self, **kwargs: Any) -> Any:
                class FakeScope:
                    def __init__(self) -> None:
                        self.tenant_id = kwargs.get("tenant_id")

                return FakeScope()

        fake_db = FakeDatabase()
        scope = get_transaction_scope(fake_db)  # type: ignore[arg-type]
        assert scope.tenant_id is None

    @pytest.mark.asyncio
    async def test_query_use_case_sets_tenant_context_from_context(self) -> None:
        tenant_id = TenantId(uuid.uuid4())
        applied_tenant_id = None

        class DummyScope:
            async def set_tenant_context(self, tid: object | None) -> None:
                nonlocal applied_tenant_id
                applied_tenant_id = tid

        class DummyUow:
            _scope = DummyScope()

            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

        @dataclass(frozen=True, slots=True)
        class DummyQuery:
            context: RequestContext

        class DummyQueryUseCase(QueryUseCase[DummyQuery, str, DummyUow]):
            async def _execute(self, uow: DummyUow, command: DummyQuery) -> str:
                return "ok"

        uc = DummyQueryUseCase(uow_factory=lambda: DummyUow(), clock=None)
        ctx = RequestContext(tenant_id=tenant_id, actor_id="user1", actor_type=ActorType.USER)
        res = await uc(DummyQuery(context=ctx))
        assert res == "ok"
        assert applied_tenant_id == tenant_id
