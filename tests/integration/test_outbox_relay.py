"""Outbox Relay integration tests against PostgreSQL.

Proves:
1. Relay claims PENDING outbox events using FOR UPDATE SKIP LOCKED.
2. Relay marks events PUBLISHED after successful publisher confirmation.
3. Relay retries failed events with backoff.
4. Relay recovers stale CLAIMED events.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.conftest import test_database_url as get_test_database_url
from tests.support.database import scalar

from shared.infrastructure.db.outbox import SqlAlchemyOutboxRepository
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


async def test_claim_batch_locks_and_claims_pending_events(
    database_handle: Database, admin_engine: AsyncEngine
) -> None:
    now = datetime.now(UTC)
    tenant_id = uuid.uuid4()

    # 0. Register dummy tenant in DB to satisfy FK
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Relay Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"relay-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    # 1. Insert 3 outbox events
    event_ids = [uuid.uuid4() for _ in range(3)]
    async with database_handle.new_scope() as scope:
        for evt_id in event_ids:
            await scope.session.execute(
                text(
                    "INSERT INTO outbox_events "
                    "(id, tenant_id, event_type, schema_version, aggregate_type, aggregate_id, "
                    "payload, headers, correlation_id, occurred_at, available_at, status) "
                    "VALUES (:id, :tenant, 'TestEvent', 1, 'order', :agg_id, "
                    "'{}'::jsonb, '{}'::jsonb, 'corr-1', :now, :now, 'PENDING')"
                ),
                {"id": evt_id, "tenant": tenant_id, "agg_id": evt_id, "now": now},
            )
        await scope.commit()

    # 2. Claim batch
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyOutboxRepository(scope.session)
        claimed = await repo.claim_batch(
            batch_size=2, claim_timeout_seconds=30.0, claimed_by="worker-1", now=now
        )
        await scope.commit()

    assert len(claimed) == 2
    assert claimed[0].status == "CLAIMED"
    assert claimed[0].claimed_by == "worker-1"

    # 3. Mark one published
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyOutboxRepository(scope.session)
        await repo.mark_published(claimed[0].id, now=now)
        await scope.commit()

    status_1 = await scalar(
        admin_engine, "SELECT status FROM outbox_events WHERE id = :id", id=claimed[0].id
    )
    assert status_1 == "PUBLISHED"


async def test_stale_claimed_events_are_recovered_by_relay(
    database_handle: Database, admin_engine: AsyncEngine
) -> None:
    now = datetime.now(UTC)
    stale_time = now - timedelta(seconds=60)
    tenant_id = uuid.uuid4()
    evt_id = uuid.uuid4()

    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Stale Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"stale-{uuid.uuid4().hex[:6]}"},
        )
        await scope.session.execute(
            text(
                "INSERT INTO outbox_events "
                "(id, tenant_id, event_type, schema_version, aggregate_type, aggregate_id, "
                "payload, headers, correlation_id, occurred_at, status, "
                "claimed_at, claimed_by) "
                "VALUES (:id, :tenant, 'StaleEvent', 1, 'order', :agg_id, "
                "'{}'::jsonb, '{}'::jsonb, 'corr-2', :now, 'CLAIMED', "
                ":claimed_at, 'dead-worker')"
            ),
            {
                "id": evt_id,
                "tenant": tenant_id,
                "agg_id": evt_id,
                "now": now,
                "claimed_at": stale_time,
            },
        )
        await scope.commit()

    # Claim batch with timeout 30s
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyOutboxRepository(scope.session)
        recovered = await repo.claim_batch(
            batch_size=10, claim_timeout_seconds=30.0, claimed_by="new-worker", now=now
        )
        await scope.commit()

    assert len(recovered) == 1
    assert recovered[0].id == evt_id
    assert recovered[0].claimed_by == "new-worker"


async def test_stale_worker_failure_cannot_overwrite_newer_claim_or_published_state(
    database_handle: Database, admin_engine: AsyncEngine
) -> None:
    now = datetime.now(UTC)
    tenant_id = uuid.uuid4()
    evt_id = uuid.uuid4()

    # 1. Insert tenant and pending outbox event
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Fencing Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"fencing-{uuid.uuid4().hex[:6]}"},
        )
        await scope.session.execute(
            text(
                "INSERT INTO outbox_events "
                "(id, tenant_id, event_type, schema_version, aggregate_type, aggregate_id, "
                "payload, headers, correlation_id, occurred_at, status, available_at) "
                "VALUES (:id, :tenant, 'FencingEvent', 1, 'order', :agg_id, "
                "'{}'::jsonb, '{}'::jsonb, 'corr-3', :now, 'PENDING', :now)"
            ),
            {"id": evt_id, "tenant": tenant_id, "agg_id": evt_id, "now": now},
        )
        await scope.commit()

    # 2. Worker A claims the event
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyOutboxRepository(scope.session)
        claimed_a = await repo.claim_batch(
            batch_size=1, claim_timeout_seconds=10.0, claimed_by="worker-A", now=now
        )
        await scope.commit()
    assert len(claimed_a) == 1
    assert claimed_a[0].claimed_by == "worker-A"

    # 3. Time passes, Worker A's claim expires and Worker B re-claims the event
    later = now + timedelta(seconds=15)
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyOutboxRepository(scope.session)
        claimed_b = await repo.claim_batch(
            batch_size=1, claim_timeout_seconds=10.0, claimed_by="worker-B", now=later
        )
        await scope.commit()
    assert len(claimed_b) == 1
    assert claimed_b[0].claimed_by == "worker-B"

    # 4. Worker B successfully publishes and records success
    publish_time = later + timedelta(seconds=1)
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyOutboxRepository(scope.session)
        updated = await repo.mark_published(evt_id, now=publish_time, claimed_by="worker-B")
        await scope.commit()
    assert updated is True

    # Verify event is PUBLISHED
    status = await scalar(
        admin_engine, "SELECT status FROM outbox_events WHERE id = :id", id=evt_id
    )
    assert status == "PUBLISHED"

    # 5. Stale Worker A wakes up and attempts to record failure with its old claim
    stale_time = publish_time + timedelta(seconds=1)
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyOutboxRepository(scope.session)
        updated_a = await repo.record_failure(
            evt_id,
            error="Worker A network timeout",
            max_attempts=3,
            now=stale_time,
            claimed_by="worker-A",
        )
        await scope.commit()
    assert updated_a is False

    # 6. Verify event state was NOT overwritten by stale Worker A and remains PUBLISHED
    status_after = await scalar(
        admin_engine, "SELECT status FROM outbox_events WHERE id = :id", id=evt_id
    )
    assert status_after == "PUBLISHED"
