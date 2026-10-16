"""Regression and security test suite for Phase 4.2 findings (AUTH-001 through AUTH-015)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from tests.fakes.application import build_fake_application

from identity.application.commands import (
    AuthenticateServiceAccountCommand,
    CreateRoleCommand,
    CreateServiceAccountCommand,
    RevokeRoleCommand,
    RotateServiceAccountSecretCommand,
)
from identity.domain.membership import MembershipRole, TenantMembership
from identity.domain.role import Role
from identity.domain.service_account import ServiceAccount, ServiceAccountStatus
from identity.domain.tenant import Tenant, TenantStatus
from shared.application.context import RequestContext
from shared.application.errors import AuthenticationFailed, CannotRemoveLastAdmin
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import MembershipId, ServiceAccountId, TenantId, UserId


def _ctx(tenant_id: TenantId | None = None) -> RequestContext:
    return RequestContext(tenant_id=tenant_id, actor_id="test-actor")


# ==============================================================================
# AUTH-006: Last Admin Protection
# ==============================================================================
class TestAuth006LastAdminProtection:
    @pytest.mark.asyncio
    async def test_last_admin_removal_blocked(self) -> None:
        app = build_fake_application()
        tenant_id = TenantId(uuid.uuid4())
        user_id = UserId(uuid.uuid4())
        mem_id = MembershipId(uuid.uuid4())
        now = app.clock.now()

        tenant = Tenant(
            id=tenant_id,
            name="Tenant A",
            slug="tenant-a",
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        app.stores.tenants[tenant_id] = tenant
        app.stores.versions[tenant_id] = 1

        admin_membership = TenantMembership.create(
            user_id=user_id,
            tenant_id=tenant_id,
            role=MembershipRole.ADMIN,
            roles=["ADMIN"],
            now=now,
            id=mem_id,
        )
        app.stores.memberships[mem_id] = admin_membership
        app.stores.versions[mem_id] = 1

        cmd = RevokeRoleCommand(
            context=_ctx(tenant_id),
            tenant_id=tenant_id,
            membership_id=mem_id,
            role_name="ADMIN",
        )

        with pytest.raises(
            CannotRemoveLastAdmin, match="cannot remove the last active administrator"
        ):
            await app.use_cases.revoke_role(cmd)

    @pytest.mark.asyncio
    async def test_two_admins_remove_one_allowed(self) -> None:
        app = build_fake_application()
        tenant_id = TenantId(uuid.uuid4())
        now = app.clock.now()

        tenant = Tenant(
            id=tenant_id,
            name="Tenant A",
            slug="tenant-a",
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        app.stores.tenants[tenant_id] = tenant
        app.stores.versions[tenant_id] = 1

        mem1_id = MembershipId(uuid.uuid4())
        admin1 = TenantMembership.create(
            user_id=UserId(uuid.uuid4()),
            tenant_id=tenant_id,
            role=MembershipRole.ADMIN,
            roles=["ADMIN"],
            now=now,
            id=mem1_id,
        )
        app.stores.memberships[mem1_id] = admin1
        app.stores.versions[mem1_id] = 1

        mem2_id = MembershipId(uuid.uuid4())
        admin2 = TenantMembership.create(
            user_id=UserId(uuid.uuid4()),
            tenant_id=tenant_id,
            role=MembershipRole.ADMIN,
            roles=["ADMIN"],
            now=now,
            id=mem2_id,
        )
        app.stores.memberships[mem2_id] = admin2
        app.stores.versions[mem2_id] = 1

        cmd = RevokeRoleCommand(
            context=_ctx(tenant_id),
            tenant_id=tenant_id,
            membership_id=mem1_id,
            role_name="ADMIN",
        )

        outcome = await app.use_cases.revoke_role(cmd)
        assert "ADMIN" not in outcome.view.roles
        # But attempting to revoke the second admin now fails
        cmd2 = RevokeRoleCommand(
            context=_ctx(tenant_id),
            tenant_id=tenant_id,
            membership_id=mem2_id,
            role_name="ADMIN",
        )
        with pytest.raises(CannotRemoveLastAdmin):
            await app.use_cases.revoke_role(cmd2)

    @pytest.mark.asyncio
    async def test_inactive_admin_does_not_count_as_active(self) -> None:
        app = build_fake_application()
        tenant_id = TenantId(uuid.uuid4())
        now = app.clock.now()

        tenant = Tenant(
            id=tenant_id,
            name="Tenant A",
            slug="tenant-a",
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        app.stores.tenants[tenant_id] = tenant
        app.stores.versions[tenant_id] = 1

        mem1_id = MembershipId(uuid.uuid4())
        admin1 = TenantMembership.create(
            user_id=UserId(uuid.uuid4()),
            tenant_id=tenant_id,
            role=MembershipRole.ADMIN,
            roles=["ADMIN"],
            now=now,
            id=mem1_id,
        )
        app.stores.memberships[mem1_id] = admin1
        app.stores.versions[mem1_id] = 1

        mem2_id = MembershipId(uuid.uuid4())
        admin2 = TenantMembership.create(
            user_id=UserId(uuid.uuid4()),
            tenant_id=tenant_id,
            role=MembershipRole.ADMIN,
            roles=["ADMIN"],
            now=now,
            id=mem2_id,
        )
        admin2.disable(now=now)  # Inactive
        app.stores.memberships[mem2_id] = admin2
        app.stores.versions[mem2_id] = 1

        cmd = RevokeRoleCommand(
            context=_ctx(tenant_id),
            tenant_id=tenant_id,
            membership_id=mem1_id,
            role_name="ADMIN",
        )

        with pytest.raises(CannotRemoveLastAdmin):
            await app.use_cases.revoke_role(cmd)

    @pytest.mark.asyncio
    async def test_cross_tenant_isolation_last_admin(self) -> None:
        app = build_fake_application()
        t1_id = TenantId(uuid.uuid4())
        t2_id = TenantId(uuid.uuid4())
        now = app.clock.now()

        app.stores.tenants[t1_id] = Tenant(
            id=t1_id,
            name="Tenant 1",
            slug="tenant-1",
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        app.stores.versions[t1_id] = 1
        app.stores.tenants[t2_id] = Tenant(
            id=t2_id,
            name="Tenant 2",
            slug="tenant-2",
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        app.stores.versions[t2_id] = 1

        mem1_id = MembershipId(uuid.uuid4())
        t1_admin = TenantMembership.create(
            user_id=UserId(uuid.uuid4()),
            tenant_id=t1_id,
            role=MembershipRole.ADMIN,
            roles=["ADMIN"],
            now=now,
            id=mem1_id,
        )
        app.stores.memberships[mem1_id] = t1_admin
        app.stores.versions[mem1_id] = 1

        # Multiple admins in tenant 2
        for _ in range(3):
            mid = MembershipId(uuid.uuid4())
            app.stores.memberships[mid] = TenantMembership.create(
                user_id=UserId(uuid.uuid4()),
                tenant_id=t2_id,
                role=MembershipRole.ADMIN,
                roles=["ADMIN"],
                now=now,
                id=mid,
            )
            app.stores.versions[mid] = 1

        cmd = RevokeRoleCommand(
            context=_ctx(t1_id),
            tenant_id=t1_id,
            membership_id=mem1_id,
            role_name="ADMIN",
        )
        with pytest.raises(CannotRemoveLastAdmin):
            await app.use_cases.revoke_role(cmd)


# ==============================================================================
# AUTH-007: Role Shadowing Prevention
# ==============================================================================
class TestAuth007RoleShadowing:
    @pytest.mark.parametrize(
        "reserved_name",
        ["ADMIN", "admin", "Admin ", " MEMBER", "SUPPORT_AGENT", "AUDITOR"],
    )
    def test_custom_role_cannot_use_reserved_name_domain(self, reserved_name: str) -> None:
        app = build_fake_application()
        with pytest.raises(InvalidValue, match="reserved for built-in system roles"):
            Role.create(
                name=reserved_name,
                description="Custom admin",
                permissions=["orders.read"],
                tenant_id=TenantId(uuid.uuid4()),
                is_system=False,
                now=app.clock.now(),
            )

    @pytest.mark.asyncio
    async def test_create_role_use_case_rejects_reserved_name(self) -> None:
        app = build_fake_application()
        tenant_id = TenantId(uuid.uuid4())
        now = app.clock.now()
        app.stores.tenants[tenant_id] = Tenant(
            id=tenant_id,
            name="Tenant A",
            slug="tenant-a",
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        app.stores.versions[tenant_id] = 1

        cmd = CreateRoleCommand(
            context=_ctx(tenant_id),
            tenant_id=tenant_id,
            name="admin",
            description="Tenant custom admin",
            permissions=("orders.read",),
        )
        with pytest.raises(InvalidValue, match="reserved"):
            await app.use_cases.create_role(cmd)


# ==============================================================================
# AUTH-008: Service Account Scope Validation
# ==============================================================================
class TestAuth008ServiceAccountScopes:
    def test_valid_scopes_accepted(self) -> None:
        app = build_fake_application()
        sa = ServiceAccount.create(
            tenant_id=TenantId(uuid.uuid4()),
            name="Backup SA",
            client_id="backup-client-1",
            secret_hash="hash123",
            now=app.clock.now(),
            scopes=["orders.read", "catalog.products.read"],
        )
        assert "orders.read" in sa.scopes
        assert "catalog.products.read" in sa.scopes

    @pytest.mark.parametrize(
        "bad_scope",
        ["*", "orders.*", "arbitrary_scope", "admin:all", "orders.unknown"],
    )
    def test_invalid_or_wildcard_scopes_rejected(self, bad_scope: str) -> None:
        app = build_fake_application()
        with pytest.raises(InvalidValue):
            ServiceAccount.create(
                tenant_id=TenantId(uuid.uuid4()),
                name="Bad SA",
                client_id="bad-client-1",
                secret_hash="hash123",
                now=app.clock.now(),
                scopes=[bad_scope],
            )

    def test_update_scopes_validates_canonical_permissions(self) -> None:
        app = build_fake_application()
        sa = ServiceAccount.create(
            tenant_id=TenantId(uuid.uuid4()),
            name="Good SA",
            client_id="good-client-1",
            secret_hash="hash123",
            now=app.clock.now(),
            scopes=["orders.read"],
        )
        with pytest.raises(InvalidValue):
            sa.update_scopes(["*", "orders.read"], now=app.clock.now())


# ==============================================================================
# AUTH-009: Service Account Token Versioning
# ==============================================================================
class TestAuth009ServiceAccountTokenVersioning:
    @pytest.mark.asyncio
    async def test_secret_rotation_invalidates_previously_issued_tokens(self) -> None:
        app = build_fake_application()
        tenant_id = TenantId(uuid.uuid4())
        now = app.clock.now()
        tenant = Tenant(
            id=tenant_id,
            name="Tenant A",
            slug="tenant-a",
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        app.stores.tenants[tenant_id] = tenant
        app.stores.versions[tenant_id] = 1

        from identity.infrastructure.security import PasswordHasher, TokenService

        hasher = PasswordHasher()
        token_service = TokenService(
            secret="test-in-memory-jwt-secret-at-least-32-chars-long",
            algorithm="HS256",
            issuer="arad-platform",
            audience="arad-api",
            access_token_ttl_seconds=900,
        )

        sa_id = ServiceAccountId(uuid.uuid4())
        secret = "InitialClientSecret12345!"
        sa = ServiceAccount(
            id=sa_id,
            tenant_id=tenant_id,
            name="SA 1",
            client_id="sa-client-1",
            secret_hash=hasher.hash(secret),
            status=ServiceAccountStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            scopes=["orders.read"],
            version=1,
        )
        app.stores.service_accounts[sa_id] = sa
        app.stores.versions[sa_id] = 1

        # 1. Token 1 issued at version 1
        auth_cmd = AuthenticateServiceAccountCommand(
            context=_ctx(tenant_id), client_id="sa-client-1", client_secret=secret
        )
        outcome1 = await app.use_cases.authenticate_service_account(auth_cmd)
        token1_claims = token_service.verify_access_token(outcome1.view.access_token)
        assert token1_claims["token_version"] == 1

        # 2. Rotate secret -> version becomes 2
        rotate_cmd = RotateServiceAccountSecretCommand(
            context=_ctx(tenant_id), tenant_id=tenant_id, service_account_id=sa_id
        )
        outcome_rotate = await app.use_cases.rotate_service_account(rotate_cmd)
        new_secret = outcome_rotate.view.client_secret
        assert new_secret != secret

        # Verify sa version in store
        updated_sa = app.stores.service_accounts[sa_id]
        assert updated_sa.version == 2

        # 3. Old token (token1) carries token_version=1 != updated_sa.version (2)
        # Testing principal resolution logic
        from bootstrap.dependencies import get_current_principal

        mock_db = MagicMock()
        mock_scope = MagicMock()
        mock_uow = MagicMock()
        mock_uow.service_accounts.get = AsyncMock(return_value=updated_sa)
        mock_uow.tenants.get = AsyncMock(return_value=tenant)
        mock_uow.__aenter__ = AsyncMock(return_value=mock_uow)
        mock_uow.__aexit__ = AsyncMock(return_value=None)
        mock_scope.__aenter__ = AsyncMock(return_value=mock_scope)
        mock_scope.__aexit__ = AsyncMock(return_value=None)
        mock_db.new_scope.return_value = mock_scope

        mock_settings = MagicMock()
        mock_settings.auth_required = True
        mock_request = MagicMock()

        from unittest.mock import patch

        patch_target = "identity.adapters.persistence.unit_of_work.SqlAlchemyIdentityUnitOfWork"
        with patch(patch_target) as mock_uow_cls:
            mock_uow_cls.return_value.__aenter__.return_value = mock_uow

            with pytest.raises(AuthenticationFailed, match="invalidated by secret rotation"):
                await get_current_principal(
                    request=mock_request,
                    settings=mock_settings,
                    token_service=token_service,
                    database=mock_db,
                    authorization=f"Bearer {outcome1.view.access_token}",
                )

            # 4. Authenticate with new secret -> Token 2 issued at version 2
            auth_cmd2 = AuthenticateServiceAccountCommand(
                context=_ctx(tenant_id), client_id="sa-client-1", client_secret=new_secret
            )
            outcome2 = await app.use_cases.authenticate_service_account(auth_cmd2)
            token2_claims = token_service.verify_access_token(outcome2.view.access_token)
            assert token2_claims["token_version"] == 2

            principal = await get_current_principal(
                request=mock_request,
                settings=mock_settings,
                token_service=token_service,
                database=mock_db,
                authorization=f"Bearer {outcome2.view.access_token}",
            )
            assert principal is not None
            assert principal.id == str(sa_id)


class TestStage3Remediation:
    """Tests for AUTH-003, AUTH-004, AUTH-011, AUTH-012."""

    async def test_app_level_authorization_denies_unauthorized_direct_call(self) -> None:
        """AUTH-004: Direct invocation of protected use cases fails if not authorized."""
        from unittest.mock import AsyncMock

        from identity.domain.decision import AuthorizationDecision, DecisionReason
        from identity.domain.principal import Principal
        from inventory.application.commands import AdjustInventoryCommand
        from inventory.application.use_cases import AdjustInventory
        from inventory.domain.value_objects import AdjustmentReason
        from shared.application.context import ActorType
        from shared.application.errors import Forbidden
        from shared.domain.clock import SystemClock
        from shared.domain.identifiers import SkuId, WarehouseId

        tenant_id = TenantId(uuid.uuid4())
        mock_uow = AsyncMock()

        def mock_uow_factory() -> AsyncMock:
            return mock_uow

        mock_clock = SystemClock()

        # Mock authorizer that denies
        mock_authorizer = AsyncMock()
        mock_authorizer.authorize.return_value = AuthorizationDecision(
            allowed=False,
            reason=DecisionReason.DENIED_MISSING_PERMISSION,
            action="inventory.adjust",
            resource_type="inventory",
        )

        use_case = AdjustInventory(
            uow_factory=mock_uow_factory,
            clock=mock_clock,
            authorizer=mock_authorizer,
        )

        # Context with an unauthorized principal
        unauthorized_principal = Principal(
            id="user-1",
            type=ActorType.USER,
            roles=(),
            permissions=frozenset(),
        )
        ctx = RequestContext(
            tenant_id=tenant_id,
            actor_id="user-1",
            actor_type=ActorType.USER,
            principal=unauthorized_principal,
        )

        cmd = AdjustInventoryCommand(
            tenant_id=tenant_id,
            context=ctx,
            warehouse_id=WarehouseId(uuid.uuid4()),
            sku_id=SkuId(uuid.uuid4()),
            quantity_delta=10,
            reason=AdjustmentReason.CYCLE_COUNT,
        )

        # Direct execution must raise Forbidden even though HTTP dependencies were not run
        with pytest.raises(Forbidden) as exc_info:
            await use_case._execute(mock_uow, cmd)
        assert "not authorized" in str(exc_info.value.message)

    async def test_resource_aware_abac_receives_resource_attributes(self) -> None:
        """AUTH-003: Resource attributes are provided to authorizer for evaluation."""
        from unittest.mock import AsyncMock

        from identity.domain.decision import AuthorizationDecision, DecisionReason
        from identity.domain.principal import Principal
        from inventory.application.commands import AdjustInventoryCommand
        from inventory.application.use_cases import AdjustInventory
        from inventory.domain.value_objects import AdjustmentReason
        from shared.application.context import ActorType
        from shared.domain.clock import SystemClock
        from shared.domain.identifiers import SkuId, WarehouseId

        tenant_id = TenantId(uuid.uuid4())
        mock_uow = AsyncMock()

        def mock_uow_factory() -> AsyncMock:
            return mock_uow

        mock_clock = SystemClock()

        captured_requests = []

        async def fake_authorize(req):
            captured_requests.append(req)
            return AuthorizationDecision(
                allowed=True,
                reason=DecisionReason.ALLOWED_BY_ABAC_POLICY,
                action=req.action,
                resource_type=req.resource_type,
            )

        mock_authorizer = AsyncMock()
        mock_authorizer.authorize.side_effect = fake_authorize

        use_case = AdjustInventory(
            uow_factory=mock_uow_factory,
            clock=mock_clock,
            authorizer=mock_authorizer,
        )

        wh_id = WarehouseId(uuid.uuid4())
        sku_id = SkuId(uuid.uuid4())
        principal = Principal(
            id="user-admin",
            type=ActorType.USER,
            role="ADMIN",
            roles=("ADMIN",),
            permissions=frozenset({"inventory.adjust"}),
        )
        ctx = RequestContext(
            tenant_id=tenant_id,
            actor_id="user-admin",
            actor_type=ActorType.USER,
            principal=principal,
        )

        cmd = AdjustInventoryCommand(
            tenant_id=tenant_id,
            context=ctx,
            warehouse_id=wh_id,
            sku_id=sku_id,
            quantity_delta=25,
            reason=AdjustmentReason.CYCLE_COUNT,
        )

        from inventory.domain.inventory_item import InventoryItem

        # Real InventoryItem aggregate
        item = InventoryItem.register(
            tenant_id=tenant_id,
            warehouse_id=wh_id,
            sku_id=sku_id,
            now=mock_clock.now(),
        )
        mock_uow.warehouses.get.return_value = AsyncMock()
        mock_uow.sku_exists.return_value = True
        mock_uow.inventory_items.get_or_register_for_update.return_value = (item, False)

        await use_case._execute(mock_uow, cmd)

        assert len(captured_requests) == 1
        req = captured_requests[0]
        assert req.action == "inventory.adjust"
        assert req.resource_type == "inventory"
        assert req.resource_id == str(sku_id)
        assert req.resource_tenant_id == tenant_id
        assert req.resource_attributes["warehouse_id"] == str(wh_id)
        assert req.resource_attributes["quantity_delta"] == 25

    async def test_abac_decision_oracle_sanitization(self) -> None:
        """AUTH-011: POST /v1/access/evaluate sanitizes details for non-admin evaluators."""
        from unittest.mock import AsyncMock

        from bootstrap.routers.access import evaluate_access
        from bootstrap.schemas.access import DecisionEvaluationRequest
        from identity.domain.decision import AuthorizationDecision, DecisionReason
        from identity.domain.principal import Principal
        from shared.application.context import ActorType

        tenant_id = TenantId(uuid.uuid4())
        mock_auth_service = AsyncMock()
        mock_auth_service.authorize.return_value = AuthorizationDecision(
            allowed=False,
            reason=DecisionReason.DENIED_BY_ABAC_POLICY,
            reason_details="ast_condition: (resource.warehouse_id == 'secret_wh') failed",
            action="inventory.adjust",
            resource_type="inventory",
            policy_id=uuid.uuid4(),
        )

        # 1. Non-admin evaluator with access.policies.read
        non_admin_principal = Principal(
            id="auditor-1",
            type=ActorType.USER,
            role="AUDITOR",
            roles=("AUDITOR",),
            permissions=frozenset({"access.policies.read"}),
        )
        ctx = RequestContext(
            tenant_id=tenant_id, actor_id="auditor-1", principal=non_admin_principal
        )
        payload = DecisionEvaluationRequest(
            action="inventory.adjust",
            resource_type="inventory",
        )

        res = await evaluate_access(
            payload=payload,
            principal=non_admin_principal,
            context=ctx,
            auth_service=mock_auth_service,
        )
        # Reason details and internal policy ID must be sanitized
        assert res.reason == DecisionReason.DENIED_BY_ABAC_POLICY.value
        assert "secret_wh" not in res.reason
        assert res.policy_id is None

        # 2. Admin evaluator receives full details
        admin_principal = Principal(
            id="admin-1",
            type=ActorType.USER,
            role="ADMIN",
            roles=("ADMIN",),
            permissions=frozenset(
                {"*"},
            ),
        )
        admin_ctx = RequestContext(
            tenant_id=tenant_id, actor_id="admin-1", principal=admin_principal
        )

        admin_res = await evaluate_access(
            payload=payload,
            principal=admin_principal,
            context=admin_ctx,
            auth_service=mock_auth_service,
        )
        assert admin_res.reason == "ast_condition: (resource.warehouse_id == 'secret_wh') failed"
        assert admin_res.policy_id is not None

    async def test_tenant_inspection_requires_authentication_and_tenant_match(self) -> None:
        """AUTH-012: GET /v1/tenants/{tenant_id} requires auth and isolates tenant metadata."""
        from unittest.mock import AsyncMock

        from bootstrap.routers.platform import get_tenant
        from identity.application.commands import TenantView
        from identity.domain.principal import Principal
        from identity.domain.tenant import TenantStatus
        from shared.application.context import ActorType
        from shared.application.errors import Forbidden
        from shared.domain.clock import SystemClock

        tenant_a = TenantId(uuid.uuid4())
        tenant_b = TenantId(uuid.uuid4())

        now = SystemClock().now()
        mock_view = TenantView(
            id=tenant_a,
            slug="tenant-a",
            name="Tenant A",
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
            version=1,
        )
        mock_use_cases = AsyncMock()
        mock_use_cases.get_tenant.return_value = mock_view

        # User belonging to Tenant B trying to inspect Tenant A -> 403 Forbidden
        principal_b = Principal(
            id="user-b",
            type=ActorType.USER,
            active_tenant_id=tenant_b,
        )
        ctx = RequestContext(actor_id="user-b", principal=principal_b)

        with pytest.raises(Forbidden) as exc:
            await get_tenant(
                principal=principal_b,
                context=ctx,
                use_cases=mock_use_cases,
                tenant_id=tenant_a,
            )
        assert "access to tenant metadata requires membership" in str(exc.value.message)

        # User belonging to Tenant A inspecting Tenant A -> Allowed
        principal_a = Principal(
            id="user-a",
            type=ActorType.USER,
            active_tenant_id=tenant_a,
        )
        res = await get_tenant(
            principal=principal_a,
            context=ctx,
            use_cases=mock_use_cases,
            tenant_id=tenant_a,
        )
        assert res.id == tenant_a


class TestStage4DurableAudit:
    """Tests for AUTH-014 Durable Audit Logging."""

    async def test_identity_mutations_emit_audit_records_and_redact_secrets(self) -> None:
        """Mutations emit audit records and strictly redact credentials/secrets."""
        from tests.fakes.application import build_fake_application

        from identity.application.commands import (
            CreatePolicyCommand,
            CreateRoleCommand,
            RegisterTenantCommand,
            RevokeServiceAccountCommand,
            RotateServiceAccountSecretCommand,
        )
        from shared.application.context import ActorType

        app = build_fake_application()
        platform_ctx = RequestContext(actor_id="admin-1", actor_type=ActorType.USER)

        # 1. Register tenant
        reg_cmd = RegisterTenantCommand(
            context=platform_ctx, slug="audit-test", name="Audit Test Tenant"
        )
        reg_out = await app.use_cases.register_tenant(reg_cmd)
        tenant_id = reg_out.view.id
        ctx = RequestContext(
            tenant_id=tenant_id,
            actor_id="admin-1",
            actor_type=ActorType.USER,
            correlation_id="corr-audit-1",
        )

        # 2. CreateRole
        role_cmd = CreateRoleCommand(
            tenant_id=tenant_id,
            context=ctx,
            name="CUSTOM_OPERATOR",
            description="Operator role",
            permissions=("orders.read", "inventory.read"),
        )
        await app.use_cases.create_role(role_cmd)
        assert len(app.stores.audit_logs) >= 1
        role_audit = app.stores.audit_logs[-1]
        assert role_audit.action == "access.role.created"
        assert role_audit.tenant_id == str(tenant_id)
        assert role_audit.actor_id == "admin-1"
        assert role_audit.resource_type == "role"
        assert role_audit.after["name"] == "CUSTOM_OPERATOR"

        # 3. CreatePolicy
        policy_cmd = CreatePolicyCommand(
            tenant_id=tenant_id,
            context=ctx,
            name="HighValueLimit",
            description="Deny high value orders",
            effect="DENY",
            actions=("orders.create",),
            resources=("orders",),
            conditions={"operator": "gt", "field": "resource.total_minor", "value": 100000},
        )
        await app.use_cases.create_policy(policy_cmd)
        policy_audit = app.stores.audit_logs[-1]
        assert policy_audit.action == "access.policy.created"
        assert policy_audit.resource_type == "policy"
        assert policy_audit.after["effect"] == "DENY"

        # 4. CreateServiceAccount - verify NO plaintext secret or secret_hash stored in audit
        sa_cmd = CreateServiceAccountCommand(
            tenant_id=tenant_id,
            context=ctx,
            name="AutomationBot",
            client_id="bot-client-1",
            scopes=("orders.read",),
        )
        sa_out = await app.use_cases.create_service_account(sa_cmd)
        sa_audit = app.stores.audit_logs[-1]
        assert sa_audit.action == "identity.service_account.created"
        assert sa_audit.resource_type == "service_account"
        assert sa_audit.after["client_id"] == "bot-client-1"
        assert "client_secret" not in sa_audit.after
        assert "secret_hash" not in sa_audit.after

        # 5. RotateServiceAccountSecret - verify NO secret in audit
        rot_cmd = RotateServiceAccountSecretCommand(
            tenant_id=tenant_id,
            context=ctx,
            service_account_id=sa_out.view.id,
        )
        await app.use_cases.rotate_service_account(rot_cmd)
        rot_audit = app.stores.audit_logs[-1]
        assert rot_audit.action == "identity.service_account.secret_rotated"
        assert rot_audit.resource_type == "service_account"
        assert "client_secret" not in rot_audit.after
        assert "secret_hash" not in rot_audit.after
        assert rot_audit.after["token_version"] == 2

        # 6. RevokeServiceAccount
        rev_cmd = RevokeServiceAccountCommand(
            tenant_id=tenant_id,
            context=ctx,
            service_account_id=sa_out.view.id,
        )
        await app.use_cases.revoke_service_account(rev_cmd)
        rev_audit = app.stores.audit_logs[-1]
        assert rev_audit.action == "identity.service_account.revoked"
        assert rev_audit.after["status"] == "DISABLED"

    async def test_audit_adapter_redacts_sensitive_credentials(self) -> None:
        """SqlAlchemyAuditAdapter strictly redacts secrets, passwords, and tokens."""
        from unittest.mock import AsyncMock

        from audit.adapters.persistence.repositories import SqlAlchemyAuditAdapter
        from audit.domain.entry import AuditEntry
        from shared.domain.clock import SystemClock

        mock_session = AsyncMock()
        adapter = SqlAlchemyAuditAdapter(mock_session)

        now = SystemClock().now()
        entry = AuditEntry(
            audit_id=str(uuid.uuid4()),
            tenant_id=str(uuid.uuid4()),
            actor_id="actor-1",
            action="identity.login",
            resource_type="user",
            resource_id="user-1",
            correlation_id="corr-1",
            causation_id=None,
            occurred_at=now,
            before={"password": "plain_password", "token": "secret_jwt"},
            after={"secret": "my_secret_key", "public_info": "safe_data"},
            details={"access_token": "token123", "client_secret": "secret_val"},
        )

        await adapter.record(entry)

        assert mock_session.execute.called
        call_args = mock_session.execute.call_args[0][0]
        # Inspect compiled parameters inserted
        inserted_values = call_args.compile().params
        details = inserted_values["details"]
        assert details["before"]["password"] == "[REDACTED]"
        assert details["before"]["token"] == "[REDACTED]"
        assert details["after"]["secret"] == "[REDACTED]"
        assert details["after"]["public_info"] == "safe_data"
        assert details["access_token"] == "[REDACTED]"
        assert details["client_secret"] == "[REDACTED]"
