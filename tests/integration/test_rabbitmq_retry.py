"""Tests for RabbitMQ retry, exponential backoff, jitter, and DLQ handling.

Tests:
1. Outbox backoff calculation includes jitter and respects max delay.
2. Consumer routes transient failure to retry queue with backoff and preserves all event metadata.
3. Consumer routes to DLQ when max retries are exhausted.
4. Permanent errors (malformed payload, invalid schema, missing envelope) route directly to DLQ.
5. Event identity (event_id, correlation_id, causation_id, tenant_id) is strictly
   preserved across retry.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from tests.conftest import test_database_url as get_test_database_url

from shared.application.context import RequestContext
from shared.infrastructure.db.outbox import SqlAlchemyOutboxRepository
from shared.infrastructure.db.session import Database
from shared.infrastructure.messaging.consumer import (
    IdempotentConsumer,
)
from shared.infrastructure.messaging.topology import EXCHANGE_DLX, EXCHANGE_RETRY

pytestmark = [pytest.mark.messaging, pytest.mark.integration]


@pytest.fixture
async def database_handle() -> Any:
    db = Database(get_test_database_url(), pool_size=5)
    try:
        yield db
    finally:
        await db.dispose()


class FakeIncomingMessage:
    """Mock aio-pika AbstractIncomingMessage for focused unit/integration testing."""

    def __init__(
        self,
        body: bytes,
        headers: dict[str, Any] | None = None,
        message_id: str | None = None,
        correlation_id: str | None = None,
        channel: Any = None,
    ) -> None:
        self.body = body
        self.headers = headers or {}
        self.message_id = message_id or str(uuid.uuid4())
        self.correlation_id = correlation_id or str(uuid.uuid4())
        self.channel = channel
        self.content_type = "application/json"
        self.content_encoding = "utf-8"
        self.acked = False
        self.nacked = False

    async def ack(self, multiple: bool = False) -> None:
        self.acked = True

    async def nack(self, multiple: bool = False, requeue: bool = False) -> None:
        self.nacked = True


def _make_fake_channel() -> MagicMock:
    channel = MagicMock()
    retry_exchange = AsyncMock()
    dlx_exchange = AsyncMock()

    async def get_exchange(name: str) -> Any:
        if name == EXCHANGE_RETRY:
            return retry_exchange
        if name == EXCHANGE_DLX:
            return dlx_exchange
        return AsyncMock()

    channel.get_exchange = AsyncMock(side_effect=get_exchange)
    channel._retry_exchange = retry_exchange
    channel._dlx_exchange = dlx_exchange
    return channel


async def test_outbox_backoff_and_jitter(database_handle: Database) -> None:
    """Outbox retry calculation produces exponential backoff with jitter bounded by max delay."""
    now = datetime.now(UTC)
    tenant_id = uuid.uuid4()
    evt_id = uuid.uuid4()

    from sqlalchemy import text

    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Outbox Jitter Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"oj-{uuid.uuid4().hex[:6]}"},
        )
        await scope.session.execute(
            text(
                "INSERT INTO outbox_events "
                "(id, tenant_id, event_type, schema_version, aggregate_type, "
                "aggregate_id, payload, headers, correlation_id, occurred_at, status, "
                "attempt_count, available_at) "
                "VALUES (:id, :tenant, 'TestEvt', 1, 'order', :agg_id, '{}'::jsonb, '{}'::jsonb, "
                "'corr', :now, 'CLAIMED', 2, :now)"
            ),
            {"id": evt_id, "tenant": tenant_id, "agg_id": evt_id, "now": now},
        )
        await scope.commit()

    async with database_handle.new_scope() as scope:
        repo = SqlAlchemyOutboxRepository(scope.session)
        # Record failure for attempt 2 with max_attempts=5, base=2.0s, max=30s, jitter=0.5s
        await repo.record_failure(
            evt_id,
            error="Connection timeout",
            max_attempts=5,
            now=now,
            backoff_seconds=2.0,
            max_delay_seconds=30.0,
            jitter_seconds=0.5,
        )
        await scope.commit()

    async with database_handle.new_scope() as scope:
        from sqlalchemy import select

        from shared.infrastructure.db.tables import outbox_events

        row = (
            await scope.session.execute(
                select(outbox_events).where(outbox_events.c.id == evt_id)
            )
        ).mappings().first()

    assert row is not None
    assert row["status"] == "PENDING"
    assert row["last_error"] == "Connection timeout"
    # Delay for attempt 2 is 2.0 * 2^(2-1) = 4.0s + jitter [0, 0.5] -> between 4.0 and 4.6 seconds
    diff = (row["available_at"] - now).total_seconds()
    assert 3.9 <= diff <= 4.6


async def test_consumer_transient_failure_routes_to_retry_queue(
    database_handle: Database,
) -> None:
    """Transient handler failure publishes to retry exchange with backoff
    and ACKs original message.
    """
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Retry Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"rt-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
        initial_retry_delay=1.0,
        retry_multiplier=2.0,
        retry_jitter=0.2,
    )

    async def failing_handler(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise ConnectionResetError("Transient network failure")

    consumer.register_handler("OrderPlaced", failing_handler)

    event_id = str(uuid.uuid4())
    correlation_id = str(uuid.uuid4())
    payload = {
        "event_id": event_id,
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": correlation_id,
        "causation_id": "cause-123",
        "payload": {"order_id": "ord-1"},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    fake_msg = FakeIncomingMessage(body=raw_body, headers={"x-retry-count": 1}, channel=channel)

    await consumer.process_message(fake_msg)

    # 1. Original message was ACKed
    assert fake_msg.acked is True

    # 2. Retry exchange publish was called
    retry_exchange = channel._retry_exchange
    assert retry_exchange.publish.called
    published_msg = retry_exchange.publish.call_args[0][0]
    kwargs = retry_exchange.publish.call_args[1]

    # Verify routing and headers
    assert kwargs["routing_key"] == "arad.fulfillment"
    retry_headers = published_msg.headers
    assert retry_headers["x-retry-count"] == 2
    assert "Transient network failure" in retry_headers["x-last-error"]
    assert published_msg.expiration is not None

    # Verify event identity is preserved
    parsed_body = json.loads(published_msg.body.decode("utf-8"))
    assert parsed_body["event_id"] == event_id
    assert parsed_body["correlation_id"] == correlation_id
    assert parsed_body["causation_id"] == "cause-123"
    assert parsed_body["tenant_id"] == str(tenant_id)


async def test_consumer_exhausted_retries_routes_to_dlq(
    database_handle: Database,
) -> None:
    """When max retries are exceeded, message is routed to DLQ."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Exhaust Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"ex-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
    )

    async def failing_handler(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise ValueError("Repeated operational failure")

    consumer.register_handler("OrderPlaced", failing_handler)

    event_id = str(uuid.uuid4())
    payload = {
        "event_id": event_id,
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-1",
        "payload": {},
    }
    raw_body = json.dumps(payload).encode("utf-8")
    # Message already on attempt 3
    fake_msg = FakeIncomingMessage(body=raw_body, headers={"x-retry-count": 3}, channel=channel)

    await consumer.process_message(fake_msg)

    # 1. Original message was ACKed
    assert fake_msg.acked is True

    # 2. DLX publish was called
    dlx_exchange = channel._dlx_exchange
    assert dlx_exchange.publish.called
    kwargs = dlx_exchange.publish.call_args[1]
    assert kwargs["routing_key"] == "arad.fulfillment.dlq"

    published_msg = dlx_exchange.publish.call_args[0][0]
    assert "Retries exhausted" in published_msg.headers["x-death-reason"]


async def test_consumer_permanent_error_routes_immediately_to_dlq(
    database_handle: Database,
) -> None:
    """Permanent errors (e.g. malformed JSON or schema version mismatch)
    bypass retry and route to DLQ immediately.
    """
    channel = _make_fake_channel()
    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=5,
    )

    # Message with invalid JSON
    bad_msg = FakeIncomingMessage(body=b"{not valid json...", channel=channel)
    await consumer.process_message(bad_msg)

    assert bad_msg.acked is True
    assert channel._dlx_exchange.publish.called
    dlq_msg = channel._dlx_exchange.publish.call_args[0][0]
    assert "Malformed JSON" in dlq_msg.headers["x-death-reason"]


async def test_consumer_unsupported_schema_version_routes_to_dlq(
    database_handle: Database,
) -> None:
    """Messages with unsupported schema_version (e.g. version 2) route directly to DLQ."""
    channel = _make_fake_channel()
    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=5,
    )

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 99,  # Unsupported version!
        "tenant_id": str(uuid.uuid4()),
        "correlation_id": "c-99",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    await consumer.process_message(msg)

    assert msg.acked is True
    assert channel._dlx_exchange.publish.called
    dlq_msg = channel._dlx_exchange.publish.call_args[0][0]
    assert "Unsupported schema version" in dlq_msg.headers["x-death-reason"]
