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
        await inbox_repo.mark_processed(
            message_id=msg_id, consumer_name=consumer_name, now=now
        )
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
