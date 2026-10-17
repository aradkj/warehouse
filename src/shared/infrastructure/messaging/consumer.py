"""Idempotent RabbitMQ event consumer wrapper.

Receives RabbitMQ messages, validates the event envelope, enforces consumer inbox
idempotency inside PostgreSQL transactions with atomic leasing, executes business handlers,
commits state before acknowledging RabbitMQ, and provides native RabbitMQ exponential
backoff retry with jitter and DLQ routing for poison messages.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import aio_pika
import pamqp.commands as pamqp_cmd
from aiormq.abc import DeliveredMessage
from aiormq.exceptions import DeliveryError, PublishError

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


class PublishConfirmationError(RuntimeError):
    """Raised when broker negatively acknowledges or rejects a publication."""


class UnroutableMessageError(RuntimeError):
    """Raised when a mandatory message cannot be routed to any destination queue."""


class RoutingFailureError(RuntimeError):
    """Raised when publication to retry exchange or dead-letter exchange fails."""


class IdempotentConsumer:
    """Wrapper that guarantees idempotent, tenant-safe message processing with retry and DLQ."""

    __slots__ = (
        "_base_routing_backoff",
        "_consecutive_routing_failures",
        "_consumer_name",
        "_database",
        "_handlers",
        "_initial_retry_delay",
        "_last_routing_error_log_time",
        "_lease_duration_seconds",
        "_max_retries",
        "_max_retry_delay",
        "_max_routing_backoff",
        "_on_routing_failure",
        "_publish_timeout",
        "_publisher_channel",
        "_queue_name",
        "_retry_jitter",
        "_retry_multiplier",
        "_routing_backoff_jitter",
        "_routing_error_log_interval",
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
        publisher_channel: aio_pika.abc.AbstractChannel | None = None,
        publish_timeout: float = 10.0,
        base_routing_backoff: float = 0.5,
        max_routing_backoff: float = 10.0,
        routing_backoff_jitter: float = 0.2,
        routing_error_log_interval: float = 5.0,
        on_routing_failure: (
            Callable[[aio_pika.abc.AbstractIncomingMessage, Exception], Awaitable[None]] | None
        ) = None,
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
        self._publisher_channel = publisher_channel
        self._publish_timeout = publish_timeout
        self._base_routing_backoff = base_routing_backoff
        self._max_routing_backoff = max_routing_backoff
        self._routing_backoff_jitter = routing_backoff_jitter
        self._routing_error_log_interval = routing_error_log_interval
        self._on_routing_failure = on_routing_failure
        self._consecutive_routing_failures = 0
        self._last_routing_error_log_time = 0.0
        self._handlers: dict[str, HandlerFunc] = {}

    def get_consecutive_routing_failures(self) -> int:
        return self._consecutive_routing_failures

    def reset_routing_failures(self) -> None:
        self._consecutive_routing_failures = 0

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
            await self._safe_route_to_dlq(message, {}, reason=f"Malformed JSON: {exc}")
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
            await self._safe_route_to_dlq(message, body, reason=err)
            return

        try:
            tenant_uuid = uuid.UUID(str(tenant_id_str))
        except ValueError as exc:
            err = f"Invalid tenant_id UUID format: {tenant_id_str} ({exc})"
            logger.error("Consumer %s: %s", self._consumer_name, err)
            await self._safe_route_to_dlq(message, body, reason=err)
            return

        # Check schema version compatibility (Phase 2 contract supports schema_version 1)
        if not isinstance(schema_version, int) or schema_version < 1 or schema_version > 1:
            err = f"Unsupported schema version {schema_version} (supported: 1)"
            logger.error("Consumer %s: %s", self._consumer_name, err)
            await self._safe_route_to_dlq(message, body, reason=err)
            return

        raw_header_tenant = message.headers.get("x-tenant-id") if message.headers else None
        header_tenant = _header_to_str(raw_header_tenant)
        if header_tenant is not None and header_tenant != str(tenant_id_str):
            err = f"Tenant ID header mismatch: header='{header_tenant}' vs body='{tenant_id_str}'"
            logger.error("Consumer %s: %s", self._consumer_name, err)
            await self._safe_route_to_dlq(message, body, reason=err)
            return

        raw_correlation = message.headers.get("x-correlation-id") if message.headers else None
        raw_causation = message.headers.get("x-causation-id") if message.headers else None
        correlation_id: str = _header_to_str(raw_correlation) or str(
            body.get("correlation_id") or uuid.uuid4()
        )
        causation_id: str | None = _header_to_str(raw_causation) or (
            str(body["causation_id"]) if body.get("causation_id") is not None else str(event_id)
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
            logger.warning(
                "Consumer %s: Message %s is leased by worker %s; deferring via retry queue.",
                self._consumer_name,
                event_id,
                claim_res.worker_id,
            )
            raw_count = message.headers.get("x-retry-count", 1) if message.headers else 1
            try:
                attempt = int(str(raw_count))
            except (ValueError, TypeError):
                attempt = 1
            await self._safe_route_to_retry(
                message,
                body,
                next_attempt=attempt,
                delay_seconds=min(self._lease_duration_seconds, 10.0),
                error=f"Leased by worker {claim_res.worker_id}",
            )
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

    def _verify_confirmation(self, confirmation: Any) -> None:
        """Verify broker confirmation frame according to AMQP publisher confirms.

        Fail-closed design: ONLY documented positive confirmation (Basic.Ack)
        is accepted as success. Any None, negative acknowledgment, unroutable return,
        or unexpected object raises an exception to prevent source message ACK.
        """
        # 1. Reject negative acknowledgment or rejection from broker
        if isinstance(confirmation, (pamqp_cmd.Basic.Nack, pamqp_cmd.Basic.Reject)):
            raise PublishConfirmationError(
                f"Broker negatively acknowledged publication with {type(confirmation).__name__}"
            )

        # 2. Reject unroutable message returned by broker (when on_return_raises=False)
        if isinstance(confirmation, (DeliveredMessage, pamqp_cmd.Basic.Return)):
            raise UnroutableMessageError(
                "Message returned as unroutable by broker (no bound queue for mandatory publish)"
            )

        # 3. Reject None (unconfirmed publication or publisher_confirms disabled)
        if confirmation is None:
            raise PublishConfirmationError(
                "Publication confirmation missing or unconfirmed: broker returned None"
            )

        # 4. Strictly require positive acknowledgment (Basic.Ack)
        if not isinstance(confirmation, pamqp_cmd.Basic.Ack):
            raise PublishConfirmationError(
                f"Ambiguous or unexpected confirmation result from broker: "
                f"{type(confirmation).__name__} ({confirmation!r})"
            )

    async def _handle_routing_failure(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        error: Exception,
        destination: str,
    ) -> None:
        """Handle persistent routing failure without immediate hot loop or log flooding.

        Preserves source message via nack(requeue=True) and applies bounded exponential
        backoff with jitter before returning control to the consumer loop.
        """
        now = time.monotonic()
        self._consecutive_routing_failures += 1

        # 1. Rate-limited logging: avoid flooding logs
        should_log = (
            self._consecutive_routing_failures == 1
            or (now - self._last_routing_error_log_time) >= self._routing_error_log_interval
        )
        if should_log:
            self._last_routing_error_log_time = now
            logger.critical(
                "Consumer %s: Failed to route message %s to %s (consecutive failures: %d): %s. "
                "Preserving message without ack.",
                self._consumer_name,
                message.message_id,
                destination,
                self._consecutive_routing_failures,
                error,
            )
        else:
            logger.debug(
                "Consumer %s: Suppressing repeated routing failure log for message %s (count: %d)",
                self._consumer_name,
                message.message_id,
                self._consecutive_routing_failures,
            )

        # 2. Invoke external recovery hook if configured (e.g. topology re-declaration)
        if self._on_routing_failure is not None:
            try:
                await self._on_routing_failure(message, error)
            except Exception as hook_err:
                logger.error(
                    "Consumer %s: on_routing_failure hook error: %s",
                    self._consumer_name,
                    hook_err,
                )

        # 3. Calculate bounded exponential backoff with jitter to eliminate immediate hot loop
        backoff = min(
            self._max_routing_backoff,
            self._base_routing_backoff * (2 ** min(6, self._consecutive_routing_failures - 1)),
        )
        if self._routing_backoff_jitter > 0:
            backoff += random.uniform(0.0, self._routing_backoff_jitter)  # noqa: S311

        # 4. Hold message unacknowledged during backoff to prevent other consumers sharing
        # the same queue from immediately redelivering it in a cross-consumer hot loop.
        try:
            await asyncio.sleep(backoff)
        except asyncio.CancelledError:
            # Cancellation/shutdown during backoff: attempt to nack source message
            # before propagating cancellation so broker can requeue cleanly.
            try:
                await asyncio.shield(message.nack(requeue=True))
            except Exception as cancel_nack_err:
                logger.debug(
                    "Consumer %s: Failed to nack during cancellation: %s",
                    self._consumer_name,
                    cancel_nack_err,
                )
            raise

        # 5. Requeue source message so it remains recoverable after the backoff window
        try:
            await message.nack(requeue=True)
        except Exception as nack_err:
            logger.error(
                "Consumer %s: Failed to nack unrouted message %s (channel closed?): %s",
                self._consumer_name,
                message.message_id,
                nack_err,
            )

    async def _safe_route_to_dlq(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        body: dict[str, Any],
        *,
        reason: str,
    ) -> None:
        """Publish to DLQ and ACK source message only upon verified publication."""
        try:
            await self._route_to_dlq(message, body, reason=reason)
            await message.ack()
            self._consecutive_routing_failures = 0
        except Exception as dlq_err:
            await self._handle_routing_failure(message, dlq_err, destination="DLQ")

    async def _safe_route_to_retry(
        self,
        message: aio_pika.abc.AbstractIncomingMessage,
        body: dict[str, Any],
        *,
        next_attempt: int,
        delay_seconds: float,
        error: str,
    ) -> None:
        """Publish to retry queue and ACK source message only upon verified publication."""
        try:
            await self._route_to_retry(
                message, body, next_attempt=next_attempt, delay_seconds=delay_seconds, error=error
            )
            await message.ack()
            self._consecutive_routing_failures = 0
        except Exception as retry_err:
            await self._handle_routing_failure(message, retry_err, destination="retry exchange")

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
            await self._safe_route_to_dlq(message, body, reason=str(exc))
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
            await self._safe_route_to_retry(
                message, body, next_attempt=attempt + 1, delay_seconds=delay, error=str(exc)
            )
        else:
            logger.error(
                "Consumer %s: Retries exhausted (%d attempts) for message %s -> routing to DLQ",
                self._consumer_name,
                attempt,
                message.message_id,
            )
            reason = f"Retries exhausted ({self._max_retries}). Last error: {exc}"
            await self._safe_route_to_dlq(message, body, reason=reason)

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
        channel: Any = self._publisher_channel or message.channel
        exchange = await channel.get_exchange(EXCHANGE_RETRY)
        try:
            confirmation = await exchange.publish(
                retry_msg,
                routing_key=self._queue_name,
                mandatory=True,
                timeout=self._publish_timeout,
            )
            self._verify_confirmation(confirmation)
        except (PublishError, DeliveryError) as err:
            raise PublishConfirmationError(str(err)) from err

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
        channel: Any = self._publisher_channel or message.channel
        dlx = await channel.get_exchange(EXCHANGE_DLX)
        try:
            confirmation = await dlx.publish(
                dlq_msg,
                routing_key=dlq_routing_key,
                mandatory=True,
                timeout=self._publish_timeout,
            )
            self._verify_confirmation(confirmation)
        except (PublishError, DeliveryError) as err:
            raise PublishConfirmationError(str(err)) from err


__all__ = [
    "HandlerFunc",
    "IdempotentConsumer",
    "InvalidEventEnvelopeError",
    "MalformedMessageError",
    "PermanentError",
    "PublishConfirmationError",
    "RoutingFailureError",
    "UnroutableMessageError",
    "UnsupportedSchemaVersionError",
]
