"""Unit tests for Phase 4.2 authorization domain models and evaluators."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from identity.domain.decision import (
    AuthorizationRequest,
    DecisionReason,
)
from identity.domain.evaluator import PolicyEvaluator
from identity.domain.permission import (
    ALL_PERMISSIONS,
    Permission,
    has_permission,
    match_permission,
)
from identity.domain.policy import (
    AccessPolicy,
    PolicyEffect,
    evaluate_condition,
)
from identity.domain.principal import Principal
from identity.domain.role import (
    SYSTEM_ROLE_ADMIN,
    SYSTEM_ROLE_AUDITOR,
    SYSTEM_ROLE_MEMBER,
    SYSTEM_ROLE_SUPPORT,
    SYSTEM_ROLES_PERMISSIONS,
    Role,
)
from identity.domain.service_account import ServiceAccount
from shared.application.context import ActorType
from shared.domain.errors import InvalidValue
from shared.domain.identifiers import TenantId


class TestPermissionCatalog:
    def test_canonical_permissions_defined(self) -> None:
        assert Permission.ORDERS_READ.value in ALL_PERMISSIONS
        assert Permission.ORDERS_CREATE.value in ALL_PERMISSIONS
        assert Permission.ORDERS_CANCEL.value in ALL_PERMISSIONS
        assert Permission.ORDERS_CONFIRM.value in ALL_PERMISSIONS
        assert Permission.ORDERS_FINANCIALS_READ.value in ALL_PERMISSIONS
        assert Permission.INVENTORY_READ.value in ALL_PERMISSIONS
        assert Permission.INVENTORY_ADJUST.value in ALL_PERMISSIONS
        assert Permission.CATALOG_PRODUCTS_READ.value in ALL_PERMISSIONS
        assert Permission.CATALOG_PRODUCTS_MANAGE.value in ALL_PERMISSIONS
        assert Permission.ACCESS_ROLES_MANAGE.value in ALL_PERMISSIONS
        assert Permission.ACCESS_POLICIES_MANAGE.value in ALL_PERMISSIONS
        assert Permission.AUDIT_EVENTS_READ.value in ALL_PERMISSIONS

    def test_match_permission_exact(self) -> None:
        assert match_permission("orders.read", "orders.read")
        assert not match_permission("orders.read", "orders.create")

    def test_match_permission_wildcard(self) -> None:
        assert match_permission("*", "orders.read")
        assert match_permission("*", "any.permission.id")
        assert match_permission("orders.*", "orders.read")
        assert match_permission("orders.*", "orders.create")
        assert not match_permission("orders.*", "inventory.read")

    def test_has_permission(self) -> None:
        granted = frozenset({"orders.read", "inventory.*"})
        assert has_permission(granted, "orders.read")
        assert has_permission(granted, "inventory.read")
        assert has_permission(granted, "inventory.adjust")
        assert not has_permission(granted, "orders.create")

        admin_granted = frozenset({"*"})
        assert has_permission(admin_granted, "anything")


class TestRoleDomain:
    def test_system_roles_exist(self) -> None:
        assert SYSTEM_ROLE_ADMIN in SYSTEM_ROLES_PERMISSIONS
        assert SYSTEM_ROLE_MEMBER in SYSTEM_ROLES_PERMISSIONS
        assert SYSTEM_ROLE_SUPPORT in SYSTEM_ROLES_PERMISSIONS
        assert SYSTEM_ROLE_AUDITOR in SYSTEM_ROLES_PERMISSIONS

        now = datetime.now(UTC)
        admin = Role.create(
            name=SYSTEM_ROLE_ADMIN,
            description="Full admin",
            permissions=SYSTEM_ROLES_PERMISSIONS[SYSTEM_ROLE_ADMIN],
            now=now,
            is_system=True,
        )
        assert admin.is_system
        assert admin.has_permission("orders.read")

        support = Role.create(
            name=SYSTEM_ROLE_SUPPORT,
            description="Support agent",
            permissions=SYSTEM_ROLES_PERMISSIONS[SYSTEM_ROLE_SUPPORT],
            now=now,
            is_system=True,
        )
        assert support.has_permission("orders.read")
        assert not support.has_permission("orders.financials.read")

    def test_custom_role_creation_and_mutation(self) -> None:
        tenant_id = TenantId(uuid.uuid4())
        now = datetime.now(UTC)
        role = Role.create(
            tenant_id=tenant_id,
            name="WAREHOUSE_MANAGER",
            description="Manages warehouses",
            permissions=["inventory.read", "inventory.adjust"],
            now=now,
            is_system=False,
        )
        assert not role.is_system
        assert role.tenant_id == tenant_id
        assert role.has_permission("inventory.read")
        assert not role.has_permission("orders.create")

        role.update_permissions(["inventory.read", "inventory.adjust", "orders.read"], now=now)
        assert role.has_permission("orders.read")

    def test_system_role_cannot_be_mutated(self) -> None:
        now = datetime.now(UTC)
        admin = Role.create(
            name=SYSTEM_ROLE_ADMIN,
            description="Admin",
            permissions=SYSTEM_ROLES_PERMISSIONS[SYSTEM_ROLE_ADMIN],
            now=now,
            is_system=True,
        )
        with pytest.raises(InvalidValue, match="system roles cannot be modified"):
            admin.update_permissions(["orders.read"], now=now)


class TestConditionEvaluator:
    def test_equality_and_inequality(self) -> None:
        ctx = {"subject": {"role": "OPERATOR"}, "resource": {"warehouse_id": "LAX"}}
        assert evaluate_condition(
            {"field": "subject.role", "operator": "eq", "value": "OPERATOR"}, ctx
        )
        assert not evaluate_condition(
            {"field": "subject.role", "operator": "eq", "value": "ADMIN"}, ctx
        )
        assert evaluate_condition(
            {"field": "subject.role", "operator": "neq", "value": "ADMIN"}, ctx
        )

    def test_set_membership(self) -> None:
        ctx = {"resource": {"warehouse_id": "LAX"}}
        assert evaluate_condition(
            {"field": "resource.warehouse_id", "operator": "in", "value": ["LAX", "SFO"]}, ctx
        )
        assert not evaluate_condition(
            {"field": "resource.warehouse_id", "operator": "in", "value": ["JFK", "ORD"]}, ctx
        )
        assert evaluate_condition(
            {"field": "resource.warehouse_id", "operator": "not_in", "value": ["JFK", "ORD"]}, ctx
        )

    def test_numeric_comparisons(self) -> None:
        ctx = {"resource": {"amount": 150}}
        assert evaluate_condition({"field": "resource.amount", "operator": "gt", "value": 100}, ctx)
        assert evaluate_condition(
            {"field": "resource.amount", "operator": "gte", "value": 150}, ctx
        )
        assert evaluate_condition({"field": "resource.amount", "operator": "lt", "value": 200}, ctx)
        assert evaluate_condition(
            {"field": "resource.amount", "operator": "lte", "value": 150}, ctx
        )
        assert not evaluate_condition(
            {"field": "resource.amount", "operator": "gt", "value": 200}, ctx
        )

    def test_contains_and_exists(self) -> None:
        ctx = {"subject": {"tags": ["vip", "verified"]}}
        assert evaluate_condition(
            {"field": "subject.tags", "operator": "contains", "value": "vip"}, ctx
        )
        assert not evaluate_condition(
            {"field": "subject.tags", "operator": "contains", "value": "banned"}, ctx
        )
        assert evaluate_condition({"field": "subject.tags", "operator": "exists"}, ctx)
        assert not evaluate_condition({"field": "subject.unknown", "operator": "exists"}, ctx)

    def test_boolean_composition(self) -> None:
        ctx = {"subject": {"department": "logistics"}, "resource": {"status": "OPEN"}}
        cond = {
            "operator": "and",
            "clauses": [
                {"field": "subject.department", "operator": "eq", "value": "logistics"},
                {"field": "resource.status", "operator": "eq", "value": "OPEN"},
            ],
        }
        assert evaluate_condition(cond, ctx)

        or_cond = {
            "operator": "or",
            "clauses": [
                {"field": "subject.department", "operator": "eq", "value": "finance"},
                {"field": "resource.status", "operator": "eq", "value": "OPEN"},
            ],
        }
        assert evaluate_condition(or_cond, ctx)

        not_cond = {
            "operator": "not",
            "clause": {"field": "subject.department", "operator": "eq", "value": "finance"},
        }
        assert evaluate_condition(not_cond, ctx)

    def test_fail_closed_on_missing_or_invalid_attribute(self) -> None:
        ctx = {"subject": {"id": "123"}}
        assert not evaluate_condition(
            {"field": "subject.nonexistent", "operator": "eq", "value": "val"}, ctx
        )
        with pytest.raises(InvalidValue):
            evaluate_condition({"field": "subject.id", "operator": "evil_op", "value": "123"}, ctx)


class TestPolicyEvaluatorEngine:
    def test_default_deny_when_no_permissions(self) -> None:
        evaluator = PolicyEvaluator()
        req = AuthorizationRequest(
            action="orders.create",
            resource_type="orders",
            principal_id="u-1",
            roles=("MEMBER",),
            permissions=frozenset({"orders.read"}),
        )
        decision = evaluator.evaluate(req, policies=[])
        assert not decision.allowed
        assert decision.reason == DecisionReason.DENIED_MISSING_PERMISSION

    def test_allow_by_rbac_permission(self) -> None:
        evaluator = PolicyEvaluator()
        req = AuthorizationRequest(
            action="orders.create",
            resource_type="orders",
            principal_id="u-1",
            roles=("MEMBER",),
            permissions=frozenset({"orders.read", "orders.create"}),
        )
        decision = evaluator.evaluate(req, policies=[])
        assert decision.allowed
        assert decision.reason == DecisionReason.ALLOWED_BY_RBAC

    def test_cross_tenant_denial(self) -> None:
        evaluator = PolicyEvaluator()
        t1 = TenantId(uuid.uuid4())
        t2 = TenantId(uuid.uuid4())
        req = AuthorizationRequest(
            action="orders.read",
            resource_type="orders",
            principal_id="u-1",
            tenant_id=t1,
            roles=("ADMIN",),
            permissions=frozenset({"*"}),
            resource_tenant_id=t2,
        )
        decision = evaluator.evaluate(req, policies=[])
        assert not decision.allowed
        assert decision.reason == DecisionReason.DENIED_CROSS_TENANT

    def test_abac_warehouse_scoped_policy(self) -> None:
        tenant_id = TenantId(uuid.uuid4())
        now = datetime.now(UTC)
        evaluator = PolicyEvaluator()

        # ABAC rule: user must belong to same warehouse as fulfillment/inventory
        policy = AccessPolicy.create(
            name="WarehouseIsolation",
            description="Operators can only modify resources at their assigned warehouse",
            effect=PolicyEffect.DENY,
            actions=["fulfillments.modify"],
            resources=["fulfillments"],
            conditions={
                "field": "subject.assigned_warehouse",
                "operator": "neq",
                "value_field": "resource.warehouse_id",
            },
            tenant_id=tenant_id,
            now=now,
        )

        # Mismatched warehouse -> DENIED by policy
        req_mismatch = AuthorizationRequest(
            action="fulfillments.modify",
            resource_type="fulfillments",
            principal_id="op-1",
            tenant_id=tenant_id,
            roles=("OPERATOR",),
            permissions=frozenset({"fulfillments.modify"}),
            resource_tenant_id=tenant_id,
            subject_attributes={"assigned_warehouse": "LAX"},
            resource_attributes={"warehouse_id": "SEA"},
        )
        dec_mismatch = evaluator.evaluate(req_mismatch, policies=[policy])
        assert not dec_mismatch.allowed
        assert dec_mismatch.reason == DecisionReason.DENIED_BY_ABAC_POLICY

        # Matching warehouse -> ALLOWED
        req_match = AuthorizationRequest(
            action="fulfillments.modify",
            resource_type="fulfillments",
            principal_id="op-1",
            tenant_id=tenant_id,
            roles=("OPERATOR",),
            permissions=frozenset({"fulfillments.modify"}),
            resource_tenant_id=tenant_id,
            subject_attributes={"assigned_warehouse": "LAX"},
            resource_attributes={"warehouse_id": "LAX"},
        )
        dec_match = evaluator.evaluate(req_match, policies=[policy])
        assert dec_match.allowed


class TestPrincipalAndServiceAccount:
    def test_principal_permissions(self) -> None:
        p = Principal(
            id="user-1",
            type=ActorType.USER,
            email="user@test.com",
            roles=("MEMBER",),
            permissions=frozenset({"orders.read", "orders.create"}),
        )
        assert p.has_permission("orders.read")
        assert not p.has_permission("orders.cancel")
        assert not p.is_tenant_admin

        p_admin = Principal(
            id="admin-1",
            type=ActorType.USER,
            email="admin@test.com",
            roles=("ADMIN",),
            permissions=frozenset({"*"}),
        )
        assert p_admin.is_tenant_admin
        assert p_admin.has_permission("any.action")

    def test_service_account_scopes(self) -> None:
        sa = ServiceAccount.create(
            tenant_id=TenantId(uuid.uuid4()),
            name="InventorySync",
            client_id="sa_sync",
            secret_hash="hash",
            scopes=["inventory.read", "inventory.adjust"],
            now=datetime.now(UTC),
        )
        assert sa.has_scope("inventory.read")
        assert sa.has_scope("inventory.adjust")
        assert not sa.has_scope("orders.create")
