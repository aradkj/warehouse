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

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aio_pika
import pamqp.commands as pamqp_cmd
import pytest
from aiormq.abc import DeliveredMessage
from aiormq.exceptions import ChannelClosed, PublishError
from tests.conftest import test_database_url as get_test_database_url

from bootstrap.config import get_settings
from shared.application.context import RequestContext
from shared.infrastructure.db.outbox import SqlAlchemyOutboxRepository
from shared.infrastructure.db.session import Database
from shared.infrastructure.messaging.consumer import (
    IdempotentConsumer,
    PublishConfirmationError,
    UnroutableMessageError,
)
from shared.infrastructure.messaging.topology import EXCHANGE_DLX, EXCHANGE_RETRY, declare_topology

pytestmark = [pytest.mark.messaging, pytest.mark.integration]


@pytest.fixture
async def database_handle() -> Any:
    db = Database(get_test_database_url(), pool_size=5)
    try:
        yield db
    finally:
        await db.dispose()


def _make_fake_channel() -> MagicMock:
    channel = MagicMock()
    retry_exchange = AsyncMock()
    dlx_exchange = AsyncMock()

    # Default mock publish outcome is a documented positive publisher confirmation (Basic.Ack)
    retry_exchange.publish.return_value = pamqp_cmd.Basic.Ack(delivery_tag=1)
    dlx_exchange.publish.return_value = pamqp_cmd.Basic.Ack(delivery_tag=1)

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
        self.channel = channel if channel is not None else _make_fake_channel()
        self.content_type = "application/json"
        self.content_encoding = "utf-8"
        self.acked = False
        self.nacked = False

    async def ack(self, multiple: bool = False) -> None:
        self.acked = True

    async def nack(self, multiple: bool = False, requeue: bool = False) -> None:
        self.nacked = True


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
            (await scope.session.execute(select(outbox_events).where(outbox_events.c.id == evt_id)))
            .mappings()
            .first()
        )

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


async def test_consumer_retry_publication_failure_preserves_message_and_nacks(
    database_handle: Database,
) -> None:
    """When retry exchange publishing fails, source message is NOT acked and is nacked (MSG-003)."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Retry Failure Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"rt-fail-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    channel._retry_exchange.publish.side_effect = ConnectionResetError("Broker connection died")

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
    )

    async def transient_fail(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Transient database lock timeout")

    consumer.register_handler("OrderPlaced", transient_fail)

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-fail-retry",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    await consumer.process_message(msg)

    # Source message MUST NOT be acknowledged if retry handoff failed
    assert msg.acked is False
    assert msg.nacked is True


async def test_consumer_dlq_publication_failure_preserves_message_and_nacks(
    database_handle: Database,
) -> None:
    """When DLQ publishing fails, source message is NOT acked and is nacked (MSG-001)."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'DLQ Failure Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"dlq-fail-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    channel._dlx_exchange.publish.side_effect = ConnectionResetError("DLX unreachable")

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=1,
    )

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 99,  # Permanent error routing to DLQ
        "tenant_id": str(tenant_id),
        "correlation_id": "c-fail-dlq",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    await consumer.process_message(msg)

    # Source message MUST NOT be acknowledged if DLQ handoff failed
    assert msg.acked is False
    assert msg.nacked is True


