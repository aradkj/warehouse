"""Background worker implementation (Phase 2).

Hosts Celery tasks and Outbox Relay runner.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import UTC, datetime
from typing import Any

from bootstrap.celery_app import celery_app
from bootstrap.config import get_settings
from bootstrap.dependencies import build_use_cases
from inventory.application.commands import ExpireReservationsCommand
from shared.application.context import ActorType, RequestContext
from shared.domain.clock import SystemClock
from shared.infrastructure.db.session import Database

logger = logging.getLogger(__name__)


async def _run_expire_reservations() -> int:
    """Execute reservation expiration sweep using application layer."""
    settings = get_settings()
    database = Database(settings.database_url)
    try:
        scope = database.new_scope()
        use_cases = build_use_cases(
            settings=settings,
            clock=SystemClock(),
            scope=scope,
        )

        context = RequestContext(
            tenant_id=None,
            actor_id="worker:reservation-sweeper",
            actor_type=ActorType.SYSTEM,
            correlation_id=str(uuid.uuid4()),
            received_at=datetime.now(UTC),
        )

        command = ExpireReservationsCommand(
            context=context,
            limit=settings.reservation_expiry_batch_size,
        )

        outcome = await use_cases.expire_reservations(command)
        expired_count = outcome.view
        if expired_count > 0:
            logger.info("Reservation sweeper expired %d reservations.", expired_count)
        return expired_count
    finally:
        await database.dispose()


@celery_app.task(
    name="inventory.expire_reservations_task",
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def expire_reservations_task(self: Any) -> int:
    """Celery periodic task to expire active reservations past their TTL."""
    try:
        return asyncio.run(_run_expire_reservations())
    except Exception as exc:
        logger.error("Error in expire_reservations_task: %s", exc, exc_info=True)
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)


async def _run_outbox_relay() -> None:
    """Run the outbox relay service continuous loop."""
    from shared.infrastructure.messaging.publisher import RabbitMQPublisher
    from shared.infrastructure.messaging.relay import OutboxRelayService
    from shared.infrastructure.messaging.topology import declare_topology

    settings = get_settings()
    database = Database(settings.database_url)
    publisher = RabbitMQPublisher(settings.rabbitmq_url)

    # Pre-declare topology before running relay
    import aio_pika

    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    async with connection:
        channel = await connection.channel()
        await declare_topology(channel)

    relay = OutboxRelayService(
        database=database,
        publisher=publisher,
        batch_size=settings.outbox_batch_size,
        claim_timeout_seconds=settings.outbox_claim_timeout_seconds,
        poll_interval_seconds=settings.outbox_poll_interval_seconds,
        max_attempts=settings.outbox_max_attempts,
    )

    try:
        await relay.start()
    finally:
        await database.dispose()


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m bootstrap.workers`` / ``arad-worker``."""
    parser = argparse.ArgumentParser(
        prog="arad-worker",
        description="Arad background worker runner (Outbox Relay & Expiration Sweeper)",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["relay", "expire-reservations", "help"],
        default="relay",
        help="Command to run: 'relay' (outbox relay), 'expire-reservations' (single sweep)",
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    from bootstrap.logging import configure_logging
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    if args.command == "relay":
        logger.info("Starting outbox relay service...")
        asyncio.run(_run_outbox_relay())
        return 0
    elif args.command == "expire-reservations":
        logger.info("Running single reservation expiration sweep...")
        count = asyncio.run(_run_expire_reservations())
        print(f"Expired {count} reservations.")
        return 0
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
