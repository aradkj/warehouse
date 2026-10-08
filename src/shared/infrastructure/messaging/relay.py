"""Outbox relay service.

Continuously claims PENDING outbox events from PostgreSQL, publishes them to RabbitMQ,
and marks them PUBLISHED upon receiving publisher confirmation.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

from shared.infrastructure.db.outbox import OutboxEvent, SqlAlchemyOutboxRepository
from shared.infrastructure.db.session import Database
from shared.infrastructure.messaging.publisher import RabbitMQPublisher

logger = logging.getLogger(__name__)


class OutboxRelayService:
    """Outbox Relay process / runner."""

    __slots__ = (
        "_batch_size",
        "_claim_timeout_seconds",
        "_database",
        "_max_attempts",
        "_node_id",
        "_poll_interval_seconds",
        "_publisher",
        "_running",
    )

    def __init__(
        self,
        database: Database,
        publisher: RabbitMQPublisher,
        *,
        batch_size: int = 50,
        claim_timeout_seconds: float = 30.0,
        poll_interval_seconds: float = 1.0,
        max_attempts: int = 5,
        node_id: str | None = None,
    ) -> None:
        self._database = database
        self._publisher = publisher
        self._batch_size = batch_size
        self._claim_timeout_seconds = claim_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._max_attempts = max_attempts
        self._node_id = node_id or f"relay-{uuid.uuid4().hex[:8]}"
        self._running = False

    async def run_once(self) -> int:
        """Run a single pass of outbox polling and delivery.

        Returns number of events processed.
        """
        now = datetime.now(UTC)
        claimed_events: tuple[OutboxEvent, ...] = ()

        # 1. Claim batch in atomic transaction
        async with self._database.new_scope() as scope:
            repo = SqlAlchemyOutboxRepository(scope.session)
            claimed_events = await repo.claim_batch(
                batch_size=self._batch_size,
                claim_timeout_seconds=self._claim_timeout_seconds,
                claimed_by=self._node_id,
                now=now,
            )
            await scope.commit()

        if not claimed_events:
            return 0

        # 2. Publish claimed events and update status individually
        published_count = 0
        for event in claimed_events:
            try:
                await self._publisher.publish(event)
                # Mark PUBLISHED
                async with self._database.new_scope() as scope:
                    repo = SqlAlchemyOutboxRepository(scope.session)
                    await repo.mark_published(event.id, now=datetime.now(UTC))
                    await scope.commit()
                published_count += 1
            except Exception as exc:
                logger.warning(
                    "Failed to publish outbox event %s to RabbitMQ: %s",
                    event.id,
                    exc,
                    exc_info=True,
                )
                try:
                    async with self._database.new_scope() as scope:
                        repo = SqlAlchemyOutboxRepository(scope.session)
                        await repo.record_failure(
                            event.id,
                            error=str(exc),
                            max_attempts=self._max_attempts,
                            now=datetime.now(UTC),
                        )
                        await scope.commit()
                except Exception as inner_exc:  # pragma: no cover
                    logger.error(
                        "Failed to record failure for outbox event %s: %s", event.id, inner_exc
                    )

        return published_count

    async def start(self) -> None:
        """Start the continuous relay loop until stopped."""
        self._running = True
        logger.info("Outbox relay service starting (node_id=%s)", self._node_id)
        await self._publisher.connect()
        while self._running:
            try:
                count = await self.run_once()
                if count == 0:
                    await asyncio.sleep(self._poll_interval_seconds)
            except asyncio.CancelledError:
                logger.info("Outbox relay service received cancel signal.")
                break
            except Exception as exc:  # pragma: no cover
                logger.error("Error in outbox relay loop: %s", exc, exc_info=True)
                await asyncio.sleep(self._poll_interval_seconds)
        await self.stop()

    async def stop(self) -> None:
        """Stop the relay loop and close resources."""
        self._running = False
        await self._publisher.close()
        logger.info("Outbox relay service stopped.")


__all__ = ["OutboxRelayService"]
