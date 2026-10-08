"""Transactional Outbox repository and event dispatcher implementation.

The transactional outbox is the reliability boundary between PostgreSQL and RabbitMQ.
When a business aggregate commits state changes, domain events are written to the
``outbox_events`` table in the SAME database transaction.

If the transaction rolls back: no outbox event exists.
If the transaction commits: the outbox event is durably stored.
"""

from __future__ import annotations

import logging
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Select, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.application.context import RequestContext
from shared.application.events import EventDispatcher
from shared.domain.events import DomainEvent
from shared.infrastructure.db.session import SqlAlchemyTransactionScope
from shared.infrastructure.db.tables import outbox_events

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    """Read representation of an outbox event."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    event_type: str
    schema_version: int
    aggregate_type: str
    aggregate_id: uuid.UUID
    payload: dict[str, Any]
    headers: dict[str, Any]
    correlation_id: str
    causation_id: str | None
    occurred_at: datetime
    status: str
    attempt_count: int
    available_at: datetime
    claimed_at: datetime | None = None
    claimed_by: str | None = None
    published_at: datetime | None = None
    last_error: str | None = None


def resolve_aggregate_info(event: DomainEvent) -> tuple[str, uuid.UUID]:
    """Infer (aggregate_type, aggregate_id) from a domain event's attributes."""
    if hasattr(event, "order_id") and event.order_id is not None:
        return "order", uuid.UUID(str(event.order_id))
    if hasattr(event, "reservation_id") and event.reservation_id is not None:
        return "reservation", uuid.UUID(str(event.reservation_id))
    if hasattr(event, "inventory_item_id") and event.inventory_item_id is not None:
        return "inventory_item", uuid.UUID(str(event.inventory_item_id))
    if hasattr(event, "product_id") and event.product_id is not None:
        return "product", uuid.UUID(str(event.product_id))
    if hasattr(event, "sku_id") and event.sku_id is not None:
        return "sku", uuid.UUID(str(event.sku_id))
    if hasattr(event, "warehouse_id") and event.warehouse_id is not None:
        return "warehouse", uuid.UUID(str(event.warehouse_id))
    if hasattr(event, "tenant_id") and event.tenant_id is not None:
        return "tenant", uuid.UUID(str(event.tenant_id))
    return "domain_event", event.event_id


class SqlAlchemyOutboxEventDispatcher(EventDispatcher):
    """EventDispatcher that writes domain events into outbox_events in the active session."""

    __slots__ = ("_scope",)

    def __init__(self, scope: SqlAlchemyTransactionScope) -> None:
        self._scope = scope

    async def dispatch(
        self,
        events: tuple[DomainEvent, ...],
        *,
        context: RequestContext | None = None,
    ) -> None:
        if not events:
            return
        session = self._scope.session
        now = datetime.now(UTC)

        for event in events:
            tenant_id_val = getattr(event, "tenant_id", None)
            if tenant_id_val is None:
                continue
            tenant_uuid = uuid.UUID(str(tenant_id_val))

            agg_type, agg_id = resolve_aggregate_info(event)
            envelope = event.envelope()
            payload = event.payload()

            if context is not None:
                correlation_id = context.correlation_id
                causation_id = context.causation_id or context.correlation_id
            else:
                correlation_id = str(envelope.get("correlation_id") or uuid.uuid4())
                causation_id = str(envelope.get("causation_id") or event.event_id)

            headers = {
                "correlation_id": correlation_id,
                "causation_id": causation_id,
                "event_name": event.event_name,
            }

            stmt = insert(outbox_events).values(
                id=event.event_id,
                tenant_id=tenant_uuid,
                event_type=event.event_name,
                schema_version=event.schema_version,
                aggregate_type=agg_type,
                aggregate_id=agg_id,
                payload=payload,
                headers=headers,
                correlation_id=correlation_id,
                causation_id=causation_id,
                occurred_at=event.occurred_at,
                status="PENDING",
                attempt_count=0,
                available_at=now,
                created_at=now,
                updated_at=now,
            )
            await session.execute(stmt)


