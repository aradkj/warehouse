"""Inbox concurrency, lease ownership, and crash recovery tests.

Tests:
1. Two concurrent workers racing to claim the same message: exactly ONE wins GRANTED.
2. Active lease blocks second worker from processing.
3. Expired lease allows reclaim by a second worker.
4. Two concurrent workers racing to reclaim an expired lease: exactly ONE wins.
5. Original worker cannot mark PROCESSED after losing its lease.
6. Original worker cannot mark FAILED after losing its lease.
7. Durable failure recording survives business transaction rollback.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from tests.conftest import test_database_url as get_test_database_url

from shared.infrastructure.db.session import Database
from shared.infrastructure.db.tables import inbox_messages
from shared.infrastructure.messaging.inbox import (
    ClaimStatus,
    InboxOwnershipLostError,
    SqlAlchemyInboxRepository,
)

pytestmark = [pytest.mark.inbox, pytest.mark.repository]


@pytest.fixture
async def database_handle() -> Any:
    db = Database(get_test_database_url(), pool_size=10, max_overflow=5)
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture
async def admin_engine() -> Any:
    engine = Database(get_test_database_url())._engine
    yield engine


async def _create_tenant(database: Database) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    async with database.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Concurrency Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"conc-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()
    return tenant_id


async def test_concurrent_claim_race_only_one_wins(database_handle: Database) -> None:
    """Two concurrent workers attempting to claim the same message: exactly ONE wins GRANTED."""
    tenant_id = await _create_tenant(database_handle)
    msg_id = f"msg-race-{uuid.uuid4().hex}"
    consumer = "fulfillment_service"
    now = datetime.now(UTC)

    async def _worker_claim(worker_id: str) -> Any:
        async with database_handle.new_scope() as scope:
            repo = SqlAlchemyInboxRepository(scope.session)
            result = await repo.claim_lease(
                message_id=msg_id,
                consumer_name=consumer,
                tenant_id=tenant_id,
                event_type="OrderConfirmed",
                worker_id=worker_id,
                now=now,
                lease_duration_seconds=30.0,
            )
            await scope.commit()
            return result

    # Run Worker A and Worker B concurrently
    res_a, res_b = await asyncio.gather(
        _worker_claim("worker-A"),
        _worker_claim("worker-B"),
    )

    statuses = [res_a.status, res_b.status]
    assert ClaimStatus.GRANTED in statuses
    assert ClaimStatus.LOCKED_BY_OTHER in statuses
    # Exactly one granted, one locked by other
    assert statuses.count(ClaimStatus.GRANTED) == 1
    assert statuses.count(ClaimStatus.LOCKED_BY_OTHER) == 1


async def test_active_lease_blocks_second_worker(database_handle: Database) -> None:
    """An active unexpired lease blocks any other worker from claiming the message."""
    tenant_id = await _create_tenant(database_handle)
    msg_id = f"msg-active-{uuid.uuid4().hex}"
    consumer = "search_service"
    now = datetime.now(UTC)

    # Worker A claims with 30s lease
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        res_a = await repo.claim_lease(
            message_id=msg_id,
            consumer_name=consumer,
            tenant_id=tenant_id,
            event_type="ProductCreated",
            worker_id="worker-A",
            now=now,
            lease_duration_seconds=30.0,
        )
        await scope.commit()

    assert res_a.status == ClaimStatus.GRANTED

    # Worker B tries to claim 5 seconds later (lease still active)
    later = now + timedelta(seconds=5)
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        res_b = await repo.claim_lease(
            message_id=msg_id,
            consumer_name=consumer,
            tenant_id=tenant_id,
            event_type="ProductCreated",
            worker_id="worker-B",
            now=later,
            lease_duration_seconds=30.0,
        )
        await scope.commit()

    assert res_b.status == ClaimStatus.LOCKED_BY_OTHER


async def test_expired_lease_allows_reclaim(database_handle: Database) -> None:
    """After a lease expires (worker crash), another worker can atomically reclaim it."""
    tenant_id = await _create_tenant(database_handle)
    msg_id = f"msg-reclaim-{uuid.uuid4().hex}"
    consumer = "notifications_service"
    now = datetime.now(UTC)

    # Worker A claims with 5s lease
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        res_a = await repo.claim_lease(
            message_id=msg_id,
            consumer_name=consumer,
            tenant_id=tenant_id,
            event_type="OrderDelivered",
            worker_id="worker-A",
            now=now,
            lease_duration_seconds=5.0,
        )
        await scope.commit()

    assert res_a.status == ClaimStatus.GRANTED

    # 10 seconds later: Worker A crashed and lease expired
    after_expiry = now + timedelta(seconds=10)
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        res_b = await repo.claim_lease(
            message_id=msg_id,
            consumer_name=consumer,
            tenant_id=tenant_id,
            event_type="OrderDelivered",
            worker_id="worker-B",
            now=after_expiry,
            lease_duration_seconds=30.0,
        )
        await scope.commit()

    assert res_b.status == ClaimStatus.GRANTED
    assert res_b.worker_id == "worker-B"


async def test_concurrent_reclaim_of_expired_lease_only_one_wins(database_handle: Database) -> None:
    """Two workers racing to reclaim an expired lease: exactly ONE wins."""
    tenant_id = await _create_tenant(database_handle)
    msg_id = f"msg-race-reclaim-{uuid.uuid4().hex}"
    consumer = "billing_service"
    now = datetime.now(UTC)

    # Worker A creates initial lease that expires
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        await repo.claim_lease(
            message_id=msg_id,
            consumer_name=consumer,
            tenant_id=tenant_id,
            event_type="InvoiceCreated",
            worker_id="dead-worker",
            now=now,
            lease_duration_seconds=5.0,
        )
        await scope.commit()

    after_expiry = now + timedelta(seconds=10)

    async def _reclaim(worker_id: str) -> Any:
        async with database_handle.new_scope() as scope:
            repo = SqlAlchemyInboxRepository(scope.session)
            result = await repo.claim_lease(
                message_id=msg_id,
                consumer_name=consumer,
                tenant_id=tenant_id,
                event_type="InvoiceCreated",
                worker_id=worker_id,
                now=after_expiry,
                lease_duration_seconds=30.0,
            )
            await scope.commit()
            return result

    res_1, res_2 = await asyncio.gather(
        _reclaim("worker-1"),
        _reclaim("worker-2"),
    )

    statuses = [res_1.status, res_2.status]
    assert statuses.count(ClaimStatus.GRANTED) == 1
    assert statuses.count(ClaimStatus.LOCKED_BY_OTHER) == 1


async def test_original_worker_cannot_finalize_after_losing_lease(
    database_handle: Database,
) -> None:
    """If Worker A loses its lease to Worker B, Worker A cannot mark PROCESSED."""
    tenant_id = await _create_tenant(database_handle)
    msg_id = f"msg-lost-lease-{uuid.uuid4().hex}"
    consumer = "fulfillment_service"
    now = datetime.now(UTC)

    # 1. Worker A claims with 5s lease
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        await repo.claim_lease(
            message_id=msg_id,
            consumer_name=consumer,
            tenant_id=tenant_id,
            event_type="OrderConfirmed",
            worker_id="worker-A",
            now=now,
            lease_duration_seconds=5.0,
        )
        await scope.commit()

    # 2. Worker B reclaims after expiry
    after_expiry = now + timedelta(seconds=10)
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        res_b = await repo.claim_lease(
            message_id=msg_id,
            consumer_name=consumer,
            tenant_id=tenant_id,
            event_type="OrderConfirmed",
            worker_id="worker-B",
            now=after_expiry,
            lease_duration_seconds=30.0,
        )
        await scope.commit()
    assert res_b.status == ClaimStatus.GRANTED

    # 3. Worker A attempts to finalize: MUST fail with InboxOwnershipLostError
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        with pytest.raises(InboxOwnershipLostError):
            await repo.mark_processed(
                message_id=msg_id,
                consumer_name=consumer,
                worker_id="worker-A",
                now=after_expiry,
            )


async def test_original_worker_cannot_fail_after_losing_lease(database_handle: Database) -> None:
    """If Worker A loses its lease to Worker B, Worker A cannot mark FAILED."""
    tenant_id = await _create_tenant(database_handle)
    msg_id = f"msg-lost-lease-fail-{uuid.uuid4().hex}"
    consumer = "fulfillment_service"
    now = datetime.now(UTC)

    # Worker A claims with 5s lease
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        await repo.claim_lease(
            message_id=msg_id,
            consumer_name=consumer,
            tenant_id=tenant_id,
            event_type="OrderConfirmed",
            worker_id="worker-A",
            now=now,
            lease_duration_seconds=5.0,
        )
        await scope.commit()

    # Worker B reclaims after expiry
    after_expiry = now + timedelta(seconds=10)
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        await repo.claim_lease(
            message_id=msg_id,
            consumer_name=consumer,
            tenant_id=tenant_id,
            event_type="OrderConfirmed",
            worker_id="worker-B",
            now=after_expiry,
            lease_duration_seconds=30.0,
        )
        await scope.commit()

    # Worker A attempts to mark failed: raises InboxOwnershipLostError
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        with pytest.raises(InboxOwnershipLostError):
            await repo.record_failure(
                message_id=msg_id,
                consumer_name=consumer,
                worker_id="worker-A",
                error="Some error",
                now=after_expiry,
            )


async def test_failure_recording_survives_business_rollback(database_handle: Database) -> None:
    """Durable failure recording correctly records FAILED status even when business
    transaction rolls back.
    """
    tenant_id = await _create_tenant(database_handle)
    msg_id = f"msg-fail-durable-{uuid.uuid4().hex}"
    consumer = "inventory_service"
    now = datetime.now(UTC)
    worker_id = "worker-fail-test"

    # Step 1: Durable claim is committed
    async with database_handle.new_scope() as claim_scope:
        repo = SqlAlchemyInboxRepository(claim_scope.session)
        claim_res = await repo.claim_lease(
            message_id=msg_id,
            consumer_name=consumer,
            tenant_id=tenant_id,
            event_type="StockAdjusted",
            worker_id=worker_id,
            now=now,
            lease_duration_seconds=30.0,
        )
        await claim_scope.commit()
    assert claim_res.status == ClaimStatus.GRANTED

    # Step 2: Business transaction executes, fails, and rolls back
    async with database_handle.new_scope() as biz_scope:
        # Simulate business failure
        await biz_scope.rollback()

    # Step 3: Record failure in separate transaction
    async with database_handle.new_scope() as fail_scope:
        fail_repo = SqlAlchemyInboxRepository(fail_scope.session)
        await fail_repo.record_failure(
            message_id=msg_id,
            consumer_name=consumer,
            worker_id=worker_id,
            error="Simulated business error",
            now=now,
        )
        await fail_scope.commit()

    # Verify failure is durably recorded in database
    async with database_handle.new_scope() as verify_scope:
        row = (
            await verify_scope.session.execute(
                select(inbox_messages).where(
                    inbox_messages.c.message_id == msg_id,
                    inbox_messages.c.consumer_name == consumer,
                )
            )
        ).mappings().first()

    assert row is not None
    assert row["status"] == "FAILED"
    assert "Simulated business error" in row["last_error"]
    assert row["failed_at"] is not None
