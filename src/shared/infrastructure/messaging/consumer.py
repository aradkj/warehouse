"""Idempotent RabbitMQ event consumer wrapper.

Receives RabbitMQ messages, validates the event envelope, enforces consumer inbox
idempotency inside PostgreSQL transactions with atomic leasing, executes business handlers,
commits state before acknowledging RabbitMQ, and provides native RabbitMQ exponential
backoff retry with jitter and DLQ routing for poison messages.
"""

from __future__ import annotations

import json
import logging
import random
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import aio_pika

from shared.application.context import ActorType, RequestContext
from shared.domain.identifiers import TenantId
from shared.infrastructure.db.session import Database
from shared.infrastructure.messaging.inbox import (
    ClaimStatus,
    InboxOwnershipLostError,
    SqlAlchemyInboxRepository,
)
from shared.infrastructure.messaging.topology import EXCHANGE_DLX, EXCHANGE_RETRY

logger = logging.getLogger(__name__)

HandlerFunc = Callable[[dict[str, Any], RequestContext], Awaitable[Any]]


def _header_to_str(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, (bytes, bytearray)):
        return val.decode("utf-8", errors="replace")
    return str(val)


class PermanentError(Exception):
    """Errors that should NOT be retried.

    Examples: malformed payload, unsupported version, schema violation.
    """


class MalformedMessageError(PermanentError):
    """Payload is malformed or not valid JSON."""


class InvalidEventEnvelopeError(PermanentError):
    """Required envelope metadata (event_id, event_type, tenant_id) is missing or invalid."""


class UnsupportedSchemaVersionError(PermanentError):
    """Schema version is higher than supported by this consumer."""


