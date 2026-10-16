"""API tests for Access Management, RBAC, ABAC, and Field-Level Redaction."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from tests.support import seed

pytestmark = pytest.mark.api


async def setup_tenant_and_users(
    client: httpx.AsyncClient,
) -> tuple[str, str, str, str, str, str]:
    """Helper to provision a tenant, an admin, and a regular member.

    Returns:
        (tenant_id, admin_token, member_token, admin_email, member_email, member_membership_id)
    """
    slug = f"tenant-{uuid4().hex[:8]}"
    resp = await client.post("/v1/tenants", json={"slug": slug, "name": "Access Test Tenant"})
    t_data = seed.expect(resp, 201)
    tenant_id = t_data["id"]
    inv_token = t_data.get("invitation_token", "")

    # Register admin
    admin_email = f"admin-{uuid4().hex[:8]}@example.com"
    await client.post(
        "/v1/auth/register",
        json={
            "email": admin_email,
            "password": "SecurePassword123!",
            "tenant_id": tenant_id,
            "invitation_token": inv_token,
        },
    )
    admin_login = await client.post(
        "/v1/auth/login",
        json={"email": admin_email, "password": "SecurePassword123!", "tenant_id": tenant_id},
    )
    admin_token = seed.expect(admin_login, 200)["access_token"]

    # Admin invites member
    member_email = f"member-{uuid4().hex[:8]}@example.com"
    inv_resp = await client.post(
        "/v1/auth/invitations",
        headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
        json={"email": member_email, "role": "MEMBER"},
    )
    m_inv_token = seed.expect(inv_resp, 201)["invitation_token"]

    # Register member
    await client.post(
        "/v1/auth/register",
        json={
            "email": member_email,
            "password": "SecurePassword123!",
            "tenant_id": tenant_id,
            "invitation_token": m_inv_token,
        },
    )
    member_login = await client.post(
        "/v1/auth/login",
        json={"email": member_email, "password": "SecurePassword123!", "tenant_id": tenant_id},
    )
    m_login_data = seed.expect(member_login, 200)
    member_token = m_login_data["access_token"]

    # Get member's membership id from /v1/auth/me
    me_resp = await client.get("/v1/auth/me", headers={"Authorization": f"Bearer {member_token}"})
    me_data = seed.expect(me_resp, 200)
    membership_id = me_data["active_membership_id"]

    return tenant_id, admin_token, member_token, admin_email, member_email, membership_id


class TestAccessAPI:
    async def test_permissions_catalog(self, client: httpx.AsyncClient) -> None:
        tenant_id, admin_token, _member_token, _, _, _ = await setup_tenant_and_users(client)

        resp = await client.get(
            "/v1/access/permissions",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
        )
        data = seed.expect(resp, 200)
        assert data["total"] > 0
        names = {p["name"] for p in data["permissions"]}
        assert "orders.read" in names
        assert "orders.create" in names
        assert "access.roles.manage" in names
        assert "inventory.adjust" in names

    async def test_roles_listing_and_creation(self, client: httpx.AsyncClient) -> None:
        tenant_id, admin_token, member_token, _, _, _ = await setup_tenant_and_users(client)

        # 1. List roles includes system roles
        list_resp = await client.get(
            "/v1/access/roles",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
        )
        roles = seed.expect(list_resp, 200)
        role_names = {r["name"] for r in roles}
        assert "ADMIN" in role_names
        assert "MEMBER" in role_names
        assert "SUPPORT_AGENT" in role_names
        assert "AUDITOR" in role_names

        # 2. Admin creates custom role
        create_resp = await client.post(
            "/v1/access/roles",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
            json={
                "name": "WAREHOUSE_LEAD",
                "description": "Warehouse team lead",
                "permissions": ["inventory.read", "inventory.adjust"],
            },
        )
        created = seed.expect(create_resp, 201)
        role_id = created["id"]
        assert created["name"] == "WAREHOUSE_LEAD"
        assert not created["is_system"]
        assert "inventory.adjust" in created["permissions"]

        # 3. Get created role
        get_resp = await client.get(
            f"/v1/access/roles/{role_id}",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
        )
        role_data = seed.expect(get_resp, 200)
        assert role_data["id"] == role_id
        assert role_data["name"] == "WAREHOUSE_LEAD"

        # 4. Member cannot create custom role (403 Forbidden)
        unauth_create = await client.post(
            "/v1/access/roles",
            headers={"Authorization": f"Bearer {member_token}", "X-Tenant-Id": tenant_id},
            json={
                "name": "ILLEGAL_ROLE",
                "description": "Privilege escalation attempt",
                "permissions": ["access.roles.manage"],
            },
        )
        seed.expect(unauth_create, 403)

    async def test_policies_crud_and_status(self, client: httpx.AsyncClient) -> None:
        tenant_id, admin_token, member_token, _, _, _ = await setup_tenant_and_users(client)

        # 1. Admin creates ABAC policy
        create_resp = await client.post(
            "/v1/access/policies",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
            json={
                "name": "HighValueOrderApproval",
                "description": "Deny high-value orders without manual approval",
                "effect": "DENY",
                "actions": ["orders.confirm"],
                "resources": ["orders"],
                "conditions": {
                    "field": "resource.total_minor",
                    "operator": "gt",
                    "value": 500000,
                },
            },
        )
        policy_data = seed.expect(create_resp, 201)
        policy_id = policy_data["id"]
        assert policy_data["name"] == "HighValueOrderApproval"
        assert policy_data["status"] == "ACTIVE"
        assert policy_data["effect"] == "DENY"

        # 2. Get policy
        get_resp = await client.get(
            f"/v1/access/policies/{policy_id}",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
        )
        got = seed.expect(get_resp, 200)
        assert got["id"] == policy_id

        # 3. List policies
        list_resp = await client.get(
            "/v1/access/policies",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
        )
        policies = seed.expect(list_resp, 200)
        policy_ids = [p["id"] for p in policies]
        assert policy_id in policy_ids

        # 4. Update policy status to INACTIVE
        status_resp = await client.post(
            f"/v1/access/policies/{policy_id}/status",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
            json={"status": "INACTIVE"},
        )
        updated = seed.expect(status_resp, 200)
        assert updated["status"] == "INACTIVE"

        # 5. Member cannot create policies (403 Forbidden)
        member_create = await client.post(
            "/v1/access/policies",
            headers={"Authorization": f"Bearer {member_token}", "X-Tenant-Id": tenant_id},
            json={
                "name": "RoguePolicy",
                "description": "Bypass policy",
                "effect": "ALLOW",
                "actions": ["orders.confirm"],
                "resources": ["orders"],
            },
        )
        seed.expect(member_create, 403)

    async def test_access_evaluation_endpoint(self, client: httpx.AsyncClient) -> None:
        tenant_id, admin_token, member_token, _, _, _ = await setup_tenant_and_users(client)

        # Admin evaluate allowed action
        eval_admin = await client.post(
            "/v1/access/evaluate",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
            json={
                "action": "access.roles.manage",
                "resource_type": "access",
            },
        )
        res_admin = seed.expect(eval_admin, 200)
        assert res_admin["allowed"] is True
        assert res_admin["action"] == "access.roles.manage"

        # Member evaluate action they lack
        eval_member = await client.post(
            "/v1/access/evaluate",
            headers={"Authorization": f"Bearer {member_token}", "X-Tenant-Id": tenant_id},
            json={
                "action": "access.roles.manage",
                "resource_type": "access",
            },
        )
        res_member = seed.expect(eval_member, 200)
        assert res_member["allowed"] is False
        assert res_member["reason_code"] == "DENIED_MISSING_PERMISSION"

    async def test_membership_role_assignment_and_revocation(
        self, client: httpx.AsyncClient
    ) -> None:
        tenant_id, admin_token, member_token, _, _, membership_id = await setup_tenant_and_users(
            client
        )

        # Admin assigns SUPPORT_AGENT role to member
        assign_resp = await client.post(
            f"/v1/access/memberships/{membership_id}/roles",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
            json={"role_name": "SUPPORT_AGENT"},
        )
        m_view = seed.expect(assign_resp, 200)
        assert "SUPPORT_AGENT" in m_view["roles"]

        # Admin revokes SUPPORT_AGENT role from member
        revoke_resp = await client.delete(
            f"/v1/access/memberships/{membership_id}/roles/SUPPORT_AGENT",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
        )
        revoked_view = seed.expect(revoke_resp, 200)
        assert "SUPPORT_AGENT" not in revoked_view["roles"]

        # Non-admin cannot assign roles (403 Forbidden)
        unauth_assign = await client.post(
            f"/v1/access/memberships/{membership_id}/roles",
            headers={"Authorization": f"Bearer {member_token}", "X-Tenant-Id": tenant_id},
            json={"role_name": "ADMIN"},
        )
        seed.expect(unauth_assign, 403)

    async def test_field_level_financial_redaction(self, client: httpx.AsyncClient) -> None:
        tenant_id, admin_token, _, _, _, _ = await setup_tenant_and_users(client)
        # Create support user
        supp_email = f"support-{uuid4().hex[:8]}@example.com"
        inv_resp = await client.post(
            "/v1/auth/invitations",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
            json={"email": supp_email, "role": "MEMBER"},
        )
        s_inv_token = seed.expect(inv_resp, 201)["invitation_token"]
        await client.post(
            "/v1/auth/register",
            json={
                "email": supp_email,
                "password": "SupportPassword123!",
                "tenant_id": tenant_id,
                "invitation_token": s_inv_token,
            },
        )
        supp_login = await client.post(
            "/v1/auth/login",
            json={"email": supp_email, "password": "SupportPassword123!", "tenant_id": tenant_id},
        )
        s_login_data = seed.expect(supp_login, 200)
        supp_token = s_login_data["access_token"]
        supp_membership_id = s_login_data["active_membership_id"]

        # Admin assigns SUPPORT_AGENT role to support user and revokes default MEMBER role
        await client.post(
            f"/v1/access/memberships/{supp_membership_id}/roles",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
            json={"role_name": "SUPPORT_AGENT"},
        )
        await client.delete(
            f"/v1/access/memberships/{supp_membership_id}/roles/MEMBER",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
        )

        # Provision product, SKU, warehouse, stock
        _, sku_id = await seed.provision_catalog(client, tenant_id=tenant_id)
        wh_id = await seed.create_warehouse(
            client, tenant_id=tenant_id, code=f"W-{uuid4().hex[:4]}", name="Main WH"
        )
        await seed.adjust_stock(
            client, tenant_id=tenant_id, sku_id=sku_id, warehouse_id=wh_id, quantity_delta=50
        )

        # Admin submits order
        order_resp = await client.post(
            "/v1/orders",
            headers={
                "Authorization": f"Bearer {admin_token}",
                "X-Tenant-Id": tenant_id,
                "Idempotency-Key": f"order-{uuid4().hex[:8]}",
            },
            json={
                "lines": [{"sku_id": sku_id, "quantity": 2}],
                "customer_reference": "CUST-001",
            },
        )
        order_data = seed.expect(order_resp, 201)
        order_id = order_data["id"]

        # 1. Admin gets order -> financial fields are present
        admin_order_resp = await client.get(
            f"/v1/orders/{order_id}",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
        )
        admin_order = seed.expect(admin_order_resp, 200)
        assert admin_order["total"] is not None
        assert admin_order["lines"][0]["unit_price"] is not None
        assert admin_order["lines"][0]["line_total"] is not None

        # 2. Support agent gets order -> financial fields are redacted (None)
        supp_order_resp = await client.get(
            f"/v1/orders/{order_id}",
            headers={"Authorization": f"Bearer {supp_token}", "X-Tenant-Id": tenant_id},
        )
        supp_order = seed.expect(supp_order_resp, 200)
        assert supp_order["id"] == order_id
        assert supp_order["total"] is None
        assert supp_order["lines"][0]["unit_price"] is None
        assert supp_order["lines"][0]["line_total"] is None


class TestTenantMembershipAndPolicyCacheIntegration:
    """Verifies end-to-end HTTP cache behavior and immediate invalidation."""

    async def test_policy_cache_invalidation_on_creation_and_deactivation(
        self, client: httpx.AsyncClient
    ) -> None:
        tenant_id, admin_token, member_token, _, _, _ = await setup_tenant_and_users(client)

        # Baseline evaluation: orders.create is permitted for member
        eval_resp = await client.post(
            "/v1/access/evaluate",
            headers={"Authorization": f"Bearer {member_token}", "X-Tenant-Id": tenant_id},
            json={"action": "orders.create", "resource_type": "orders"},
        )
        assert eval_resp.status_code == 200
        assert eval_resp.json()["allowed"] is True

        # Admin creates an explicit DENY policy for orders.create
        policy_resp = await client.post(
            "/v1/access/policies",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
            json={
                "name": "EmergencyDenyOrders",
                "description": "Block all order creations",
                "effect": "DENY",
                "actions": ["orders.create"],
                "resources": ["orders"],
            },
        )
        assert policy_resp.status_code == 201
        policy_id = policy_resp.json()["id"]

        # Immediate evaluation MUST be denied (proving cache invalidation occurred on creation)
        eval_after_deny = await client.post(
            "/v1/access/evaluate",
            headers={"Authorization": f"Bearer {member_token}", "X-Tenant-Id": tenant_id},
            json={"action": "orders.create", "resource_type": "orders"},
        )
        assert eval_after_deny.status_code == 200
        assert eval_after_deny.json()["allowed"] is False

        # Admin deactivates the policy
        status_resp = await client.patch(
            f"/v1/access/policies/{policy_id}/status",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
            json={"status": "INACTIVE"},
        )
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "INACTIVE"

        # Immediate evaluation MUST be allowed again
        # (proving cache invalidation occurred on status update)
        eval_after_restore = await client.post(
            "/v1/access/evaluate",
            headers={"Authorization": f"Bearer {member_token}", "X-Tenant-Id": tenant_id},
            json={"action": "orders.create", "resource_type": "orders"},
        )
        assert eval_after_restore.status_code == 200
        assert eval_after_restore.json()["allowed"] is True

    async def test_headerless_tenant_context_still_enforces_active_membership(
        self, client: httpx.AsyncClient
    ) -> None:
        tenant_id, _admin_token, member_token, _, _, _ = await setup_tenant_and_users(client)

        # Request omitting X-Tenant-Id header resolves tenant from member_token claims
        resp = await client.get(
            "/v1/access/me",
            headers={"Authorization": f"Bearer {member_token}"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] is not None
        assert data["active_tenant_id"] == tenant_id
