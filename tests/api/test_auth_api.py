"""API tests for Identity, Authentication & Tenant Membership endpoints."""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest
from tests.support import seed

pytestmark = pytest.mark.api


async def create_test_tenant(client: httpx.AsyncClient, slug: str, name: str) -> tuple[str, str]:
    response = await client.post(
        "/v1/tenants",
        json={"slug": slug, "name": name},
    )
    data = seed.expect(response, 201)
    return data["id"], data.get("invitation_token", "")


class TestAuthAPI:
    async def test_register_and_login_flow(self, client: httpx.AsyncClient) -> None:
        tenant_id, inv_token = await create_test_tenant(
            client, f"tenant-{uuid4().hex[:8]}", "Test Tenant"
        )

        # 1. Register User using valid onboarding invitation token
        email = f"user-{uuid4().hex[:8]}@example.com"
        reg_response = await client.post(
            "/v1/auth/register",
            json={
                "email": email,
                "password": "SecurePassword123!",
                "tenant_id": tenant_id,
                "invitation_token": inv_token,
            },
        )
        reg_data = seed.expect(reg_response, 201)
        assert reg_data["email"] == email
        assert reg_data["status"] == "ACTIVE"
        assert "password_hash" not in reg_data
        assert "password" not in reg_data

        # 2. Duplicate registration fails with 409
        dup_response = await client.post(
            "/v1/auth/register",
            json={
                "email": email,
                "password": "SecurePassword123!",
            },
        )
        seed.expect(dup_response, 409)

        # 3. Login with wrong password fails with 401
        bad_login = await client.post(
            "/v1/auth/login",
            json={"email": email, "password": "WrongPassword!"},
        )
        seed.expect(bad_login, 401)

        # 4. Login with correct credentials succeeds with 200
        login_response = await client.post(
            "/v1/auth/login",
            json={"email": email, "password": "SecurePassword123!", "tenant_id": tenant_id},
        )
        login_data = seed.expect(login_response, 200)
        access_token = login_data["access_token"]
        refresh_token = login_data["refresh_token"]
        assert login_data["token_type"] == "bearer"
        assert login_data["expires_in"] > 0
        assert login_data["active_tenant_id"] == tenant_id
        assert login_data["user"]["email"] == email

        # 5. Access /v1/auth/me without token fails with 401
        unauth_me = await client.get("/v1/auth/me")
        seed.expect(unauth_me, 401)

        # 6. Access /v1/auth/me with Bearer token succeeds with 200
        auth_me = await client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        me_data = seed.expect(auth_me, 200)
        assert me_data["email"] == email
        assert me_data["type"] == "user"
        assert me_data["account_status"] == "ACTIVE"
        assert me_data["active_tenant_id"] == tenant_id
        assert me_data["role"] == "ADMIN"

        # 7. List memberships
        memberships_resp = await client.get(
            "/v1/auth/memberships",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        memberships = seed.expect(memberships_resp, 200)
        assert len(memberships) >= 1
        assert any(m["tenant_id"] == tenant_id for m in memberships)

        # 8. Refresh access token
        refresh_resp = await client.post(
            "/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        refresh_data = seed.expect(refresh_resp, 200)
        new_access_token = refresh_data["access_token"]
        new_refresh_token = refresh_data["refresh_token"]
        assert new_refresh_token != refresh_token

        # 9. Reuse detection: using the rotated old refresh token fails with 401
        reuse_resp = await client.post(
            "/v1/auth/refresh",
            json={"refresh_token": refresh_token},
        )
        seed.expect(reuse_resp, 401)

        # 10. Change Password
        change_resp = await client.post(
            "/v1/auth/change-password",
            headers={"Authorization": f"Bearer {new_access_token}"},
            json={
                "current_password": "SecurePassword123!",
                "new_password": "BrandNewPassword456!",
            },
        )
        seed.expect(change_resp, 200)

        # Old password cannot login anymore
        old_login_resp = await client.post(
            "/v1/auth/login",
            json={"email": email, "password": "SecurePassword123!"},
        )
        seed.expect(old_login_resp, 401)

        # New password logs in successfully
        new_login_resp = await client.post(
            "/v1/auth/login",
            json={"email": email, "password": "BrandNewPassword456!"},
        )
        seed.expect(new_login_resp, 200)

        # 11. Logout
        logout_token = seed.expect(new_login_resp, 200)["refresh_token"]
        logout_resp = await client.post(
            "/v1/auth/logout",
            json={"refresh_token": logout_token},
        )
        assert logout_resp.status_code == 204

        # Refreshing after logout fails
        post_logout_refresh = await client.post(
            "/v1/auth/refresh",
            json={"refresh_token": logout_token},
        )
        seed.expect(post_logout_refresh, 401)

    async def test_anonymous_user_cannot_join_tenant_by_uuid(
        self, client: httpx.AsyncClient
    ) -> None:
        tenant_id, _ = await create_test_tenant(
            client, f"victim-{uuid4().hex[:8]}", "Victim Tenant"
        )

        # Anonymous caller attempts to register into tenant_id without an invitation token
        resp = await client.post(
            "/v1/auth/register",
            json={
                "email": f"attacker-{uuid4().hex[:8]}@example.com",
                "password": "AttackerPassword123!",
                "tenant_id": tenant_id,
            },
        )
        seed.expect(resp, 403)

    async def test_invalid_or_expired_invitation_rejected(self, client: httpx.AsyncClient) -> None:
        tenant_id, _ = await create_test_tenant(client, f"inv-fail-{uuid4().hex[:8]}", "Inv Fail")

        # Completely forged invitation token
        resp = await client.post(
            "/v1/auth/register",
            json={
                "email": f"user-{uuid4().hex[:8]}@example.com",
                "password": "Password123!",
                "tenant_id": tenant_id,
                "invitation_token": "forged.fake.token",
            },
        )
        seed.expect(resp, 401)

    async def test_tenant_selection_and_switch(self, client: httpx.AsyncClient) -> None:
        t1_id, t1_inv = await create_test_tenant(client, f"t1-{uuid4().hex[:8]}", "Tenant One")
        t3_unrelated, _ = await create_test_tenant(client, f"t3-{uuid4().hex[:8]}", "Tenant Three")

        email = f"multi-{uuid4().hex[:8]}@example.com"
        reg = await client.post(
            "/v1/auth/register",
            json={
                "email": email,
                "password": "Password123!",
                "tenant_id": t1_id,
                "invitation_token": t1_inv,
            },
        )
        seed.expect(reg, 201)

        login_resp = await client.post(
            "/v1/auth/login",
            json={"email": email, "password": "Password123!", "tenant_id": t1_id},
        )
        token = seed.expect(login_resp, 200)["access_token"]

        # Attempt to select t3 (not a member) -> 403 Forbidden
        select_forbidden = await client.post(
            "/v1/auth/select-tenant",
            headers={"Authorization": f"Bearer {token}"},
            json={"tenant_id": t3_unrelated},
        )
        seed.expect(select_forbidden, 403)

    async def test_service_account_api_lifecycle(self, client: httpx.AsyncClient) -> None:
        tenant_id, inv_token = await create_test_tenant(
            client, f"sa-{uuid4().hex[:8]}", "SA Tenant"
        )

        # Register Admin User using invitation token
        admin_email = f"admin-{uuid4().hex[:8]}@example.com"
        await client.post(
            "/v1/auth/register",
            json={
                "email": admin_email,
                "password": "AdminPassword123!",
                "tenant_id": tenant_id,
                "invitation_token": inv_token,
            },
        )

        # Login as Admin
        admin_login = await client.post(
            "/v1/auth/login",
            json={"email": admin_email, "password": "AdminPassword123!", "tenant_id": tenant_id},
        )
        admin_token = seed.expect(admin_login, 200)["access_token"]

        # Admin invites ordinary MEMBER
        member_email = f"member-{uuid4().hex[:8]}@example.com"
        inv_resp = await client.post(
            "/v1/auth/invitations",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
            json={"email": member_email, "role": "MEMBER"},
        )
        member_inv_token = seed.expect(inv_resp, 201)["invitation_token"]

        # Register as ordinary member
        await client.post(
            "/v1/auth/register",
            json={
                "email": member_email,
                "password": "MemberPassword123!",
                "tenant_id": tenant_id,
                "invitation_token": member_inv_token,
            },
        )
        member_login = await client.post(
            "/v1/auth/login",
            json={"email": member_email, "password": "MemberPassword123!", "tenant_id": tenant_id},
        )
        member_token = seed.expect(member_login, 200)["access_token"]

        # 1. Ordinary member attempts to create service account -> 403 Forbidden
        client_id = f"sa-{uuid4().hex[:8]}"
        unauth_create = await client.post(
            "/v1/auth/service-accounts",
            headers={"Authorization": f"Bearer {member_token}", "X-Tenant-Id": tenant_id},
            json={"name": "Worker Bot", "client_id": client_id},
        )
        seed.expect(unauth_create, 403)

        # 2. Admin creates Service Account -> 201 Created
        create_resp = await client.post(
            "/v1/auth/service-accounts",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
            json={"name": "Worker Bot", "client_id": client_id},
        )
        create_data = seed.expect(create_resp, 201)
        sa_id = create_data["id"]
        client_secret = create_data["client_secret"]
        assert len(client_secret) >= 32

        # 3. Authenticate Service Account
        auth_resp = await client.post(
            "/v1/auth/service-accounts/token",
            json={"client_id": client_id, "client_secret": client_secret},
        )
        auth_data = seed.expect(auth_resp, 200)
        sa_token = auth_data["access_token"]
        assert auth_data["service_account"]["client_id"] == client_id

        # 4. Verify /v1/auth/me with SA token
        me_resp = await client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {sa_token}"},
        )
        me_data = seed.expect(me_resp, 200)
        assert me_data["type"] == "service_account"
        assert me_data["active_tenant_id"] == tenant_id

        # 5. Ordinary member attempts to rotate SA secret -> 403 Forbidden
        unauth_rotate = await client.post(
            f"/v1/auth/service-accounts/{sa_id}/rotate",
            headers={"Authorization": f"Bearer {member_token}", "X-Tenant-Id": tenant_id},
        )
        seed.expect(unauth_rotate, 403)

        # 6. Admin rotates SA secret -> 200 OK
        rotate_resp = await client.post(
            f"/v1/auth/service-accounts/{sa_id}/rotate",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
        )
        rotate_data = seed.expect(rotate_resp, 200)
        new_secret = rotate_data["client_secret"]
        assert new_secret != client_secret

        # Old secret fails authentication -> 401
        old_auth = await client.post(
            "/v1/auth/service-accounts/token",
            json={"client_id": client_id, "client_secret": client_secret},
        )
        seed.expect(old_auth, 401)

        # New secret authenticates successfully -> 200
        new_auth = await client.post(
            "/v1/auth/service-accounts/token",
            json={"client_id": client_id, "client_secret": new_secret},
        )
        seed.expect(new_auth, 200)

        # 7. Ordinary member attempts to revoke SA -> 403 Forbidden
        unauth_revoke = await client.delete(
            f"/v1/auth/service-accounts/{sa_id}",
            headers={"Authorization": f"Bearer {member_token}", "X-Tenant-Id": tenant_id},
        )
        seed.expect(unauth_revoke, 403)

        # 8. Admin revokes Service Account -> 200 OK
        revoke_resp = await client.delete(
            f"/v1/auth/service-accounts/{sa_id}",
            headers={"Authorization": f"Bearer {admin_token}", "X-Tenant-Id": tenant_id},
        )
        seed.expect(revoke_resp, 200)

        # 9. Revoked SA cannot authenticate anymore -> 403
        revoked_auth = await client.post(
            "/v1/auth/service-accounts/token",
            json={"client_id": client_id, "client_secret": new_secret},
        )
        seed.expect(revoked_auth, 403)

    async def test_authenticated_cross_tenant_access_prevented(
        self, client: httpx.AsyncClient
    ) -> None:
        t_a, inv_a = await create_test_tenant(client, f"tenant-a-{uuid4().hex[:8]}", "Tenant A")
        t_b, _ = await create_test_tenant(client, f"tenant-b-{uuid4().hex[:8]}", "Tenant B")

        # Register and login into Tenant A
        user_email = f"user-a-{uuid4().hex[:8]}@example.com"
        await client.post(
            "/v1/auth/register",
            json={
                "email": user_email,
                "password": "Password123!",
                "tenant_id": t_a,
                "invitation_token": inv_a,
            },
        )
        login_resp = await client.post(
            "/v1/auth/login",
            json={"email": user_email, "password": "Password123!", "tenant_id": t_a},
        )
        token_a = seed.expect(login_resp, 200)["access_token"]

        # Call business route (catalog) with valid tenant header -> succeeds
        ok_resp = await client.get(
            "/v1/products",
            headers={"Authorization": f"Bearer {token_a}", "X-Tenant-Id": t_a},
        )
        assert ok_resp.status_code == 200

        # Attempt to access Tenant B with Tenant A's token -> 403 Forbidden!
        spoofed_resp = await client.get(
            "/v1/products",
            headers={"Authorization": f"Bearer {token_a}", "X-Tenant-Id": t_b},
        )
        seed.expect(spoofed_resp, 403)

    async def test_malformed_and_invalid_tokens(self, client: httpx.AsyncClient) -> None:
        # Invalid authorization header format
        bad_format = await client.get("/v1/auth/me", headers={"Authorization": "NotBearer 12345"})
        seed.expect(bad_format, 401)

        # Malformed JWT
        malformed = await client.get("/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
        seed.expect(malformed, 401)

        # Token signed with wrong secret
        from datetime import UTC, datetime, timedelta

        import jwt

        bad_secret_token = jwt.encode(
            {
                "sub": str(uuid4()),
                "type": "access",
                "exp": int((datetime.now(UTC) + timedelta(minutes=15)).timestamp()),
                "iss": "arad-platform",
                "aud": "arad-api",
            },
            "wrong-secret-key-that-does-not-match",
            algorithm="HS256",
        )
        bad_sig = await client.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {bad_secret_token}"}
        )
        seed.expect(bad_sig, 401)

        # Expired token
        expired_token = jwt.encode(
            {
                "sub": str(uuid4()),
                "type": "access",
                "exp": int((datetime.now(UTC) - timedelta(minutes=15)).timestamp()),
                "iss": "arad-platform",
                "aud": "arad-api",
            },
            "insecure-default-change-me-in-production-only-for-local-dev-min-32-chars",
            algorithm="HS256",
        )
        expired_resp = await client.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {expired_token}"}
        )
        seed.expect(expired_resp, 401)

    async def test_public_health_endpoints_remain_accessible(
        self, client: httpx.AsyncClient
    ) -> None:
        health_resp = await client.get("/healthz")
        assert health_resp.status_code == 200

        ready_resp = await client.get("/readyz")
        assert ready_resp.status_code == 200