class SqlAlchemyOutboxRepository:
    """Outbox queue management: batch claim, confirmation, retries, recovery."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim_batch(
        self,
        *,
        batch_size: int,
        claim_timeout_seconds: float,
        claimed_by: str,
        now: datetime,
    ) -> tuple[OutboxEvent, ...]:
        """Claim a batch of PENDING or expired CLAIMED events using FOR UPDATE SKIP LOCKED."""
        stale_threshold = now - timedelta(seconds=claim_timeout_seconds)

        # 1. Lock available candidates
        lock_stmt: Select[Any] = (
            select(outbox_events.c.id)
            .where(
                (
                    (outbox_events.c.status == "PENDING")
                    & (outbox_events.c.available_at <= now)
                )
                | (
                    (outbox_events.c.status == "CLAIMED")
                    & (outbox_events.c.claimed_at <= stale_threshold)
                )
            )
            .order_by(outbox_events.c.available_at.asc(), outbox_events.c.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        result = await self._session.execute(lock_stmt)
        event_ids = [row[0] for row in result.all()]

        if not event_ids:
            return ()

        # 2. Update state to CLAIMED
        update_stmt = (
            update(outbox_events)
            .where(outbox_events.c.id.in_(event_ids))
            .values(
                status="CLAIMED",
                claimed_at=now,
                claimed_by=claimed_by,
                attempt_count=outbox_events.c.attempt_count + 1,
                updated_at=now,
            )
            .returning(*outbox_events.c)
        )
        updated_rows = (await self._session.execute(update_stmt)).mappings().all()

        return tuple(
            OutboxEvent(
                id=row["id"],
                tenant_id=row["tenant_id"],
                event_type=row["event_type"],
                schema_version=row["schema_version"],
                aggregate_type=row["aggregate_type"],
                aggregate_id=row["aggregate_id"],
                payload=dict(row["payload"]),
                headers=dict(row["headers"]),
                correlation_id=row["correlation_id"],
                causation_id=row["causation_id"],
                occurred_at=row["occurred_at"],
                status=row["status"],
                attempt_count=row["attempt_count"],
                available_at=row["available_at"],
                claimed_at=row["claimed_at"],
                claimed_by=row["claimed_by"],
                published_at=row["published_at"],
                last_error=row["last_error"],
            )
            for row in updated_rows
        )

    async def mark_published(self, event_id: uuid.UUID, *, now: datetime) -> None:
        """Mark an event as successfully published to RabbitMQ."""
        stmt = (
            update(outbox_events)
            .where(outbox_events.c.id == event_id)
            .values(
                status="PUBLISHED",
                published_at=now,
                claimed_at=None,
                claimed_by=None,
                updated_at=now,
            )
        )
        await self._session.execute(stmt)

    async def record_failure(
        self,
        event_id: uuid.UUID,
        *,
        error: str,
        max_attempts: int,
        now: datetime,
        backoff_seconds: float = 1.0,
        max_delay_seconds: float = 60.0,
        jitter_seconds: float = 0.5,
    ) -> None:
        """Record publication failure; retry with backoff and jitter or mark FAILED
        if max_attempts reached.
        """
        stmt = select(outbox_events.c.attempt_count).where(outbox_events.c.id == event_id)
        current_attempts = (await self._session.execute(stmt)).scalar_one_or_none() or 1

        if current_attempts >= max_attempts:
            new_status = "FAILED"
            available_at = now
        else:
            new_status = "PENDING"
            delay = min(max_delay_seconds, backoff_seconds * (2 ** max(0, current_attempts - 1)))
            if jitter_seconds > 0:
                delay += random.uniform(0.0, jitter_seconds)  # noqa: S311
            delay = max(0.0, delay)
            available_at = now + timedelta(seconds=delay)

        update_stmt = (
            update(outbox_events)
            .where(outbox_events.c.id == event_id)
            .values(
                status=new_status,
                available_at=available_at,
                last_error=error[:2000],
                claimed_at=None,
                claimed_by=None,
                updated_at=now,
            )
        )
        await self._session.execute(update_stmt)


__all__ = [
    "OutboxEvent",
    "SqlAlchemyOutboxEventDispatcher",
    "SqlAlchemyOutboxRepository",
    "resolve_aggregate_info",
]