async def test_consumer_locked_by_other_defers_to_retry_without_premature_ack(
    database_handle: Database,
) -> None:
    """Duplicate delivery while another worker holds lease defers via retry queue (MSG-004)."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import text

    from shared.infrastructure.messaging.inbox import SqlAlchemyInboxRepository

    event_id = str(uuid.uuid4())
    tenant_id = uuid.uuid4()
    consumer_name = "fulfillment"

    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Lease Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"lease-t-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    # Pre-claim active lease by Worker A
    async with database_handle.new_scope() as claim_scope:
        repo = SqlAlchemyInboxRepository(claim_scope.session)
        await repo.claim_lease(
            message_id=event_id,
            consumer_name=consumer_name,
            tenant_id=tenant_id,
            event_type="OrderPlaced",
            worker_id="worker-A-active",
            now=datetime.now(UTC),
            lease_duration_seconds=30.0,
        )
        await claim_scope.commit()

    channel = _make_fake_channel()
    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name=consumer_name,
        lease_duration_seconds=30.0,
    )
    consumer.register_handler("OrderPlaced", AsyncMock())

    payload = {
        "event_id": event_id,
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-duplicate",
        "payload": {},
    }
    duplicate_msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    await consumer.process_message(duplicate_msg)

    # Duplicate was deferred to retry exchange with delay, then acked on main queue
    assert duplicate_msg.acked is True
    assert channel._retry_exchange.publish.called
    published_msg = channel._retry_exchange.publish.call_args[0][0]
    assert published_msg.expiration == timedelta(seconds=10.0)
    assert "Leased by worker worker-A-active" in published_msg.headers["x-last-error"]


async def test_confirmed_retry_publication_with_broker_ack(database_handle: Database) -> None:
    """When broker returns Basic.Ack, retry publication is confirmed and source message is ACKed."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Ack Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"ack-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    # Simulate broker publisher confirmation with Basic.Ack
    channel._retry_exchange.publish.return_value = pamqp_cmd.Basic.Ack(delivery_tag=1)

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
        base_routing_backoff=0.01,
    )

    async def fail_handler(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Transient processing error")

    consumer.register_handler("OrderPlaced", fail_handler)

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-ack",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    await consumer.process_message(msg)

    # Message must be ACKed after confirmed publication
    assert msg.acked is True
    assert msg.nacked is False
    assert consumer.get_consecutive_routing_failures() == 0


async def test_confirmed_dlq_publication_with_broker_ack(database_handle: Database) -> None:
    """When broker returns Basic.Ack, DLQ publication is confirmed and source message is ACKed."""
    channel = _make_fake_channel()
    channel._dlx_exchange.publish.return_value = pamqp_cmd.Basic.Ack(delivery_tag=1)

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=1,
        base_routing_backoff=0.01,
    )

    bad_msg = FakeIncomingMessage(body=b"{invalid json...", channel=channel)
    await consumer.process_message(bad_msg)

    assert bad_msg.acked is True
    assert bad_msg.nacked is False
    assert consumer.get_consecutive_routing_failures() == 0


