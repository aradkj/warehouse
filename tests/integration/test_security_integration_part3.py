"""Comprehensive Security Integration & Operational Authorization Tests (Phase 4 Part 3).

Verifies end-to-end security consistency across:
- HTTP and direct Application use cases / queries
- Early idempotency authorization check and replay authorization verification
- Tenant mismatch fail-closed enforcement across principal, context, and command
- Message-driven RabbitMQ consumers and header-tampering rejection
- Celery / background worker trusted system execution vs unprivileged callers
- Search reindex & reconciliation tenant isolation (preventing AUTH-001 regression)
- Service account scope validation & privilege boundaries
- ABAC decision traceability with correlation and causation propagation
- PostgreSQL RLS isolation consistency
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from tests.api.test_access_api import setup_tenant_and_users
from tests.conftest import test_database_url as get_test_database_url

from bootstrap.config import Settings, get_settings
from bootstrap.dependencies import build_use_cases
from identity.application.commands import (
    AuthenticateServiceAccountCommand,
    CreateServiceAccountCommand,
    RegisterTenantCommand,
)
from identity.domain.principal import Principal
from identity.infrastructure.security import TokenService
from inventory.application.commands import (
    ExpireReservationsCommand,
)
from ordering.application.commands import (
    CreateOrderCommand,
)
from search.adapters.postgres import SqlAlchemyCatalogSearchReader
from shared.application.context import ActorType, RequestContext
from shared.application.errors import ApplicationError, Forbidden, Unauthorized
from shared.domain.clock import SystemClock
from shared.domain.identifiers import MembershipId, OrderId, TenantId
from shared.infrastructure.db.session import Database

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------


def make_principal(
    *,
    actor_id: str | None = None,
    actor_type: ActorType = ActorType.USER,
    tenant_id: uuid.UUID | None = None,
    role: str = "MEMBER",
    roles: tuple[str, ...] = ("MEMBER",),
    permissions: frozenset[str] = frozenset(),
    attributes: dict[str, Any] | None = None,
) -> Principal:
    tid = TenantId(tenant_id) if tenant_id is not None else None
    return Principal(
        id=actor_id or str(uuid.uuid4()),
        type=actor_type,
        active_tenant_id=tid,
        active_membership_id=MembershipId(uuid.uuid4()) if tid else None,
        role=role,
        roles=roles,
        permissions=permissions,
        attributes=attributes or {},
    )


def make_context(
    principal: Principal,
    *,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    idempotency_key: str | None = None,
) -> RequestContext:
    return RequestContext(
        tenant_id=principal.active_tenant_id,
        actor_id=principal.id,
        actor_type=principal.type,
        correlation_id=correlation_id or str(uuid.uuid4()),
        causation_id=causation_id,
        idempotency_key=idempotency_key,
        received_at=datetime.now(UTC),
        principal=principal,
    )


# ---------------------------------------------------------------------------
# 1. Application Layer Direct Authorization Consistency
# ---------------------------------------------------------------------------


class TestApplicationLayerAuthorizationConsistency:
    async def test_use_case_unauthenticated_fails_closed(self) -> None:
        """Calling a protected use case without an authenticated principal must fail closed."""
        db = Database(get_test_database_url(), pool_size=2)
        try:
            scope = db.new_scope()
            auth_settings = Settings(auth_required=True)
            use_cases = build_use_cases(settings=auth_settings, clock=SystemClock(), scope=scope)
            tenant_id = uuid.uuid4()

            # Context with NO principal attached
            unauthenticated_context = RequestContext(
                tenant_id=TenantId(tenant_id),
                actor_id="user-1",
                actor_type=ActorType.USER,
                correlation_id=str(uuid.uuid4()),
                received_at=datetime.now(UTC),
                principal=None,
            )

            cmd = CreateOrderCommand(
                tenant_id=TenantId(tenant_id),
                context=unauthenticated_context,
                lines=(),
            )

            with pytest.raises((Forbidden, Unauthorized)):
                await use_cases.create_order(cmd)
        finally:
            await db.dispose()

    async def test_use_case_unauthorized_fails_closed(self) -> None:
        """Calling a protected use case with principal missing required permission
        must fail closed.
        """
        db = Database(get_test_database_url(), pool_size=2)
        try:
            scope = db.new_scope()
            auth_settings = Settings(auth_required=True)
            use_cases = build_use_cases(settings=auth_settings, clock=SystemClock(), scope=scope)
            tenant_id = uuid.uuid4()

            # Principal with only read permissions, attempting a mutation
            principal = make_principal(
                tenant_id=tenant_id,
                permissions=frozenset({"orders.read"}),
            )
            context = make_context(principal)

            cmd = CreateOrderCommand(
                tenant_id=TenantId(tenant_id),
                context=context,
                lines=(),
            )

            with pytest.raises(Forbidden):
                await use_cases.create_order(cmd)
        finally:
            await db.dispose()

    async def test_query_use_case_unauthorized_fails_closed(self) -> None:
        """Directly querying via QueryUseCase without orders.read permission must fail closed."""
        from ordering.application.commands import GetOrderQuery

        db = Database(get_test_database_url(), pool_size=2)
        try:
            scope = db.new_scope()
            auth_settings = Settings(auth_required=True)
            use_cases = build_use_cases(settings=auth_settings, clock=SystemClock(), scope=scope)
            tenant_id = uuid.uuid4()

            principal = make_principal(
                tenant_id=tenant_id,
                permissions=frozenset({"inventory.read"}),  # lacks orders.read
            )
            context = make_context(principal)

            query = GetOrderQuery(
                tenant_id=TenantId(tenant_id),
                context=context,
                order_id=OrderId(uuid.uuid4()),
            )

            with pytest.raises(Forbidden):
                await use_cases.get_order(query)
        finally:
            await db.dispose()

    async def test_tenant_mismatch_between_principal_and_command_fails_closed(self) -> None:
        """Principal belonging to Tenant A attempting to mutate Tenant B must fail closed."""
        db = Database(get_test_database_url(), pool_size=2)
        try:
            scope = db.new_scope()
            auth_settings = Settings(auth_required=True)
            use_cases = build_use_cases(settings=auth_settings, clock=SystemClock(), scope=scope)
            tenant_a = uuid.uuid4()
            tenant_b = uuid.uuid4()

            # Principal is registered under Tenant A with full permissions
            principal_a = make_principal(
                tenant_id=tenant_a,
                permissions=frozenset({"*"}),
            )
            context_a = make_context(principal_a)

            # Command attempts to target Tenant B
            cmd = CreateOrderCommand(
                tenant_id=TenantId(tenant_b),
                context=context_a,
                lines=(),
            )

            with pytest.raises(Forbidden):
                await use_cases.create_order(cmd)
        finally:
            await db.dispose()

    async def test_tenant_mismatch_in_query_fails_closed(self) -> None:
        """Principal from Tenant A querying Tenant B orders must fail closed."""
        from ordering.application.commands import GetOrderQuery

        db = Database(get_test_database_url(), pool_size=2)
        try:
            scope = db.new_scope()
            auth_settings = Settings(auth_required=True)
            use_cases = build_use_cases(settings=auth_settings, clock=SystemClock(), scope=scope)
            tenant_a = uuid.uuid4()
            tenant_b = uuid.uuid4()

            principal_a = make_principal(
                tenant_id=tenant_a,
                permissions=frozenset({"*"}),
            )
            context_a = make_context(principal_a)

            query = GetOrderQuery(
                tenant_id=TenantId(tenant_b),  # mismatched tenant
                context=context_a,
                order_id=OrderId(uuid.uuid4()),
            )

            with pytest.raises(Forbidden):
                await use_cases.get_order(query)
        finally:
            await db.dispose()


# ---------------------------------------------------------------------------
# 2. Idempotency Order & Replay Authorization
# ---------------------------------------------------------------------------


class TestIdempotencyOrderAndReplayAuthorization:
    async def test_idempotency_authorization_evaluated_before_claim(self) -> None:
        """An unauthorized request must be rejected BEFORE any idempotency record is created."""
        db = Database(get_test_database_url(), pool_size=2)
        try:
            scope = db.new_scope()
            auth_settings = Settings(auth_required=True)
            use_cases = build_use_cases(settings=auth_settings, clock=SystemClock(), scope=scope)
            tenant_id = uuid.uuid4()
            idem_key = f"idem-unauth-{uuid.uuid4().hex[:8]}"

            # Principal without orders.create permission
            unauthorized_principal = make_principal(
                tenant_id=tenant_id,
                permissions=frozenset({"orders.read"}),
            )
            context = make_context(unauthorized_principal, idempotency_key=idem_key)

            cmd = CreateOrderCommand(
                tenant_id=TenantId(tenant_id),
                context=context,
                lines=(),
            )

            # Execution fails with Forbidden
            with pytest.raises(Forbidden):
                await use_cases.create_order(cmd)

            # Verify that NO idempotency record was committed
            async with db.new_scope(bypass_rls=True) as check_scope:
                res = await check_scope.session.execute(
                    text("SELECT count(*) FROM idempotency_records WHERE idempotency_key = :k"),
                    {"k": idem_key},
                )
                count = res.scalar_one()
                assert count == 0, "Idempotency record was created despite authorization failure!"
        finally:
            await db.dispose()

    async def test_replay_authorization_denies_unauthorized_replay(self) -> None:
        """Replaying an idempotency key with an unauthorized principal must fail closed."""
        db = Database(get_test_database_url(), pool_size=2)
        try:
            tenant_id = uuid.uuid4()
            idem_key = f"idem-replay-{uuid.uuid4().hex[:8]}"
            cached_order_id = uuid.uuid4()

            # Seed a completed idempotency record and tenant directly
            async with db.new_scope(tenant_id=str(tenant_id)) as seed_scope:
                await seed_scope.session.execute(
                    text(
                        "INSERT INTO tenants ("
                        "  id, slug, name, status, version, created_at, updated_at"
                        ") VALUES (:id, :slug, 'Idem Tenant', 'ACTIVE', 1, now(), now()) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"id": tenant_id, "slug": f"idem-{tenant_id.hex[:6]}"},
                )
                await seed_scope.session.execute(
                    text(
                        "INSERT INTO idempotency_records ("
                        "  id, tenant_id, idempotency_key, operation, request_hash, state, "
                        "  resource_type, resource_id, response_status, created_at, "
                        "  updated_at, expires_at"
                        ") VALUES ("
                        "  :id, :tid, :key, 'CreateOrder', :hash, 'completed', 'order', :rid, "
                        "  201, now(), now(), now() + interval '1 day'"
                        ")"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "key": idem_key,
                        "tid": tenant_id,
                        "hash": "0" * 64,
                        "rid": cached_order_id,
                    },
                )
                await seed_scope.commit()

            # Now an attacker from Tenant B tries to replay Tenant A's idempotency key
            attacker_tenant = uuid.uuid4()
            attacker_principal = make_principal(
                tenant_id=attacker_tenant,
                permissions=frozenset({"orders.create"}),
            )
            attacker_context = make_context(attacker_principal, idempotency_key=idem_key)

            scope = db.new_scope()
            auth_settings = Settings(auth_required=True)
            use_cases = build_use_cases(settings=auth_settings, clock=SystemClock(), scope=scope)

            cmd = CreateOrderCommand(
                tenant_id=TenantId(attacker_tenant),
                context=attacker_context,
                lines=(),
            )

            # Must fail closed: cannot hijack/read Tenant A's cached response
            with pytest.raises(ApplicationError):
                await use_cases.create_order(cmd)
        finally:
            await db.dispose()


# ---------------------------------------------------------------------------
# 3. Message-Driven Authorization (RabbitMQ & Consumer)
# ---------------------------------------------------------------------------


class TestMessageDrivenAuthorization:
    async def test_consumer_rejects_tampered_tenant_header(self) -> None:
        """If message header x-tenant-id does not match body tenant_id, message is sent to DLQ."""
        from tests.integration.test_rabbitmq_retry import FakeIncomingMessage

        from shared.infrastructure.messaging.consumer import IdempotentConsumer

        db = Database(get_test_database_url(), pool_size=2)
        try:
            consumer = IdempotentConsumer(db, "security_test_consumer")
            handler_called = False

            async def dummy_handler(payload: dict[str, Any], ctx: RequestContext) -> None:
                nonlocal handler_called
                handler_called = True

            consumer.register_handler("SecurityEvent", dummy_handler)

            real_tenant = str(uuid.uuid4())
            forged_header_tenant = str(uuid.uuid4())
            event_id = str(uuid.uuid4())

            body_dict = {
                "event_id": event_id,
                "event_type": "SecurityEvent",
                "tenant_id": real_tenant,
                "schema_version": 1,
                "payload": {"data": 123},
            }

            fake_msg = FakeIncomingMessage(
                body=json.dumps(body_dict).encode("utf-8"),
                message_id=event_id,
                headers={"x-tenant-id": forged_header_tenant},  # TAMPERED HEADER!
            )

            # Process the message
            await consumer.process_message(fake_msg)

            # Handler must NEVER have been called
            assert not handler_called, "Handler was executed despite tenant header tampering!"
            assert fake_msg.acked, "Tampered message was not acknowledged after DLQ routing!"
        finally:
            await db.dispose()

    async def test_consumer_propagates_correlation_causation_and_tenant(self) -> None:
        """Legitimate message execution receives correct tenant and lineage metadata."""
        from tests.integration.test_rabbitmq_retry import FakeIncomingMessage

        from shared.infrastructure.messaging.consumer import IdempotentConsumer

        db = Database(get_test_database_url(), pool_size=2)
        try:
            consumer = IdempotentConsumer(db, "lineage_test_consumer")
            captured_context: RequestContext | None = None

            async def capture_handler(payload: dict[str, Any], ctx: RequestContext) -> None:
                nonlocal captured_context
                captured_context = ctx

            consumer.register_handler("LineageEvent", capture_handler)

            tenant_id = str(uuid.uuid4())
            event_id = str(uuid.uuid4())
            corr_id = f"corr-{uuid.uuid4().hex[:8]}"
            caus_id = f"caus-{uuid.uuid4().hex[:8]}"

            body_dict = {
                "event_id": event_id,
                "event_type": "LineageEvent",
                "tenant_id": tenant_id,
                "schema_version": 1,
                "payload": {"status": "OK"},
            }

            fake_msg = FakeIncomingMessage(
                body=json.dumps(body_dict).encode("utf-8"),
                message_id=event_id,
                headers={
                    "x-tenant-id": tenant_id,
                    "x-correlation-id": corr_id,
                    "x-causation-id": caus_id,
                    "x-actor-id": "upstream-service",
                    "x-actor-type": "SYSTEM",
                },
            )

            # Seed tenant in DB so foreign keys/leases are valid
            async with db.new_scope(bypass_rls=True) as seed_scope:
                await seed_scope.session.execute(
                    text(
                        "INSERT INTO tenants ("
                        "  id, slug, name, status, version, created_at, updated_at"
                        ") VALUES (:id, :slug, 'Lineage Tenant', 'ACTIVE', 1, now(), now()) "
                        "ON CONFLICT DO NOTHING"
                    ),
                    {"id": uuid.UUID(tenant_id), "slug": f"lin-{tenant_id[:8]}"},
                )
                await seed_scope.commit()

            await consumer.process_message(fake_msg)

            assert captured_context is not None
            assert str(captured_context.tenant_id) == tenant_id
            assert captured_context.correlation_id == corr_id
            assert captured_context.causation_id == caus_id
            assert captured_context.actor_id == "upstream-service"
            assert captured_context.actor_type == ActorType.SYSTEM
            assert captured_context.principal is not None
            assert str(captured_context.principal.tenant_id) == tenant_id
        finally:
            await db.dispose()


# ---------------------------------------------------------------------------
# 4. Celery / Background Worker Authorization
# ---------------------------------------------------------------------------


class TestBackgroundWorkerAuthorization:
    async def test_worker_run_expire_reservations_has_system_principal(self) -> None:
        """The reservation-sweeper worker runs with trusted system principal
        and inventory.expire permission.
        """
        from bootstrap.workers import _run_expire_reservations

        expired_count = await _run_expire_reservations()
        assert isinstance(expired_count, int)

    async def test_expire_reservations_denies_ordinary_member(self) -> None:
        """Direct invocation of ExpireReservations by a normal user without
        inventory.expire fails.
        """
        db = Database(get_test_database_url(), pool_size=2)
        try:
            scope = db.new_scope()
            auth_settings = Settings(auth_required=True)
            use_cases = build_use_cases(settings=auth_settings, clock=SystemClock(), scope=scope)

            user_principal = make_principal(
                tenant_id=uuid.uuid4(),
                permissions=frozenset({"inventory.read"}),  # lacks inventory.expire
            )
            context = make_context(user_principal)

            cmd = ExpireReservationsCommand(
                context=context,
                limit=50,
            )

            with pytest.raises(Forbidden):
                await use_cases.expire_reservations(cmd)
        finally:
            await db.dispose()


# ---------------------------------------------------------------------------
# 5. Search Reindex Tenant Isolation & AUTH-001 Regression Protection
# ---------------------------------------------------------------------------


class TestSearchReindexSecurityIntegration:
    async def test_forged_headers_rejected_401(self, client: httpx.AsyncClient) -> None:
        """AUTH-001 regression: header-based actor spoofing without valid JWT returns 401."""
        resp = await client.post(
            "/v1/search/reindex",
            headers={"X-Actor-Type": "system", "X-Role": "ADMIN"},
            json={},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "authentication_failed"

    async def test_tenant_admin_cannot_trigger_cross_tenant_reindex(
        self, client: httpx.AsyncClient
    ) -> None:
        """Tenant Admin cannot trigger reindex for another tenant (must fail with 403 Forbidden)."""
        _, admin_token_a, *_ = await setup_tenant_and_users(client)
        tenant_b = str(uuid.uuid4())

        headers_a = {"Authorization": f"Bearer {admin_token_a}"}

        # Attempt to reindex Tenant B
        resp = await client.post(
            "/v1/search/reindex",
            headers=headers_a,
            json={"tenant_id": tenant_b},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"

    async def test_tenant_admin_cannot_trigger_platform_wide_reindex(
        self, client: httpx.AsyncClient
    ) -> None:
        """Tenant Admin cannot trigger platform-wide reindex without tenant_id
        (must fail with 403).
        """
        _, admin_token_a, _, _, _, _ = await setup_tenant_and_users(client)

        headers_a = {"Authorization": f"Bearer {admin_token_a}"}

        # Omit tenant_id -> attempts platform-wide reindex
        resp = await client.post(
            "/v1/search/reindex",
            headers=headers_a,
            json={},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "forbidden"

    async def test_tenant_admin_can_reindex_own_tenant(
        self, client: httpx.AsyncClient
    ) -> None:
        """Tenant Admin specifying their own tenant ID can successfully trigger reindex."""
        tenant_a, admin_token_a, _, _, _, _ = await setup_tenant_and_users(client)

        headers_a = {"Authorization": f"Bearer {admin_token_a}"}

        resp = await client.post(
            "/v1/search/reindex",
            headers=headers_a,
            json={"tenant_id": tenant_a},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "COMPLETED"

    async def test_platform_admin_can_reindex(self, client: httpx.AsyncClient) -> None:
        """System administrator with wildcard permission can trigger platform reindex."""
        settings = Settings()
        token_service = TokenService(
            secret=settings.auth_jwt_secret,
            algorithm=settings.auth_jwt_algorithm,
            issuer=settings.auth_jwt_issuer,
            audience=settings.auth_jwt_audience,
        )

        token_sys = token_service.create_access_token(
            {
                "sub": str(uuid.uuid4()),
                "type": "system",
                "role": "ADMIN",
                "permissions": ["*"],
            }
        )

        headers = {"Authorization": f"Bearer {token_sys}"}
        resp = await client.post(
            "/v1/search/reindex",
            headers=headers,
            json={},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "COMPLETED"


# ---------------------------------------------------------------------------
# 6. Service Account Scope Boundary Enforcement
# ---------------------------------------------------------------------------


class TestServiceAccountScopeEnforcement:
    async def test_service_account_denied_administrative_reindex(
        self, client: httpx.AsyncClient
    ) -> None:
        """Service Account with catalog.products.read cannot invoke
        administrative search reindex.
        """
        db = Database(get_test_database_url(), pool_size=2)
        try:
            scope = db.new_scope(bypass_rls=True)
            use_cases = build_use_cases(settings=get_settings(), clock=SystemClock(), scope=scope)

            tenant_id = uuid.uuid4()
            sys_ctx = RequestContext(
                tenant_id=None,
                actor_id="system",
                actor_type=ActorType.SYSTEM,
                correlation_id=str(uuid.uuid4()),
                received_at=datetime.now(UTC),
            )

            # Provision tenant & SA
            tenant_res = await use_cases.register_tenant(
                RegisterTenantCommand(
                    context=sys_ctx,
                    slug=f"sa-t-{uuid.uuid4().hex[:6]}",
                    name="SA Tenant",
                )
            )
            tenant_id = tenant_res.view.id
            sa_res = await use_cases.create_service_account(
                CreateServiceAccountCommand(
                    context=sys_ctx,
                    tenant_id=tenant_id,
                    name="Reader SA",
                    client_id=f"sa-read-{uuid.uuid4().hex[:6]}",
                    scopes=("catalog.products.read",),
                )
            )
            auth_res = await use_cases.authenticate_service_account(
                AuthenticateServiceAccountCommand(
                    context=sys_ctx,
                    client_id=sa_res.view.client_id,
                    client_secret=sa_res.view.client_secret or "",
                )
            )
            sa_token = auth_res.view.access_token

            headers = {"Authorization": f"Bearer {sa_token}"}
            resp = await client.post(
                "/v1/search/reindex",
                headers=headers,
                json={"tenant_id": str(tenant_id)},
            )
            assert resp.status_code == 403
            assert resp.json()["error"]["code"] == "forbidden"
        finally:
            await db.dispose()

    async def test_service_account_denied_cross_tenant_access(
        self, client: httpx.AsyncClient
    ) -> None:
        """Service Account assigned to Tenant A cannot access resources in Tenant B."""
        db = Database(get_test_database_url(), pool_size=2)
        try:
            scope = db.new_scope(bypass_rls=True)
            use_cases = build_use_cases(settings=get_settings(), clock=SystemClock(), scope=scope)

            tenant_b = uuid.uuid4()
            sys_ctx = RequestContext(
                tenant_id=None,
                actor_id="system",
                actor_type=ActorType.SYSTEM,
                correlation_id=str(uuid.uuid4()),
                received_at=datetime.now(UTC),
            )

            tenant_res_a = await use_cases.register_tenant(
                RegisterTenantCommand(
                    context=sys_ctx,
                    slug=f"sa-a-{uuid.uuid4().hex[:6]}",
                    name="SA Tenant A",
                )
            )
            tenant_a = tenant_res_a.view.id
            sa_res = await use_cases.create_service_account(
                CreateServiceAccountCommand(
                    context=sys_ctx,
                    tenant_id=tenant_a,
                    name="Orders SA",
                    client_id=f"sa-orders-{uuid.uuid4().hex[:6]}",
                    scopes=("orders.read",),
                )
            )
            auth_res = await use_cases.authenticate_service_account(
                AuthenticateServiceAccountCommand(
                    context=sys_ctx,
                    client_id=sa_res.view.client_id,
                    client_secret=sa_res.view.client_secret or "",
                )
            )
            sa_token = auth_res.view.access_token

            # Attempt to read Tenant B's order list
            headers = {
                "Authorization": f"Bearer {sa_token}",
                "X-Tenant-Id": str(tenant_b),  # mismatched header!
            }
            resp = await client.get("/v1/orders", headers=headers)
            assert resp.status_code in (401, 403)
        finally:
            await db.dispose()


# ---------------------------------------------------------------------------
# 7. ABAC Decision Traceability & Audit Integration
# ---------------------------------------------------------------------------


class TestABACDecisionTraceabilityAndAudit:
    async def test_abac_decision_preserves_correlation_and_causation(self) -> None:
        """Authorization decisions capture correlation_id and causation_id."""
        from identity.domain.decision import AuthorizationRequest

        db = Database(get_test_database_url(), pool_size=2)
        try:
            scope = db.new_scope()
            auth_settings = Settings(auth_required=True)
            use_cases = build_use_cases(
                settings=auth_settings, clock=SystemClock(), scope=scope
            )
            authorizer = use_cases.create_order._authorizer
            assert authorizer is not None
            tenant_id = uuid.uuid4()
            corr_id = f"corr-{uuid.uuid4().hex[:8]}"
            caus_id = f"caus-{uuid.uuid4().hex[:8]}"

            principal = make_principal(
                tenant_id=tenant_id,
                permissions=frozenset({"orders.read"}),
            )

            # 1. Allowed decision
            req_allow = AuthorizationRequest(
                action="orders.read",
                resource_type="orders",
                principal_id=principal.id,
                principal_type=principal.type,
                tenant_id=principal.active_tenant_id,
                roles=principal.roles,
                permissions=principal.permissions,
                resource_tenant_id=TenantId(tenant_id),
                environment={"correlation_id": corr_id, "causation_id": caus_id},
            )
            decision = await authorizer.authorize(req_allow)
            assert decision.allowed
            assert decision.correlation_id == corr_id
            assert decision.causation_id == caus_id

            # 2. Denied decision
            req_deny = AuthorizationRequest(
                action="orders.confirm",
                resource_type="orders",
                principal_id=principal.id,
                principal_type=principal.type,
                tenant_id=principal.active_tenant_id,
                roles=principal.roles,
                permissions=principal.permissions,
                resource_tenant_id=TenantId(tenant_id),
                environment={"correlation_id": corr_id, "causation_id": caus_id},
            )
            decision_denied = await authorizer.authorize(req_deny)
            assert not decision_denied.allowed
            assert decision_denied.correlation_id == corr_id
            assert decision_denied.causation_id == caus_id
        finally:
            await db.dispose()

    async def test_audit_logs_do_not_contain_secrets(self) -> None:
        """Verify that audit records in audit_logs table never contain raw secrets or tokens."""
        db = Database(get_test_database_url(), pool_size=2)
        try:
            async with db.new_scope(bypass_rls=True) as scope:
                res = await scope.session.execute(
                    text(
                        "SELECT action, details FROM audit_logs "
                        "ORDER BY occurred_at DESC LIMIT 50"
                    )
                )
                rows = res.fetchall()
                for row in rows:
                    details_str = json.dumps(row.details or {})
                    d_lower = details_str.lower()
                    assert (
                        "password" not in d_lower
                        or "******" in details_str
                        or "[redacted]" in d_lower
                    )
                    assert (
                        "secret" not in d_lower
                        or "******" in details_str
                        or "[redacted]" in d_lower
                    )
                    assert "eyJh" not in details_str  # standard JWT header prefix
        finally:
            await db.dispose()


# ---------------------------------------------------------------------------
# 8. PostgreSQL RLS SQL Reader Consistency
# ---------------------------------------------------------------------------


class TestSqlAlchemyCatalogSearchReaderRLSConsistency:
    async def test_search_reader_respects_tenant_isolation(self) -> None:
        """SqlAlchemyCatalogSearchReader with tenant_id only queries that tenant's records."""
        db = Database(get_test_database_url(), pool_size=2)
        try:
            reader = SqlAlchemyCatalogSearchReader(db)
            tenant_a = str(uuid.uuid4())
            tenant_b = str(uuid.uuid4())
            prod_a_id = str(uuid.uuid4())
            prod_b_id = str(uuid.uuid4())

            # Seed products for Tenant A and Tenant B
            # Seed tenants (un-scoped)
            async with db.new_scope() as seed_scope:
                await seed_scope.session.execute(
                    text(
                        "INSERT INTO tenants ("
                        "  id, slug, name, status, version, created_at, updated_at"
                        ") VALUES (:id, :slug, :name, 'ACTIVE', 1, now(), now())"
                    ),
                    [
                        {
                            "id": uuid.UUID(tenant_a),
                            "slug": f"t-a-{tenant_a[:6]}",
                            "name": "Tenant A",
                        },
                        {
                            "id": uuid.UUID(tenant_b),
                            "slug": f"t-b-{tenant_b[:6]}",
                            "name": "Tenant B",
                        },
                    ],
                )
                await seed_scope.commit()

            # Seed Tenant A product under Tenant A scope
            async with db.new_scope(tenant_id=tenant_a) as scope_a:
                await scope_a.session.execute(
                    text(
                        "INSERT INTO products ("
                        "  id, tenant_id, slug, name, description, status, attributes, "
                        "  version, created_at, updated_at"
                        ") VALUES ("
                        "  :id, :tenant_id, 'prod-a', 'Product A', '', 'ACTIVE', '{}', 1, now(), now()"
                        ")"
                    ),
                    {"id": uuid.UUID(prod_a_id), "tenant_id": uuid.UUID(tenant_a)},
                )
                await scope_a.commit()

            # Seed Tenant B product under Tenant B scope
            async with db.new_scope(tenant_id=tenant_b) as scope_b:
                await scope_b.session.execute(
                    text(
                        "INSERT INTO products ("
                        "  id, tenant_id, slug, name, description, status, attributes, "
                        "  version, created_at, updated_at"
                        ") VALUES ("
                        "  :id, :tenant_id, 'prod-b', 'Product B', '', 'ACTIVE', '{}', 1, now(), now()"
                        ")"
                    ),
                    {"id": uuid.UUID(prod_b_id), "tenant_id": uuid.UUID(tenant_b)},
                )
                await scope_b.commit()

            # 1. Fetch document under Tenant A
            doc_a = await reader.get_search_document(tenant_id=tenant_a, product_id=prod_a_id)
            assert doc_a is not None
            assert doc_a.document_id == f"{tenant_a}:{prod_a_id}"

            # 2. Attempt to fetch Tenant B's product while scoped to Tenant A -> MUST be None!
            doc_cross = await reader.get_search_document(tenant_id=tenant_a, product_id=prod_b_id)
            assert doc_cross is None, "Cross-tenant product was returned by search reader!"
        finally:
            await db.dispose()
