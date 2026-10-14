"""RabbitMQ event publisher with publisher confirmations.

Publishes OutboxEvent records as persistent messages to the durable RabbitMQ exchange.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import aio_pika

from shared.infrastructure.db.outbox import OutboxEvent
from shared.infrastructure.messaging.topology import EXCHANGE_EVENTS, event_type_to_routing_key

logger = logging.getLogger(__name__)


class RabbitMQPublisher:
    """Async RabbitMQ publisher with mandatory delivery and publisher confirmation."""

    __slots__ = ("_connection", "_exchange", "_url")

    def __init__(self, url: str) -> None:
        self._url = url
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None

    async def connect(self) -> None:
        """Establish connection and declare exchange."""
        if self._connection is None or self._connection.is_closed:
            self._connection = await aio_pika.connect_robust(self._url)
            channel = await self._connection.channel(publisher_confirms=True)
            self._exchange = await channel.declare_exchange(
                EXCHANGE_EVENTS, type=aio_pika.ExchangeType.TOPIC, durable=True
            )

    async def close(self) -> None:
        """Close connection."""
        if self._connection is not None and not self._connection.is_closed:
            await self._connection.close()
            self._connection = None
            self._exchange = None

    async def publish(self, event: OutboxEvent) -> None:
        """Publish a single outbox event with publisher confirmation."""
        if self._exchange is None:
            await self.connect()

        routing_key = event_type_to_routing_key(event.event_type)

        envelope: dict[str, Any] = {
            "event_id": str(event.id),
            "event_type": event.event_type,
            "schema_version": event.schema_version,
            "tenant_id": str(event.tenant_id),
            "aggregate_type": event.aggregate_type,
            "aggregate_id": str(event.aggregate_id),
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id or str(event.id),
            "occurred_at": event.occurred_at.isoformat(),
            "payload": event.payload,
        }

        body = json.dumps(envelope).encode("utf-8")
        msg_headers: dict[str, Any] = {
            "x-tenant-id": str(event.tenant_id),
            "x-correlation-id": event.correlation_id,
            "x-causation-id": event.causation_id or str(event.id),
            "x-schema-version": event.schema_version,
            "x-event-type": event.event_type,
        }
        if event.headers and isinstance(event.headers, dict):
            if "actor_id" in event.headers:
                msg_headers["x-actor-id"] = str(event.headers["actor_id"])
            if "actor_type" in event.headers:
                msg_headers["x-actor-type"] = str(event.headers["actor_type"])

        message = aio_pika.Message(
            body=body,
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            headers=msg_headers,
            message_id=str(event.id),
            timestamp=event.occurred_at,
        )

        assert self._exchange is not None
        await self._exchange.publish(message, routing_key=routing_key)
        logger.debug(
            "Published outbox event %s (%s) with routing_key %s",
            event.id,
            event.event_type,
            routing_key,
        )


__all__ = ["RabbitMQPublisher"]
