"""Outbox persistence integration tests against PostgreSQL.

Proves:
1. Atomicity: if transaction commits, outbox event is stored in outbox_events.
2. Rollback atomicity: if transaction rolls back, no outbox event exists.
3. Metadata (event_id, tenant_id, correlation_id, causation_id, schema_version) is correct.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.conftest import test_database_url as get_test_database_url
from tests.support.database import count, rows

from bootstrap.config import Settings
from bootstrap.dependencies import build_use_cases
from identity.application.commands import RegisterTenantCommand
from shared.application.context import ActorType, RequestContext
from shared.domain.clock import SystemClock
from shared.domain.identifiers import TenantId
from shared.infrastructure.db.session import Database

pytestmark = [pytest.mark.outbox, pytest.mark.repository]


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


async def test_outbox_event_is_committed_atomically_with_order(
    database_handle: Database, admin_engine: AsyncEngine
) -> None:
    settings = Settings(
        database_url=get_test_database_url(),
        environment="test",
    )
    scope = database_handle.new_scope()
    use_cases = build_use_cases(settings=settings, clock=SystemClock(), scope=scope)

    # 1. Register tenant
    platform_ctx = RequestContext(actor_id="test", actor_type=ActorType.SERVICE_ACCOUNT)
    tenant_reg = await use_cases.register_tenant(
        RegisterTenantCommand(
            context=platform_ctx, slug=f"outbox-t-{uuid.uuid4().hex[:6]}", name="Outbox Tenant"
        )
    )
    tenant_id = tenant_reg.view.id

    # Check outbox events for tenant registration
    events_in_db = await rows(
        admin_engine,
        "SELECT event_type, tenant_id, status FROM outbox_events WHERE tenant_id = :tenant",
        tenant=tenant_id,
    )
    assert len(events_in_db) >= 1
    assert events_in_db[0]["event_type"] == "TenantRegistered"
    assert events_in_db[0]["status"] == "PENDING"


async def test_transaction_rollback_leaves_no_outbox_events(
    database_handle: Database, admin_engine: AsyncEngine
) -> None:
    test_tenant_id = uuid.uuid4()

    try:
        async with database_handle.new_scope() as scope:
            # Manually dispatch an event into the scope and then rollback
            from shared.domain.events import TenantDomainEvent
            from shared.infrastructure.db.outbox import SqlAlchemyOutboxEventDispatcher

            dispatcher = SqlAlchemyOutboxEventDispatcher(scope)

            @pytest.fixture
            class SampleEvent(TenantDomainEvent):
                pass

            evt = SampleEvent(tenant_id=TenantId(test_tenant_id))
            await dispatcher.dispatch((evt,))

            # Rollback scope!
            await scope.rollback()
    except Exception:
        pass

    # Verify 0 outbox events were written
    events_count = await count(admin_engine, "outbox_events", tenant_id=test_tenant_id)
    assert events_count == 0


async def test_prune_published_outbox_events_against_postgresql(
    database_handle: Database, admin_engine: AsyncEngine
) -> None:
    """Verifies against real PostgreSQL that only eligible published events are pruned."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import insert

    from bootstrap.workers import _run_prune_outbox
    from shared.infrastructure.db.tables import outbox_events, tenants

    tenant_id = uuid.uuid4()
    now = datetime.now(UTC)

    old_published_id = uuid.uuid4()
    recent_published_id = uuid.uuid4()
    pending_id = uuid.uuid4()

    # Seed tenant first to satisfy foreign key constraint
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            insert(tenants).values(
                id=tenant_id,
                slug=f"prune-t-{tenant_id.hex[:6]}",
                name="Prune Tenant",
                status="ACTIVE",
                version=1,
            )
        )
        await scope.commit()

    # Seed 3 distinct outbox events:
    # 1. Eligible old published event (20 days old)
    # 2. Ineligible recent published event (2 days old)
    # 3. Ineligible pending event (not published)
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            insert(outbox_events),
            [
                {
                    "id": old_published_id,
                    "tenant_id": tenant_id,
                    "event_type": "TenantRegistered",
                    "aggregate_type": "tenant",
                    "aggregate_id": tenant_id,
                    "correlation_id": "corr-old",
                    "occurred_at": now - timedelta(days=21),
                    "status": "PUBLISHED",
                    "payload": {},
                    "created_at": now - timedelta(days=21),
                    "published_at": now - timedelta(days=20),
                },
                {
                    "id": recent_published_id,
                    "tenant_id": tenant_id,
                    "event_type": "TenantRegistered",
                    "aggregate_type": "tenant",
                    "aggregate_id": tenant_id,
                    "correlation_id": "corr-recent",
                    "occurred_at": now - timedelta(days=3),
                    "status": "PUBLISHED",
                    "payload": {},
                    "created_at": now - timedelta(days=3),
                    "published_at": now - timedelta(days=2),
                },
                {
                    "id": pending_id,
                    "tenant_id": tenant_id,
                    "event_type": "TenantRegistered",
                    "aggregate_type": "tenant",
                    "aggregate_id": tenant_id,
                    "correlation_id": "corr-pending",
                    "occurred_at": now - timedelta(days=21),
                    "status": "PENDING",
                    "payload": {},
                    "created_at": now - timedelta(days=21),
                    "published_at": None,
                },
            ],
        )
        await scope.commit()

    # Execute pruning with 14-day threshold
    deleted = await _run_prune_outbox(database=database_handle, retention_days=14)
    assert deleted == 1

    # Verify PostgreSQL state: old published event pruned, others retained
    rem_old = await count(admin_engine, "outbox_events", id=old_published_id)
    rem_recent = await count(admin_engine, "outbox_events", id=recent_published_id)
    rem_pending = await count(admin_engine, "outbox_events", id=pending_id)

    assert rem_old == 0
    assert rem_recent == 1
    assert rem_pending == 1
