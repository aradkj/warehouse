"""Inbox & consumer idempotency integration tests against PostgreSQL.

Proves:
1. First message delivery is claimed and processed.
2. Duplicate message delivery returns False (skip) with no duplicate side effect.
3. Distinct consumers (e.g. fulfillment, search) process the same message once each independently.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.support.database import count, scalar

from shared.infrastructure.db.session import Database
from shared.infrastructure.messaging.inbox import SqlAlchemyInboxRepository

from tests.conftest import test_database_url as get_test_database_url

pytestmark = [pytest.mark.inbox, pytest.mark.repository]


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


async def test_inbox_first_delivery_is_processed_and_duplicate_is_skipped(
    database_handle: Database, admin_engine: AsyncEngine
) -> None:
    now = datetime.now(UTC)
    tenant_id = uuid.uuid4()
    msg_id = f"msg-{uuid.uuid4().hex}"
    consumer = "fulfillment_consumer"

    # Register tenant for FK constraint
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Inbox Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"inbox-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    # 1. First delivery -> claim_for_processing returns True
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        should_process = await repo.claim_for_processing(
            message_id=msg_id,
            consumer_name=consumer,
            tenant_id=tenant_id,
            event_type="OrderConfirmed",
            now=now,
        )
        assert should_process is True
        await repo.mark_processed(message_id=msg_id, consumer_name=consumer, now=now)
        await scope.commit()

    # Verify inbox status
    status = await scalar(
        admin_engine,
        "SELECT status FROM inbox_messages WHERE message_id = :msg AND consumer_name = :cons",
        msg=msg_id,
        cons=consumer,
    )
    assert status == "PROCESSED"

    # 2. Redelivery (duplicate message) -> claim_for_processing returns False!
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        should_process_dup = await repo.claim_for_processing(
            message_id=msg_id,
            consumer_name=consumer,
            tenant_id=tenant_id,
            event_type="OrderConfirmed",
            now=now,
        )
        assert should_process_dup is False
        await scope.commit()


async def test_distinct_consumers_process_the_same_message_independently(
    database_handle: Database, admin_engine: AsyncEngine
) -> None:
    now = datetime.now(UTC)
    tenant_id = uuid.uuid4()
    msg_id = f"msg-shared-{uuid.uuid4().hex}"

    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Shared Inbox Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"shared-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    # Consumer A (fulfillment)
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        c_a = await repo.claim_for_processing(
            message_id=msg_id,
            consumer_name="fulfillment",
            tenant_id=tenant_id,
            event_type="OrderConfirmed",
            now=now,
        )
        assert c_a is True
        await repo.mark_processed(message_id=msg_id, consumer_name="fulfillment", now=now)
        await scope.commit()

    # Consumer B (search)
    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyInboxRepository(scope.session)
        c_b = await repo.claim_for_processing(
            message_id=msg_id,
            consumer_name="search",
            tenant_id=tenant_id,
            event_type="OrderConfirmed",
            now=now,
        )
        assert c_b is True
        await repo.mark_processed(message_id=msg_id, consumer_name="search", now=now)
        await scope.commit()

    # Count inbox records
    count_records = await count(admin_engine, "inbox_messages", message_id=msg_id)
    assert count_records == 2