async def test_publisher_negative_acknowledgment_triggers_requeue_and_no_ack(
    database_handle: Database,
) -> None:
    """When broker returns Basic.Nack, source message is NOT acked and is requeued via nack."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Nack Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"nack-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    # Broker negatively acknowledges the published retry message
    channel._retry_exchange.publish.return_value = pamqp_cmd.Basic.Nack(delivery_tag=1)

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
        base_routing_backoff=0.01,
    )

    async def transient_err(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Lock contention")

    consumer.register_handler("OrderPlaced", transient_err)

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-nack-test",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    await consumer.process_message(msg)

    assert msg.acked is False
    assert msg.nacked is True
    assert consumer.get_consecutive_routing_failures() == 1


async def test_unroutable_returned_message_triggers_requeue_and_no_ack(
    database_handle: Database,
) -> None:
    """When broker returns message as unroutable (Basic.Return), source message is not acked."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Return Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"ret-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    # Broker returns the message because routing key has no bound queue
    channel._retry_exchange.publish.return_value = pamqp_cmd.Basic.Return(
        reply_code=312, reply_text="NO_ROUTE", exchange=EXCHANGE_RETRY, routing_key="non_existent"
    )

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
        base_routing_backoff=0.01,
    )

    async def transient_err(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Downstream service busy")

    consumer.register_handler("OrderPlaced", transient_err)

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-unroutable",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    await consumer.process_message(msg)

    assert msg.acked is False
    assert msg.nacked is True
    assert consumer.get_consecutive_routing_failures() == 1


async def test_publish_timeout_and_channel_closure_triggers_requeue_and_no_ack(
    database_handle: Database,
) -> None:
    """When publishing times out or channel is closed, source message is preserved and nacked."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Timeout Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"to-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    channel._retry_exchange.publish.side_effect = TimeoutError("Confirmation timed out")

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
        base_routing_backoff=0.01,
    )

    async def transient_err(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Transient error")

    consumer.register_handler("OrderPlaced", transient_err)

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-to",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    await consumer.process_message(msg)

    assert msg.acked is False
    assert msg.nacked is True


async def test_persistent_routing_failure_bounded_backoff_and_recovery(
    database_handle: Database,
) -> None:
    """Persistent routing failure applies exponential backoff, throttles logs, and recovers cleanly."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Loop Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"loop-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    hook_calls: list[str] = []

    async def routing_failure_hook(message: Any, exc: Exception) -> None:
        hook_calls.append(message.message_id)

    channel = _make_fake_channel()
    channel._retry_exchange.publish.side_effect = ConnectionResetError("Broker unreachable")

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
        base_routing_backoff=0.01,
        max_routing_backoff=0.05,
        routing_error_log_interval=1.0,
        on_routing_failure=routing_failure_hook,
    )

    async def fail_handler(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Handler error")

    consumer.register_handler("OrderPlaced", fail_handler)

    # Deliver 3 failing messages consecutively
    for i in range(3):
        payload = {
            "event_id": f"evt-loop-{i}",
            "event_type": "OrderPlaced",
            "schema_version": 1,
            "tenant_id": str(tenant_id),
            "correlation_id": f"c-loop-{i}",
            "payload": {},
        }
        msg = FakeIncomingMessage(
            body=json.dumps(payload).encode("utf-8"),
            message_id=f"msg-loop-{i}",
            channel=channel,
        )
        await consumer.process_message(msg)
        assert msg.acked is False
        assert msg.nacked is True

    # Bounded failure counter and recovery hook verified
    assert consumer.get_consecutive_routing_failures() == 3
    assert len(hook_calls) == 3

    # Now broker heals: retry publication succeeds
    channel._retry_exchange.publish.side_effect = None
    channel._retry_exchange.publish.return_value = pamqp_cmd.Basic.Ack(delivery_tag=1)

    heal_payload = {
        "event_id": "evt-healed",
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-healed",
        "payload": {},
    }
    heal_msg = FakeIncomingMessage(
        body=json.dumps(heal_payload).encode("utf-8"),
        message_id="msg-healed",
        channel=channel,
    )
    await consumer.process_message(heal_msg)

    # Clean recovery
    assert heal_msg.acked is True
    assert consumer.get_consecutive_routing_failures() == 0


async def test_live_rabbitmq_confirmed_publication() -> None:
    """Verify live RabbitMQ broker publisher confirmation behavior."""
    try:
        connection = await aio_pika.connect_robust(get_settings().rabbitmq_url, timeout=2.0)
    except Exception:
        pytest.skip("Live RabbitMQ broker not reachable in this test environment")

    async with connection:
        channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
        await declare_topology(channel)
        retry_ex = await channel.get_exchange(EXCHANGE_RETRY)

        test_msg = aio_pika.Message(
            body=b"probe",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        # Confirmed publish to existing queue bound to retry exchange
        confirm = await retry_ex.publish(test_msg, routing_key="arad.search", mandatory=True)
        assert isinstance(confirm, pamqp_cmd.Basic.Ack)

        # Mandatory publish to an unroutable routing key raises PublishError
        unroutable_msg = aio_pika.Message(
            body=b"probe unroutable",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        )
        with pytest.raises(PublishError):
            await retry_ex.publish(
                unroutable_msg, routing_key="non_existent_routing_key_xyz", mandatory=True
            )


async def test_cross_consumer_hot_loop_prevention_on_routing_failure(
    database_handle: Database,
) -> None:
    """When multiple consumers share a queue, routing failure holds the unacked message

    during backoff so another consumer cannot immediately redeliver it in a zero-delay loop.
    """
    channel = _make_fake_channel()
    channel._retry_exchange.publish.side_effect = ConnectionResetError("Broker unreachable")

    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Shared Queue Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"shared-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    # Consumer A on shared queue "arad.shared_queue"
    consumer_a = IdempotentConsumer(
        database=database_handle,
        consumer_name="worker_a",
        queue_name="arad.shared_queue",
        max_retries=3,
        base_routing_backoff=0.2,
        routing_backoff_jitter=0.0,
    )

    # Consumer B on the exact same shared queue
    consumer_b = IdempotentConsumer(
        database=database_handle,
        consumer_name="worker_b",
        queue_name="arad.shared_queue",
        max_retries=3,
        base_routing_backoff=0.2,
        routing_backoff_jitter=0.0,
    )

    async def fail_handler(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Handler error")

    consumer_a.register_handler("OrderPlaced", fail_handler)
    consumer_b.register_handler("OrderPlaced", fail_handler)

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-shared",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)

    # Launch Consumer A processing in background
    start_time = time.monotonic()
    task_a = asyncio.create_task(consumer_a.process_message(msg))

    # After 50ms (well within Consumer A's 200ms backoff window):
    await asyncio.sleep(0.05)
    # The message MUST NOT yet be nacked/requeued on the queue!
    assert msg.nacked is False, (
        "Message was immediately nacked before backoff, which would allow Consumer B "
        "to redeliver it in an unthrottled cross-consumer hot loop!"
    )

    # Wait for Consumer A to complete its backoff and nack
    await task_a
    elapsed_a = time.monotonic() - start_time
    assert elapsed_a >= 0.18, f"Backoff delay was bypassed: elapsed={elapsed_a}s"
    assert msg.nacked is True, "Message was not requeued after backoff"

    # Now simulate Consumer B receiving the requeued message
    msg_redelivered = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    start_time_b = time.monotonic()
    await consumer_b.process_message(msg_redelivered)
    elapsed_b = time.monotonic() - start_time_b
    assert elapsed_b >= 0.18, f"Consumer B failed to enforce backoff: elapsed={elapsed_b}s"
    assert msg_redelivered.nacked is True


async def test_confirmation_fail_closed_on_none_result(database_handle: Database) -> None:
    """When publisher confirmation returns None (unconfirmed), fail closed: source message is NOT ACKed."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'None Conf Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"nc-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    # Simulate broker returning None (e.g. publisher confirms disabled on underlay channel)
    channel._retry_exchange.publish.return_value = None

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
        base_routing_backoff=0.01,
    )

    async def transient_err(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Transient error")

    consumer.register_handler("OrderPlaced", transient_err)

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-none",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    await consumer.process_message(msg)

    # Fail closed assertion: source message MUST NOT be acknowledged on None result
    assert msg.acked is False
    assert msg.nacked is True


async def test_confirmation_fail_closed_on_unexpected_object(database_handle: Database) -> None:
    """When publish returns an unexpected object or type, fail closed: source message is NOT ACKed."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Unexp Obj Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"uo-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    # Simulate unexpected confirmation object
    channel._retry_exchange.publish.return_value = "UNEXPECTED_ACK_PAYLOAD"

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
        base_routing_backoff=0.01,
    )

    async def transient_err(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Transient error")

    consumer.register_handler("OrderPlaced", transient_err)

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-unexp",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    await consumer.process_message(msg)

    # Fail closed assertion: unexpected object must never lead to source ACK
    assert msg.acked is False
    assert msg.nacked is True


async def test_confirmation_fail_closed_on_basic_reject(database_handle: Database) -> None:
    """When broker returns Basic.Reject, fail closed: source message is NOT ACKed."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Reject Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"rej-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    channel._retry_exchange.publish.return_value = pamqp_cmd.Basic.Reject(delivery_tag=1)

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
        base_routing_backoff=0.01,
    )

    async def transient_err(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Transient error")

    consumer.register_handler("OrderPlaced", transient_err)

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-rej",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    await consumer.process_message(msg)

    assert msg.acked is False
    assert msg.nacked is True


async def test_source_ack_only_occurs_after_confirmed_publication(
    database_handle: Database,
) -> None:
    """Verify that source message ACK strictly occurs AFTER publisher confirmation succeeds."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Timing Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"tm-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    publish_events: list[str] = []

    async def publish_with_timing_check(*args: Any, **kwargs: Any) -> pamqp_cmd.Basic.Ack:
        publish_events.append("publish_called")
        # Assert message is strictly unacknowledged while publish is in-flight
        assert msg.acked is False, (
            "Premature ACK! Source message was ACKed before confirmation completed."
        )
        return pamqp_cmd.Basic.Ack(delivery_tag=1)

    channel._retry_exchange.publish.side_effect = publish_with_timing_check

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
        base_routing_backoff=0.01,
    )

    async def transient_err(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Transient error")

    consumer.register_handler("OrderPlaced", transient_err)

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-timing",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    await consumer.process_message(msg)

    # Only after publication is confirmed does source ACK occur
    assert publish_events == ["publish_called"]
    assert msg.acked is True
    assert msg.nacked is False


async def test_source_ack_never_called_across_unsuccessful_outcomes(
    database_handle: Database,
) -> None:
    """Comprehensive test asserting that source message.ack() is NEVER called across any failed outcome."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Matrix Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"mx-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    failing_outcomes: list[Any] = [
        pamqp_cmd.Basic.Nack(delivery_tag=1),
        pamqp_cmd.Basic.Reject(delivery_tag=1),
        pamqp_cmd.Basic.Return(
            reply_code=312, reply_text="NO_ROUTE", exchange="e", routing_key="k"
        ),
        None,
        "UNEXPECTED",
        12345,
        object(),
    ]

    for idx, outcome in enumerate(failing_outcomes):
        channel = _make_fake_channel()
        channel._retry_exchange.publish.return_value = outcome

        consumer = IdempotentConsumer(
            database=database_handle,
            consumer_name="fulfillment",
            max_retries=3,
            base_routing_backoff=0.01,
        )

        async def transient_err(payload: dict[str, Any], ctx: RequestContext) -> None:
            raise RuntimeError("Transient error")

        consumer.register_handler("OrderPlaced", transient_err)

        payload = {
            "event_id": str(uuid.uuid4()),
            "event_type": "OrderPlaced",
            "schema_version": 1,
            "tenant_id": str(tenant_id),
            "correlation_id": f"c-matrix-{idx}",
            "payload": {},
        }
        msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
        await consumer.process_message(msg)

        assert msg.acked is False, f"Failed: outcome {outcome!r} caused source message ACK!"
        assert msg.nacked is True


def test_verify_confirmation_direct_fail_closed_matrix(database_handle: Database) -> None:
    """Direct unit audit of _verify_confirmation ensuring fail-closed behavior on all inputs."""
    consumer = IdempotentConsumer(database=database_handle, consumer_name="fulfillment")

    # Documented positive confirmation succeeds without exception
    consumer._verify_confirmation(pamqp_cmd.Basic.Ack(delivery_tag=1))

    # None MUST raise PublishConfirmationError
    with pytest.raises(PublishConfirmationError, match="broker returned None"):
        consumer._verify_confirmation(None)

    # Negative acknowledgment MUST raise PublishConfirmationError
    with pytest.raises(PublishConfirmationError, match="negatively acknowledged"):
        consumer._verify_confirmation(pamqp_cmd.Basic.Nack(delivery_tag=1))

    # Rejection MUST raise PublishConfirmationError
    with pytest.raises(PublishConfirmationError, match="negatively acknowledged"):
        consumer._verify_confirmation(pamqp_cmd.Basic.Reject(delivery_tag=1))

    # Unroutable return MUST raise UnroutableMessageError
    with pytest.raises(UnroutableMessageError, match="returned as unroutable"):
        consumer._verify_confirmation(
            pamqp_cmd.Basic.Return(
                reply_code=312, reply_text="NO_ROUTE", exchange="e", routing_key="k"
            )
        )

    # DeliveredMessage (unroutable return frame in aiormq) MUST raise UnroutableMessageError
    mock_delivered = MagicMock(spec=DeliveredMessage)
    with pytest.raises(UnroutableMessageError, match="returned as unroutable"):
        consumer._verify_confirmation(mock_delivered)

    # Ambiguous/unexpected types MUST raise PublishConfirmationError
    for invalid in ("CONFIRMED", 100, False, object(), [1, 2]):
        with pytest.raises(PublishConfirmationError, match="Ambiguous or unexpected"):
            consumer._verify_confirmation(invalid)


async def test_routing_failure_cancellation_during_backoff(database_handle: Database) -> None:
    """When consumer task is cancelled during backoff sleep, cancellation propagates and message is not acked."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Cancel Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"cn-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    channel._retry_exchange.publish.side_effect = ConnectionResetError("Broker unreachable")

    routing_reached = asyncio.Event()

    async def on_routing_failure_hook(m: Any, err: Exception) -> None:
        routing_reached.set()

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
        base_routing_backoff=2.0,  # Generous backoff so task is sleeping when cancelled
        routing_backoff_jitter=0.0,
        on_routing_failure=on_routing_failure_hook,
    )

    async def fail_handler(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Transient error")

    consumer.register_handler("OrderPlaced", fail_handler)

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-cancel",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)

    task = asyncio.create_task(consumer.process_message(msg))
    # Wait until hook sets event, meaning _handle_routing_failure has been entered
    await asyncio.wait_for(routing_reached.wait(), timeout=5.0)
    # Briefly allow event loop to enter the sleep(backoff) call
    await asyncio.sleep(0.01)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Message must NOT be acked
    assert msg.acked is False
    # Attempted nack via shield during cancellation
    assert msg.nacked is True


async def test_routing_failure_channel_closure_during_nack(database_handle: Database) -> None:
    """When channel is closed before/during nack, exception is caught gracefully and message is not acked."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'ChanClose Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"cc-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    channel._retry_exchange.publish.side_effect = ConnectionResetError("Broker unreachable")

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
        base_routing_backoff=0.01,
    )

    async def fail_handler(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Transient error")

    consumer.register_handler("OrderPlaced", fail_handler)

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-chan-close",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)
    # Simulate nack raising ChannelClosed (transport dropped)
    msg.nack = AsyncMock(side_effect=ChannelClosed(504, "Channel closed by broker"))  # type: ignore[method-assign]

    # Must complete gracefully without unhandled exception crashing worker
    await consumer.process_message(msg)
    assert msg.acked is False


async def test_routing_failure_recovery_hook_failure_does_not_ack(
    database_handle: Database,
) -> None:
    """When on_routing_failure hook crashes, the error is handled, backoff proceeds, and message is not acked."""
    from sqlalchemy import text

    tenant_id = uuid.uuid4()
    async with database_handle.new_scope() as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version) "
                "VALUES (:id, :slug, 'Hook Crash Tenant', 'ACTIVE', 1)"
            ),
            {"id": tenant_id, "slug": f"hc-{uuid.uuid4().hex[:6]}"},
        )
        await scope.commit()

    channel = _make_fake_channel()
    channel._retry_exchange.publish.side_effect = ConnectionResetError("Broker unreachable")

    async def crashing_recovery_hook(msg: Any, exc: Exception) -> None:
        raise RuntimeError("Topology recovery catastrophic crash")

    consumer = IdempotentConsumer(
        database=database_handle,
        consumer_name="fulfillment",
        max_retries=3,
        base_routing_backoff=0.01,
        on_routing_failure=crashing_recovery_hook,
    )

    async def fail_handler(payload: dict[str, Any], ctx: RequestContext) -> None:
        raise RuntimeError("Transient error")

    consumer.register_handler("OrderPlaced", fail_handler)

    payload = {
        "event_id": str(uuid.uuid4()),
        "event_type": "OrderPlaced",
        "schema_version": 1,
        "tenant_id": str(tenant_id),
        "correlation_id": "c-hook-crash",
        "payload": {},
    }
    msg = FakeIncomingMessage(body=json.dumps(payload).encode("utf-8"), channel=channel)

    # Must catch hook error without crashing or prematurely acknowledging
    await consumer.process_message(msg)
    assert msg.acked is False
    assert msg.nacked is True
