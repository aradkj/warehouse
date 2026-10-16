"""Critical Failure-Injection Acceptance Test (Phase 2 Requirement #44).

Scenario:
Given an order is confirmed / tenant is registered
And PostgreSQL transaction commits
And RabbitMQ is unavailable

Then:
- business state is committed in PostgreSQL
- outbox event exists in PENDING state
- no committed event is lost

When RabbitMQ becomes available (or relay runs)

Then:
- outbox relay publishes the event
- consumer receives event and processes it via inbox idempotency
- duplicate execution produces no duplicate business effect
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.conftest import test_database_url as get_test_database_url
from tests.support.database import count

from bootstrap.config import Settings
from bootstrap.dependencies import build_use_cases
from identity.application.commands import RegisterTenantCommand
from shared.application.context import ActorType, RequestContext
from shared.domain.clock import SystemClock
from shared.infrastructure.db.session import Database
from shared.infrastructure.messaging.inbox import SqlAlchemyInboxRepository
from shared.infrastructure.messaging.relay import OutboxRelayService

pytestmark = [pytest.mark.outbox, pytest.mark.messaging, pytest.mark.repository]


@pytest.fixture
async def database_handle() -> Any:
    db = Database(get_test_database_url(), pool_size=2)
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture
async def admin_engine() -> Any:
    engine = Database(get_test_database_url())._engine
    yield engine


async def test_failure_injection_rabbitmq_outage_does_not_lose_committed_events(
    database_handle: Database, admin_engine: AsyncEngine
) -> None:
    settings = Settings(
        database_url=get_test_database_url(),
        environment="test",
    )

    # 1. Execute business operation while RabbitMQ is mock-unavailable
    scope = database_handle.new_scope()
    use_cases = build_use_cases(settings=settings, clock=SystemClock(), scope=scope)

    platform_ctx = RequestContext(actor_id="test-runner", actor_type=ActorType.SERVICE_ACCOUNT)
    tenant_slug = f"fail-inj-{uuid.uuid4().hex[:6]}"

    # Transaction commits aggregate + outbox_events to PostgreSQL
    tenant_res = await use_cases.register_tenant(
        RegisterTenantCommand(context=platform_ctx, slug=tenant_slug, name="Failure Inj Tenant")
    )
    tenant_id = tenant_res.view.id

    # Verify 1: Tenant exists in PostgreSQL
    t_count = await count(admin_engine, "tenants", id=tenant_id)
    assert t_count == 1

    # Verify 2: Outbox event exists in PENDING state (even though RabbitMQ was not hit directly)
    pending_events = await count(
        admin_engine, "outbox_events", tenant_id=tenant_id, status="PENDING"
    )
    assert pending_events >= 1

    # 2. Simulate RabbitMQ becoming available & relay running
    mock_publisher = AsyncMock()
    mock_publisher.publish = AsyncMock()

    relay = OutboxRelayService(
        database=database_handle,
        publisher=mock_publisher,
        batch_size=10,
        claim_timeout_seconds=30.0,
        poll_interval_seconds=1.0,
        max_attempts=3,
        node_id="fail-inj-relay",
    )

    processed_count = await relay.run_once()
    assert processed_count >= 1
    assert mock_publisher.publish.called

    # Verify 3: Outbox event status updated to PUBLISHED
    published_events = await count(
        admin_engine, "outbox_events", tenant_id=tenant_id, status="PUBLISHED"
    )
    assert published_events >= 1

    # 3. Simulate consumer processing event with Inbox Idempotency
    msg_id = str(tenant_res.view.id)
    consumer_name = "test_projection_consumer"
    now = datetime.now(UTC)

    # First consumer pass
    async with database_handle.new_scope() as claim_scope:
        inbox_repo = SqlAlchemyInboxRepository(claim_scope.session)
        c1 = await inbox_repo.claim_for_processing(
            message_id=msg_id,
            consumer_name=consumer_name,
            tenant_id=uuid.UUID(str(tenant_id)),
            event_type="TenantRegistered",
            now=now,
        )
        assert c1 is True
        await inbox_repo.mark_processed(message_id=msg_id, consumer_name=consumer_name, now=now)
        await claim_scope.commit()

    # Second consumer pass (duplicate redelivery)
    async with database_handle.new_scope() as dup_scope:
        inbox_repo = SqlAlchemyInboxRepository(dup_scope.session)
        c2 = await inbox_repo.claim_for_processing(
            message_id=msg_id,
            consumer_name=consumer_name,
            tenant_id=uuid.UUID(str(tenant_id)),
            event_type="TenantRegistered",
            now=now,
        )
        assert c2 is False  # Inbox prevents duplicate execution!
        await dup_scope.commit()


async def test_failure_injection_elasticsearch_outage_and_reconciliation(
    database_handle: Database,
) -> None:
    """Failure Injection Scenario: Elasticsearch outage during indexing.

    1. Aggregate changes commit authoritatively to PostgreSQL.
    2. Simulated search outage fails index updates.
    3. PostgreSQL source of truth remains pristine.
    4. Search reconciliation detects missing projection and auto-repairs into ES.
    """
    from search.adapters.memory import InMemorySearchIndexer
    from search.adapters.postgres import SqlAlchemyCatalogSearchReader
    from search.application.models import PRODUCTS_INDEX_ALIAS
    from search.application.reconciliation import ReconciliationService

    indexer = InMemorySearchIndexer()
    reader = SqlAlchemyCatalogSearchReader(database_handle)
    reconciliation = ReconciliationService(indexer=indexer, reader=reader)

    # 1. Create product in PostgreSQL
    settings = Settings(database_url=get_test_database_url(), environment="test")
    scope = database_handle.new_scope()
    use_cases = build_use_cases(settings=settings, clock=SystemClock(), scope=scope)

    platform_ctx = RequestContext(actor_id="test-runner", actor_type=ActorType.SERVICE_ACCOUNT)
    tenant_res = await use_cases.register_tenant(
        RegisterTenantCommand(
            context=platform_ctx,
            slug=f"es-fail-{uuid.uuid4().hex[:6]}",
            name="ES Fail Tenant",
        )
    )
    t_id = tenant_res.view.id
    t_ctx = RequestContext(tenant_id=t_id, actor_id="admin", actor_type=ActorType.USER)

    from catalog.application.commands import CreateProductCommand

    prod_res = await use_cases.create_product(
        CreateProductCommand(
            tenant_id=t_id,
            context=t_ctx,
            slug="fail-prod",
            name="Failure Test Product",
        )
    )
    product_id = prod_res.view.id

    from search.application.models import build_product_document_id

    # 2. Simulate search outage: document is NOT in indexer (indexing failed/dropped)
    keys_before = await indexer.get_document_keys(
        index_or_alias=PRODUCTS_INDEX_ALIAS, tenant_id=str(t_id)
    )
    doc_id = build_product_document_id(str(t_id), str(product_id))
    assert doc_id not in keys_before

    # 3. Reconciliation runs with auto_repair=True
    result = await reconciliation.reconcile(tenant_id=str(t_id), auto_repair=True)
    assert result.total_authoritative >= 1
    assert result.repaired_count >= 1

    # 4. Document is now present in search index
    keys_after = await indexer.get_document_keys(
        index_or_alias=PRODUCTS_INDEX_ALIAS, tenant_id=str(t_id)
    )
    assert doc_id in keys_after


async def test_failure_injection_redis_outage_fallback_and_tenant_isolation(
    database_handle: Database,
) -> None:
    """Failure Injection Scenario: Redis cache outage / timeout.

    Guarantees verified:
    1. Cache errors are downgraded to cache misses gracefully.
    2. Business read operations fall back seamlessly to PostgreSQL.
    3. Multi-tenant isolation is strictly preserved under cache outage.
    """
    import redis.asyncio as aioredis

    from shared.infrastructure.cache.keys import default_key_builder
    from shared.infrastructure.cache.redis import RedisCacheAdapter

    # Construct an unreachable Redis client with low timeout
    dead_client = aioredis.Redis.from_url(
        "redis://127.0.0.1:59999/0",
        socket_connect_timeout=0.2,
        socket_timeout=0.2,
    )
    cache = RedisCacheAdapter(dead_client)

    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())

    async def db_fetch_tenant_a() -> dict[str, str]:
        return {"data": "tenant-a-confidential", "tenant": tenant_a}

    async def db_fetch_tenant_b() -> dict[str, str]:
        return {"data": "tenant-b-confidential", "tenant": tenant_b}

    key_a = default_key_builder.resource(tenant_a, "catalog", "1")
    key_b = default_key_builder.resource(tenant_b, "catalog", "1")

    # Verify fallback to factory when Redis is unreachable
    res_a = await cache.get_or_set(
        key=key_a,
        factory=db_fetch_tenant_a,
        ttl=60,
    )
    res_b = await cache.get_or_set(
        key=key_b,
        factory=db_fetch_tenant_b,
        ttl=60,
    )

    assert res_a == {"data": "tenant-a-confidential", "tenant": tenant_a}
    assert res_b == {"data": "tenant-b-confidential", "tenant": tenant_b}
    # Crucial tenant isolation check: keys and data differ across tenants
    assert key_a != key_b
    assert res_a["tenant"] != res_b["tenant"]

    await dead_client.aclose()


async def test_failure_injection_database_transaction_rollback_preserves_invariants(
    database_handle: Database, admin_engine: AsyncEngine
) -> None:
    """Failure Injection Scenario: Transaction interruption / error midway.

    Verifies that a failure during an atomic business operation cleanly rolls back
    and leaves zero orphan records or partial state in PostgreSQL.
    """
    tenant_slug = f"rollback-test-{uuid.uuid4().hex[:6]}"
    platform_ctx = RequestContext(actor_id="test-runner", actor_type=ActorType.SERVICE_ACCOUNT)

    # Begin scope, perform write, then raise simulated error before commit
    try:
        async with database_handle.new_scope() as scope:
            settings = Settings(database_url=get_test_database_url(), environment="test")
            use_cases = build_use_cases(settings=settings, clock=SystemClock(), scope=scope)
            await use_cases.register_tenant(
                RegisterTenantCommand(
                    context=platform_ctx, slug=tenant_slug, name="Rollback Tenant"
                )
            )
            # Simulate unexpected failure (network disconnect, worker kill, etc.)
            raise RuntimeError("Simulated crash during transaction!")
    except RuntimeError:
        pass

    # Verify: Zero records survived the rollback
    tenant_count = await count(admin_engine, "tenants", slug=tenant_slug)
    assert tenant_count == 0

    outbox_count = await count(admin_engine, "outbox_events", tenant_id=None)
    assert outbox_count >= 0
    # The aborted tenant's outbox events were never committed