class IdempotentConsumer:
    """Wrapper that guarantees idempotent, tenant-safe message processing with retry and DLQ."""

    __slots__ = (
        "_consumer_name",
        "_database",
        "_handlers",
        "_initial_retry_delay",
        "_lease_duration_seconds",
        "_max_retries",
        "_max_retry_delay",
        "_queue_name",
        "_retry_jitter",
        "_retry_multiplier",
    )

    def __init__(
        self,
        database: Database,
        consumer_name: str,
        *,
        queue_name: str | None = None,
        lease_duration_seconds: float = 30.0,
        max_retries: int = 3,
        initial_retry_delay: float = 1.0,
        max_retry_delay: float = 30.0,
        retry_multiplier: float = 2.0,
        retry_jitter: float = 0.5,
    ) -> None:
        self._database = database
        self._consumer_name = consumer_name
        self._queue_name = queue_name or f"arad.{consumer_name}"
        self._lease_duration_seconds = lease_duration_seconds
        self._max_retries = max_retries
        self._initial_retry_delay = initial_retry_delay
        self._max_retry_delay = max_retry_delay
        self._retry_multiplier = retry_multiplier
        self._retry_jitter = retry_jitter
        self._handlers: dict[str, HandlerFunc] = {}

    def register_handler(self, event_type: str, handler: HandlerFunc) -> None:
        """Register a handler callback for a specific event_type."""
        self._handlers[event_type] = handler

    async def process_message(self, message: aio_pika.abc.AbstractIncomingMessage) -> None:
        """Process an incoming RabbitMQ message idempotently."""
        now = datetime.now(UTC)

        # 1. Parse JSON body
        try:
            body = json.loads(message.body.decode("utf-8"))
        except Exception as exc:
            logger.error("Consumer %s: Malformed JSON payload: %s", self._consumer_name, exc)
            await self._route_to_dlq(message, {}, reason=f"Malformed JSON: {exc}")
            await message.ack()
            return

        # 2. Validate envelope fields
        event_id = body.get("event_id") or message.message_id
        event_type = body.get("event_type")
        tenant_id_str = body.get("tenant_id")
        schema_version = body.get("schema_version", 1)

        if not event_id or not event_type or not tenant_id_str:
            err = (
                f"Missing required envelope fields: event_id={event_id}, "
                f"event_type={event_type}, tenant_id={tenant_id_str}"
            )
            logger.error("Consumer %s: %s", self._consumer_name, err)
            await self._route_to_dlq(message, body, reason=err)
            await message.ack()
            return

        try:
            tenant_uuid = uuid.UUID(str(tenant_id_str))
        except ValueError as exc:
            err = f"Invalid tenant_id UUID format: {tenant_id_str} ({exc})"
            logger.error("Consumer %s: %s", self._consumer_name, err)
            await self._route_to_dlq(message, body, reason=err)
            await message.ack()
            return

        # Check schema version compatibility (Phase 2 contract supports schema_version 1)
        if not isinstance(schema_version, int) or schema_version < 1 or schema_version > 1:
            err = f"Unsupported schema version {schema_version} (supported: 1)"
            logger.error("Consumer %s: %s", self._consumer_name, err)
            await self._route_to_dlq(message, body, reason=err)
            await message.ack()
            return

        raw_header_tenant = message.headers.get("x-tenant-id") if message.headers else None
        header_tenant = _header_to_str(raw_header_tenant)
        if header_tenant is not None and header_tenant != str(tenant_id_str):
            err = f"Tenant ID header mismatch: header='{header_tenant}' vs body='{tenant_id_str}'"
            logger.error("Consumer %s: %s", self._consumer_name, err)
            await self._route_to_dlq(message, body, reason=err)
            await message.ack()
            return

        raw_correlation = message.headers.get("x-correlation-id") if message.headers else None
        raw_causation = message.headers.get("x-causation-id") if message.headers else None
        correlation_id: str = (
            _header_to_str(raw_correlation)
            or str(body.get("correlation_id") or uuid.uuid4())
        )
        causation_id: str | None = (
            _header_to_str(raw_causation)
            or (
                str(body["causation_id"])
                if body.get("causation_id") is not None
                else str(event_id)
            )
        )

        handler = self._handlers.get(event_type)
        if handler is None:
            logger.debug(
                "Consumer %s: No handler registered for event_type %s",
                self._consumer_name,
                event_type,
            )
            await message.ack()
            return

        from identity.domain.principal import Principal

        raw_actor_id = message.headers.get("x-actor-id") if message.headers else None
        actor_id: str = _header_to_str(raw_actor_id) or f"consumer:{self._consumer_name}"
        raw_actor_type = message.headers.get("x-actor-type") if message.headers else None
        actor_type_str: str = _header_to_str(raw_actor_type) or "SYSTEM"
        try:
            actor_type = ActorType(str(actor_type_str))
        except ValueError:
            actor_type = ActorType.SYSTEM

        principal = Principal(
            id=str(actor_id),
            type=actor_type,
            active_tenant_id=TenantId(tenant_uuid),
            roles=("consumer",),
            permissions=frozenset({"*"}),
        )

        context = RequestContext(
            tenant_id=TenantId(tenant_uuid),
            actor_id=str(actor_id),
            actor_type=actor_type,
            correlation_id=correlation_id,
            causation_id=causation_id,
            received_at=now,
            principal=principal,
        )

        worker_id = f"{self._consumer_name}-{uuid.uuid4().hex[:8]}"

        # 3. Durable processing claim (Step 1 of safe pattern)
        async with self._database.new_scope() as claim_scope:
            inbox_repo = SqlAlchemyInboxRepository(claim_scope.session)
            claim_res = await inbox_repo.claim_lease(
                message_id=str(event_id),
                consumer_name=self._consumer_name,
                tenant_id=tenant_uuid,
                event_type=event_type,
                worker_id=worker_id,
                now=now,
                lease_duration_seconds=self._lease_duration_seconds,
            )
            await claim_scope.commit()

        if claim_res.status == ClaimStatus.ALREADY_PROCESSED:
            logger.info(
                "Consumer %s: Message %s already processed; acknowledging duplicate.",
                self._consumer_name,
                event_id,
            )
            await message.ack()
            return

        if claim_res.status == ClaimStatus.LOCKED_BY_OTHER:
            logger.info(
                "Consumer %s: Message %s is leased by worker %s; skipping duplicate.",
                self._consumer_name,
                event_id,
                claim_res.worker_id,
            )
            await message.ack()
            return

        # 4. Business execution + atomic inbox completion (Step 2 of safe pattern)
        async with self._database.new_scope(tenant_id=str(tenant_uuid)) as scope:
            inbox_repo = SqlAlchemyInboxRepository(scope.session)
            try:
                await handler(body.get("payload", {}), context)
                # Mark PROCESSED in the SAME transaction as the business effect
                await inbox_repo.mark_processed(
                    message_id=str(event_id),
                    consumer_name=self._consumer_name,
                    worker_id=worker_id,
                    now=datetime.now(UTC),
                )
                await scope.commit()
                logger.info(
                    "Consumer %s: Successfully processed message %s (%s)",
                    self._consumer_name,
                    event_id,
                    event_type,
                )
                await message.ack()
                return
            except Exception as exc:
                logger.error(
                    "Consumer %s: Processing failed for message %s (%s): %s",
                    self._consumer_name,
                    event_id,
                    event_type,
                    exc,
                    exc_info=True,
                )
                await scope.rollback()

                # Step 3: Durably record failure in inbox
                async with self._database.new_scope() as fail_scope:
                    fail_inbox = SqlAlchemyInboxRepository(fail_scope.session)
                    try:
                        await fail_inbox.record_failure(
                            message_id=str(event_id),
                            consumer_name=self._consumer_name,
                            worker_id=worker_id,
                            error=str(exc),
                            now=datetime.now(UTC),
                        )
                        await fail_scope.commit()
                    except InboxOwnershipLostError:
                        logger.warning(
                            "Consumer %s: Worker %s lost lease before recording failure for %s",
                            self._consumer_name,
                            worker_id,
                            event_id,
                        )

                # Step 4: Handle retry or DLQ routing
                await self._handle_failure(message, body, exc)

    async def _handle_failure(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        body: dict[str, Any],
        exc: Exception,
    ) -> None:
        """Route to DLQ if permanent or max retries exceeded; else route to retry queue."""
        if isinstance(exc, PermanentError):
            logger.error(
                "Consumer %s: Permanent failure for message %s: %s -> routing to DLQ",
                self._consumer_name,
                message.message_id,
                exc,
            )
            await self._route_to_dlq(message, body, reason=str(exc))
            await message.ack()
            return

        raw_count = message.headers.get("x-retry-count", 1) if message.headers else 1
        try:
            attempt = int(str(raw_count))
        except (ValueError, TypeError):
            attempt = 1

        if attempt < self._max_retries:
            delay = min(
                self._max_retry_delay,
                self._initial_retry_delay * (self._retry_multiplier ** max(0, attempt - 1)),
            )
            if self._retry_jitter > 0:
                delay += random.uniform(0.0, self._retry_jitter)  # noqa: S311
            delay = max(0.1, delay)

            logger.warning(
                "Consumer %s: Transient failure on attempt %d/%d for message %s. Delay: %.2fs",
                self._consumer_name,
                attempt,
                self._max_retries,
                message.message_id,
                delay,
            )
            await self._route_to_retry(
                message, body, next_attempt=attempt + 1, delay_seconds=delay, error=str(exc)
            )
            await message.ack()
        else:
            logger.error(
                "Consumer %s: Retries exhausted (%d attempts) for message %s -> routing to DLQ",
                self._consumer_name,
                attempt,
                message.message_id,
            )
            await self._route_to_dlq(
                message, body, reason=f"Retries exhausted ({self._max_retries}). Last error: {exc}"
            )
            await message.ack()

    async def _route_to_retry(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        body: dict[str, Any],
        *,
        next_attempt: int,
        delay_seconds: float,
        error: str,
    ) -> None:
        """Publish message to retry exchange with expiration delay and incremented retry count."""
        headers = dict(message.headers or {})
        headers["x-retry-count"] = next_attempt
        headers["x-last-error"] = error[:1000]
        headers["x-original-queue"] = self._queue_name

        retry_msg = aio_pika.Message(
            body=message.body,
            headers=headers,
            content_type=message.content_type,
            content_encoding=message.content_encoding,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            expiration=timedelta(seconds=delay_seconds),
            message_id=message.message_id,
            correlation_id=message.correlation_id,
        )
        try:
            channel: Any = message.channel
            exchange = await channel.get_exchange(EXCHANGE_RETRY)
            await exchange.publish(retry_msg, routing_key=self._queue_name)
        except Exception as pub_exc:
            logger.error(
                "Consumer %s: Failed to publish retry message for %s: %s",
                self._consumer_name,
                message.message_id,
                pub_exc,
            )

    async def _route_to_dlq(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        body: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        """Publish poison or exhausted message to Dead Letter Queue via EXCHANGE_DLX."""
        headers = dict(message.headers or {})
        headers["x-death-reason"] = reason[:1000]
        headers["x-original-queue"] = self._queue_name
        headers["x-failed-at"] = datetime.now(UTC).isoformat()

        dlq_routing_key = f"{self._queue_name}.dlq"
        dlq_msg = aio_pika.Message(
            body=message.body,
            headers=headers,
            content_type=message.content_type,
            content_encoding=message.content_encoding,
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=message.message_id,
            correlation_id=message.correlation_id,
        )
        try:
            channel: Any = message.channel
            dlx = await channel.get_exchange(EXCHANGE_DLX)
            await dlx.publish(dlq_msg, routing_key=dlq_routing_key)
        except Exception as pub_exc:
            logger.error(
                "Consumer %s: Failed to publish to DLQ for %s: %s",
                self._consumer_name,
                message.message_id,
                pub_exc,
            )


__all__ = [
    "HandlerFunc",
    "IdempotentConsumer",
    "InvalidEventEnvelopeError",
    "MalformedMessageError",
    "PermanentError",
    "UnsupportedSchemaVersionError",
]
