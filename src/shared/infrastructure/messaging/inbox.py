"""Consumer Inbox implementation for message-level idempotency.

Prevents duplicate execution when the same message is delivered more than once to a consumer.
Each consumer uses its own consumer_name so multiple distinct services can process the same message once each.
Provides atomic claim/lease semantics, ownership tracking, and crash/lease recovery.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from shared.infrastructure.db.tables import inbox_messages

logger = logging.getLogger(__name__)


class ClaimStatus(StrEnum):
    """Result status of an inbox claim attempt."""

    GRANTED = "GRANTED"
    ALREADY_PROCESSED = "ALREADY_PROCESSED"
    LOCKED_BY_OTHER = "LOCKED_BY_OTHER"


class InboxOwnershipLostError(Exception):
    """Raised when a worker attempts to finalize an inbox message whose lease was lost or reclaimed."""


class ClaimResult:
    """Encapsulates claim outcome and worker ownership."""

    __slots__ = ("status", "worker_id")

    def __init__(self, status: ClaimStatus, worker_id: str) -> None:
        self.status = status
        self.worker_id = worker_id

    @property
    def granted(self) -> bool:
        return self.status == ClaimStatus.GRANTED

    def __bool__(self) -> bool:
        return self.granted

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, bool):
            return self.granted is other
        if isinstance(other, ClaimStatus):
            return self.status == other
        return super().__eq__(other)

    def __repr__(self) -> str:
        return f"<ClaimResult status={self.status.value} worker_id={self.worker_id}>"


class SqlAlchemyInboxRepository:
    """Inbox repository adapter enforcing consumer idempotency with lease-based concurrency."""

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_already_processed(
        self, *, message_id: str, consumer_name: str
    ) -> bool:
        """Return True if message has already been successfully processed by consumer_name."""
        stmt = select(inbox_messages.c.status).where(
            inbox_messages.c.message_id == message_id,
            inbox_messages.c.consumer_name == consumer_name,
        )
        status = (await self._session.execute(stmt)).scalar_one_or_none()
        return status == "PROCESSED"

    async def claim_lease(
        self,
        *,
        message_id: str,
        consumer_name: str,
        tenant_id: uuid.UUID,
        event_type: str,
        worker_id: str,
        now: datetime,
        lease_duration_seconds: float = 30.0,
    ) -> ClaimResult:
        """Atomically claim a message lease for processing.

        Guarantees:
        - If row doesn't exist: inserts with status PROCESSING, locked_by=worker_id, returns GRANTED.
        - If row is PROCESSED: returns ALREADY_PROCESSED.
        - If row is PROCESSING with active lease (lease_until > now): returns LOCKED_BY_OTHER.
        - If row is PROCESSING with expired lease (lease_until <= now) or FAILED or RECEIVED:
          atomically updates locked_by=worker_id, lease_until=now+ttl, returns GRANTED.
        - If two workers race to claim/reclaim: PostgreSQL row lock ensures exactly ONE worker succeeds.
        """
        lease_until = now + timedelta(seconds=lease_duration_seconds)

        insert_stmt = (
            pg_insert(inbox_messages)
            .values(
                id=uuid.uuid4(),
                message_id=message_id,
                consumer_name=consumer_name,
                tenant_id=tenant_id,
                event_type=event_type,
                status="PROCESSING",
                attempt_count=1,
                locked_by=worker_id,
                locked_at=now,
                lease_until=lease_until,
                received_at=now,
            )
            .on_conflict_do_update(
                index_elements=["message_id", "consumer_name"],
                set_={
                    "status": "PROCESSING",
                    "locked_by": worker_id,
                    "locked_at": now,
                    "lease_until": lease_until,
                    "attempt_count": inbox_messages.c.attempt_count + 1,
                },
                where=(
                    (inbox_messages.c.status == "FAILED")
                    | (inbox_messages.c.status == "RECEIVED")
                    | (
                        (inbox_messages.c.status == "PROCESSING")
                        & (inbox_messages.c.lease_until <= now)
                    )
                ),
            )
            .returning(inbox_messages.c.status, inbox_messages.c.locked_by)
        )
        result = (await self._session.execute(insert_stmt)).mappings().first()
        if result is not None:
            return ClaimResult(ClaimStatus.GRANTED, worker_id)

        # Conflict occurred and WHERE clause was false: check current status
        check_stmt = select(
            inbox_messages.c.status,
            inbox_messages.c.locked_by,
            inbox_messages.c.lease_until,
        ).where(
            inbox_messages.c.message_id == message_id,
            inbox_messages.c.consumer_name == consumer_name,
        )
        existing = (await self._session.execute(check_stmt)).mappings().first()
        if existing is None:  # pragma: no cover
            return ClaimResult(ClaimStatus.LOCKED_BY_OTHER, worker_id)

        if existing["status"] == "PROCESSED":
            return ClaimResult(ClaimStatus.ALREADY_PROCESSED, worker_id)

        return ClaimResult(ClaimStatus.LOCKED_BY_OTHER, existing["locked_by"] or "unknown")

    async def claim_for_processing(
        self,
        *,
        message_id: str,
        consumer_name: str,
        tenant_id: uuid.UUID,
        event_type: str,
        now: datetime,
        worker_id: str | None = None,
        lease_duration_seconds: float = 30.0,
    ) -> ClaimResult | bool:
        """Claim a message for processing.

        When called with worker_id, returns a ClaimResult with status.
        When called without worker_id, returns True if processing was granted, False otherwise.
        """
        effective_worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        res = await self.claim_lease(
            message_id=message_id,
            consumer_name=consumer_name,
            tenant_id=tenant_id,
            event_type=event_type,
            worker_id=effective_worker_id,
            now=now,
            lease_duration_seconds=lease_duration_seconds,
        )
        if worker_id is None:
            return True if res.granted else False
        return res

    async def mark_processed(
        self,
        *,
        message_id: str,
        consumer_name: str,
        now: datetime,
        worker_id: str | None = None,
    ) -> None:
        """Mark an inbox message as successfully processed.

        If worker_id is provided, ownership is strictly verified.
        Raises InboxOwnershipLostError if worker_id does not hold the active lease.
        """
        stmt = (
            update(inbox_messages)
            .where(
                inbox_messages.c.message_id == message_id,
                inbox_messages.c.consumer_name == consumer_name,
            )
        )
        if worker_id is not None:
            stmt = stmt.where(
                inbox_messages.c.locked_by == worker_id,
                inbox_messages.c.status == "PROCESSING",
            )
        else:
            stmt = stmt.where(
                inbox_messages.c.status.in_(["RECEIVED", "PROCESSING"])
            )

        stmt = stmt.values(
            status="PROCESSED",
            processed_at=now,
            locked_by=None,
            lease_until=None,
        )
        result = await self._session.execute(stmt)
        if worker_id is not None and result.rowcount == 0:
            raise InboxOwnershipLostError(
                f"Worker {worker_id} cannot mark message {message_id} as PROCESSED: ownership lost or lease expired"
            )

    async def record_failure(
        self,
        *,
        message_id: str,
        consumer_name: str,
        error: str,
        now: datetime,
        worker_id: str | None = None,
    ) -> None:
        """Record processing failure on an inbox message.

        If worker_id is provided, ownership is verified before transitioning to FAILED.
        Raises InboxOwnershipLostError if worker_id does not hold the active lease.
        """
        stmt = (
            update(inbox_messages)
            .where(
                inbox_messages.c.message_id == message_id,
                inbox_messages.c.consumer_name == consumer_name,
            )
        )
        if worker_id is not None:
            stmt = stmt.where(
                inbox_messages.c.locked_by == worker_id,
                inbox_messages.c.status == "PROCESSING",
            )
        else:
            stmt = stmt.where(
                inbox_messages.c.status.in_(["RECEIVED", "PROCESSING"])
            )

        stmt = stmt.values(
            status="FAILED",
            failed_at=now,
            last_error=error[:2000],
            locked_by=None,
            lease_until=None,
        )
        result = await self._session.execute(stmt)
        if worker_id is not None and result.rowcount == 0:
            raise InboxOwnershipLostError(
                f"Worker {worker_id} cannot mark message {message_id} as FAILED: ownership lost or lease expired"
            )


__all__ = [
    "ClaimResult",
    "ClaimStatus",
    "InboxOwnershipLostError",
    "SqlAlchemyInboxRepository",
]
