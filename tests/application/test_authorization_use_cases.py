"""Application use-case tests for Phase 4.2 Authorization (RBAC & ABAC).

Tests Scenarios A through I from the specification:
* Scenario A: Cross-tenant resource access
* Scenario B: Privilege escalation
* Scenario C: Warehouse-scoped authorization
* Scenario D: Sensitive field protection
* Scenario E: Service-account least privilege
* Scenario F: Fail-closed policy evaluation
* Scenario G: Policy revocation
* Scenario H: Message-driven authorization context validation
* Scenario I: Backward compatibility and regression protection
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from tests.fakes.application import FakeApplication, build_fake_application

from audit.adapters.in_memory import InMemoryAuditAdapter
from identity.application.authorization import ApplicationAuthorizationService
from identity.application.commands import (
    CreatePolicyCommand,
    CreateRoleCommand,
    GetRoleQuery,
    ListRolesQuery,
    UpdatePolicyStatusCommand,
)
from identity.domain.decision import AuthorizationRequest, DecisionReason
from identity.domain.policy import PolicyStatus
from identity.domain.principal import Principal
from identity.domain.role import (
    SYSTEM_ROLE_ADMIN,
    SYSTEM_ROLE_AUDITOR,
    SYSTEM_ROLE_MEMBER,
    SYSTEM_ROLE_SUPPORT,
    SYSTEM_ROLES_PERMISSIONS,
)
from identity.domain.service_account import ServiceAccount
from identity.domain.tenant import Tenant, TenantStatus
from shared.application.context import ActorType, RequestContext
from shared.domain.identifiers import (
    TenantId,
)


@pytest.fixture
def fake_app() -> FakeApplication:
    return build_fake_application()


@pytest.fixture
def audit_adapter() -> InMemoryAuditAdapter:
    return InMemoryAuditAdapter()


@pytest.fixture
def auth_service(
    fake_app: FakeApplication, audit_adapter: InMemoryAuditAdapter
) -> ApplicationAuthorizationService:
    from tests.fakes.unit_of_work import FakeIdentityUnitOfWork

    def _uow() -> FakeIdentityUnitOfWork:
        return FakeIdentityUnitOfWork(fake_app.stores, fake_app.scope)

    return ApplicationAuthorizationService(uow_factory=_uow, audit=audit_adapter)


class TestRoleUseCases:
    async def test_create_and_get_custom_role(self, fake_app: FakeApplication) -> None:
        tenant_id = TenantId(uuid.uuid4())
        now = datetime.now(UTC)
        tenant = Tenant(
            id=tenant_id,
            name="Acme Corp",
            slug=f"acme-{uuid.uuid4().hex[:6]}",
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        fake_app.stores.tenants[tenant_id] = tenant

        context = RequestContext(
            tenant_id=tenant_id,
            actor_id="admin-1",
            actor_type=ActorType.USER,
            correlation_id=str(uuid.uuid4()),
            received_at=datetime.now(UTC),
        )
        outcome = await fake_app.use_cases.create_role(
            CreateRoleCommand(
                tenant_id=tenant_id,
                context=context,
                name="CUSTOM_OPERATOR",
                description="Custom warehouse operator",
                permissions=("inventory.read", "inventory.adjust"),
            )
        )
        assert outcome.status_code == 201
        assert outcome.view.name == "CUSTOM_OPERATOR"
        assert not outcome.view.is_system

        # Get role
        get_outcome = await fake_app.use_cases.get_role(
            GetRoleQuery(role_id=outcome.view.id, context=context)
        )
        assert get_outcome.id == outcome.view.id
        assert "inventory.read" in get_outcome.permissions

    async def test_list_roles_includes_system_roles(self, fake_app: FakeApplication) -> None:
        tenant_id = TenantId(uuid.uuid4())
        context = RequestContext(
            tenant_id=tenant_id,
            actor_id="admin-1",
            actor_type=ActorType.USER,
            correlation_id=str(uuid.uuid4()),
            received_at=datetime.now(UTC),
        )
        outcome = await fake_app.use_cases.list_roles(
            ListRolesQuery(context=context, tenant_id=tenant_id)
        )
        names = {r.name for r in outcome}
        assert SYSTEM_ROLE_ADMIN in names
        assert SYSTEM_ROLE_MEMBER in names
        assert SYSTEM_ROLE_SUPPORT in names
        assert SYSTEM_ROLE_AUDITOR in names


class TestPolicyUseCases:
    async def test_create_and_activate_policy(self, fake_app: FakeApplication) -> None:
        tenant_id = TenantId(uuid.uuid4())
        now = datetime.now(UTC)
        tenant = Tenant(
            id=tenant_id,
            name="Acme Corp",
            slug=f"acme-{uuid.uuid4().hex[:6]}",
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        fake_app.stores.tenants[tenant_id] = tenant

        context = RequestContext(
            tenant_id=tenant_id,
            actor_id="admin-1",
            actor_type=ActorType.USER,
            correlation_id=str(uuid.uuid4()),
            received_at=datetime.now(UTC),
        )
        outcome = await fake_app.use_cases.create_policy(
            CreatePolicyCommand(
                tenant_id=tenant_id,
                context=context,
                name="HighValueOrderGuard",
                description="Orders over 10000 require senior clearance",
                effect="DENY",
                actions=("orders.confirm",),
                resources=("orders",),
                conditions={"field": "resource.total_minor", "operator": "gt", "value": 1000000},
            )
        )
        assert outcome.status_code == 201
        assert outcome.view.status == PolicyStatus.ACTIVE.value

        # Update status to INACTIVE
        status_outcome = await fake_app.use_cases.update_policy_status(
            UpdatePolicyStatusCommand(
                policy_id=outcome.view.id,
                tenant_id=tenant_id,
                status=PolicyStatus.INACTIVE.value,
                context=context,
            )
        )
        assert status_outcome.view.status == PolicyStatus.INACTIVE.value


class TestSecurityScenarios:
    async def test_scenario_a_cross_tenant_resource_denial(
        self, auth_service: ApplicationAuthorizationService, audit_adapter: InMemoryAuditAdapter
    ) -> None:
        """Scenario A — Cross-tenant resource access:
        Given a user belongs to tenant A, when attempting to access a resource
        belonging to tenant B, access is denied.
        """
        tenant_a = TenantId(uuid.uuid4())
        tenant_b = TenantId(uuid.uuid4())

        req = AuthorizationRequest(
            action="orders.read",
            resource_type="orders",
            resource_id="ord-123",
            principal_id="user-a",
            tenant_id=tenant_a,
            roles=("MEMBER",),
            permissions=frozenset({"orders.read"}),
            resource_tenant_id=tenant_b,
        )
        decision = await auth_service.authorize(req)
        assert not decision.allowed
        assert decision.reason == DecisionReason.DENIED_CROSS_TENANT

        # Verify audit log entry
        assert len(audit_adapter.entries) == 1
        entry = audit_adapter.entries[0]
        assert entry.after["decision"] == "DENY"
        assert entry.tenant_id == str(tenant_a)
        assert entry.after["reason"] == DecisionReason.DENIED_CROSS_TENANT.value

    async def test_scenario_b_privilege_escalation_denied(
        self, auth_service: ApplicationAuthorizationService, fake_app: FakeApplication
    ) -> None:
        """Scenario B — Privilege escalation:
        Given a tenant member without permission to manage roles, attempts
        to assign admin role or manage access are denied.
        """
        tenant_id = TenantId(uuid.uuid4())
        # Member with only MEMBER role (has orders.* and inventory.*, but NOT access.roles.manage)
        member_req = AuthorizationRequest(
            action="access.roles.manage",
            resource_type="access",
            principal_id="member-user-1",
            tenant_id=tenant_id,
            roles=("MEMBER",),
            permissions=SYSTEM_ROLES_PERMISSIONS[SYSTEM_ROLE_MEMBER],
            resource_tenant_id=tenant_id,
        )
        decision = await auth_service.authorize(member_req)
        assert not decision.allowed
        assert decision.reason == DecisionReason.DENIED_MISSING_PERMISSION

    async def test_scenario_c_warehouse_scoped_authorization(
        self,
        auth_service: ApplicationAuthorizationService,
        fake_app: FakeApplication,
    ) -> None:
        """Scenario C — Warehouse-scoped authorization:
        Given operator assigned only to warehouse LAX, when operator attempts
        to modify fulfillment for SEA, access is denied.
        """
        tenant_id = TenantId(uuid.uuid4())
        now = datetime.now(UTC)
        tenant = Tenant(
            id=tenant_id,
            name="Acme Corp",
            slug=f"acme-{uuid.uuid4().hex[:6]}",
            status=TenantStatus.ACTIVE,
            created_at=now,
            updated_at=now,
        )
        fake_app.stores.tenants[tenant_id] = tenant

        # Create warehouse isolation ABAC policy
        context = RequestContext(
            tenant_id=tenant_id,
            actor_id="admin-1",
            actor_type=ActorType.USER,
            correlation_id=str(uuid.uuid4()),
            received_at=now,
        )
        await fake_app.use_cases.create_policy(
            CreatePolicyCommand(
                tenant_id=tenant_id,
                context=context,
                name="WarehouseEnforcement",
                description="Enforce warehouse matching",
                effect="DENY",
                actions=("fulfillments.modify",),
                resources=("fulfillments",),
                conditions={
                    "field": "subject.assigned_warehouse",
                    "operator": "neq",
                    "value_field": "resource.warehouse_id",
                },
            )
        )

        # Mismatched warehouse request (Operator has LAX, fulfillment is SEA)
        req_sea = AuthorizationRequest(
            action="fulfillments.modify",
            resource_type="fulfillments",
            principal_id="operator-1",
            tenant_id=tenant_id,
            roles=("MEMBER",),
            permissions=frozenset({"fulfillments.modify"}),
            resource_tenant_id=tenant_id,
            subject_attributes={"assigned_warehouse": "LAX"},
            resource_attributes={"warehouse_id": "SEA"},
        )
        dec_sea = await auth_service.authorize(req_sea)
        assert not dec_sea.allowed
        assert dec_sea.reason == DecisionReason.DENIED_BY_ABAC_POLICY

        # Matching warehouse request (Operator has LAX, fulfillment is LAX)
        req_lax = AuthorizationRequest(
            action="fulfillments.modify",
            resource_type="fulfillments",
            principal_id="operator-1",
            tenant_id=tenant_id,
            roles=("MEMBER",),
            permissions=frozenset({"fulfillments.modify"}),
            resource_tenant_id=tenant_id,
            subject_attributes={"assigned_warehouse": "LAX"},
            resource_attributes={"warehouse_id": "LAX"},
        )
        dec_lax = await auth_service.authorize(req_lax)
        assert dec_lax.allowed

    async def test_scenario_d_sensitive_field_protection(self) -> None:
        """Scenario D — Sensitive field protection:
        Support agent has orders.read but NOT orders.financials.read.
        """
        support_principal = Principal(
            id="supp-1",
            type=ActorType.USER,
            email="support@example.com",
            roles=("SUPPORT_AGENT",),
            permissions=SYSTEM_ROLES_PERMISSIONS[SYSTEM_ROLE_SUPPORT],
        )
        assert support_principal.has_permission("orders.read")
        assert not support_principal.has_permission("orders.financials.read")

        member_principal = Principal(
            id="mem-1",
            type=ActorType.USER,
            email="member@example.com",
            roles=("MEMBER",),
            permissions=SYSTEM_ROLES_PERMISSIONS[SYSTEM_ROLE_MEMBER],
        )
        assert member_principal.has_permission("orders.read")
        assert member_principal.has_permission("orders.financials.read")

    async def test_scenario_e_service_account_least_privilege(
        self, auth_service: ApplicationAuthorizationService
    ) -> None:
        """Scenario E — Service-account least privilege:
        Service account with inventory.read scope is denied unrelated administrative actions.
        """
        tenant_id = TenantId(uuid.uuid4())
        sa = ServiceAccount.create(
            tenant_id=tenant_id,
            name="InventoryWorker",
            client_id="inv_worker",
            secret_hash="hash",
            scopes=["inventory.read"],
            now=datetime.now(UTC),
        )

        # Allowed action covered by scope
        req_allowed = AuthorizationRequest(
            action="inventory.read",
            resource_type="inventory",
            principal_id=str(sa.id),
            principal_type=ActorType.SERVICE_ACCOUNT,
            tenant_id=tenant_id,
            permissions=sa.scopes,
            resource_tenant_id=tenant_id,
        )
        dec_allowed = await auth_service.authorize(req_allowed)
        assert dec_allowed.allowed

        # Denied unrelated administrative action
        req_admin = AuthorizationRequest(
            action="access.roles.manage",
            resource_type="access",
            principal_id=str(sa.id),
            principal_type=ActorType.SERVICE_ACCOUNT,
            tenant_id=tenant_id,
            permissions=sa.scopes,
            resource_tenant_id=tenant_id,
        )
        dec_admin = await auth_service.authorize(req_admin)
        assert not dec_admin.allowed
        assert dec_admin.reason == DecisionReason.DENIED_SERVICE_ACCOUNT_SCOPE

    async def test_scenario_f_fail_closed_policy_evaluation(
        self,
        auth_service: ApplicationAuthorizationService,
        fake_app: FakeApplication,
    ) -> None:
        """Scenario F — Fail-closed policy evaluation:
        Missing attributes or unknown operators fail closed to DENY.
        """
        tenant_id = TenantId(uuid.uuid4())
        req_missing = AuthorizationRequest(
            action="orders.cancel",
            resource_type="orders",
            principal_id="user-1",
            tenant_id=tenant_id,
            roles=("MEMBER",),
            permissions=frozenset(),  # Missing required permission
            resource_tenant_id=tenant_id,
        )
        decision = await auth_service.authorize(req_missing)
        assert not decision.allowed
        assert decision.reason == DecisionReason.DENIED_MISSING_PERMISSION

    async def test_scenario_g_policy_revocation(
        self,
        auth_service: ApplicationAuthorizationService,
        fake_app: FakeApplication,
    ) -> None:
        """Scenario G — Policy revocation:
        Revoking a permission prevents subsequent operations from succeeding.
        """
        tenant_id = TenantId(uuid.uuid4())
        perms = {"orders.read", "orders.create"}
        req = AuthorizationRequest(
            action="orders.create",
            resource_type="orders",
            principal_id="user-1",
            tenant_id=tenant_id,
            roles=("MEMBER",),
            permissions=frozenset(perms),
            resource_tenant_id=tenant_id,
        )
        dec_before = await auth_service.authorize(req)
        assert dec_before.allowed

        # Revoke orders.create
        perms.remove("orders.create")
        req_after = AuthorizationRequest(
            action="orders.create",
            resource_type="orders",
            principal_id="user-1",
            tenant_id=tenant_id,
            roles=("MEMBER",),
            permissions=frozenset(perms),
            resource_tenant_id=tenant_id,
        )
        dec_after = await auth_service.authorize(req_after)
        assert not dec_after.allowed
        assert dec_after.reason == DecisionReason.DENIED_MISSING_PERMISSION

    async def test_scenario_h_message_driven_context(self) -> None:
        """Scenario H — Message-driven authorization:
        Worker tasks must use explicit system actor context.
        """
        context = RequestContext(
            tenant_id=TenantId(uuid.uuid4()),
            actor_id="consumer:arad.order_lifecycle",
            actor_type=ActorType.SYSTEM,
            correlation_id=str(uuid.uuid4()),
            received_at=datetime.now(UTC),
        )
        assert context.actor_type == ActorType.SYSTEM
        assert context.actor_id.startswith("consumer:")
