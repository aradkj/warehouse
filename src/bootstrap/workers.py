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
    """Execute reservation expiration sweep using application layer per active tenant."""
    from sqlalchemy import text

    from identity.domain.principal import Principal

    settings = get_settings()
    database = Database(settings.database_url)
    try:
        # Enumerate active tenants without bypass (tenants table is global, un-scoped)
        async with database.new_scope() as list_scope:
            res = await list_scope.session.execute(
                text("SELECT id FROM tenants WHERE status = 'ACTIVE' ORDER BY created_at ASC")
            )
            active_tenant_ids = [row[0] for row in res.fetchall()]

        total_expired = 0
        for tid in active_tenant_ids:
            # Process each tenant strictly within its own isolated transaction and RLS context
            async with database.new_scope(tenant_id=str(tid)) as tenant_scope:
                use_cases = build_use_cases(
                    settings=settings,
                    clock=SystemClock(),
                    scope=tenant_scope,
                )
                principal = Principal(
                    id="worker:reservation-sweeper",
                    type=ActorType.SYSTEM,
                    active_tenant_id=tid,
                    roles=("system",),
                    permissions=frozenset({"*", "inventory.expire"}),
                )
                context = RequestContext(
                    tenant_id=tid,
                    actor_id="worker:reservation-sweeper",
                    actor_type=ActorType.SYSTEM,
                    correlation_id=str(uuid.uuid4()),
                    received_at=datetime.now(UTC),
                    principal=principal,
                )
                command = ExpireReservationsCommand(
                    context=context,
                    limit=settings.reservation_expiry_batch_size,
                )
                outcome = await use_cases.expire_reservations(command)
                expired_count = outcome.view
                if expired_count > 0:
                    total_expired += expired_count
                await tenant_scope.commit()

        if total_expired > 0:
            logger.info("Reservation sweeper expired %d reservations total.", total_expired)
        return total_expired
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
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc


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


async def _run_search_consumer() -> None:
    """Run search projection consumer worker loop listening to arad.search."""
    import aio_pika

    from search.adapters.elasticsearch import ElasticsearchIndexAdapter
    from search.adapters.messaging import create_search_projection_consumer
    from search.application.models import (
        AUDIT_INDEX_ALIAS,
        AUDIT_INDEX_MAPPINGS,
        AUDIT_INDEX_V1,
        FULFILLMENT_EXCEPTIONS_INDEX_ALIAS,
        FULFILLMENT_EXCEPTIONS_INDEX_MAPPINGS,
        FULFILLMENT_EXCEPTIONS_INDEX_V1,
        ORDERS_INDEX_ALIAS,
        ORDERS_INDEX_MAPPINGS,
        ORDERS_INDEX_V1,
        PRODUCTS_INDEX_ALIAS,
        PRODUCTS_INDEX_MAPPINGS,
        PRODUCTS_INDEX_V1,
    )
    from shared.infrastructure.cache.redis import RedisCacheAdapter
    from shared.infrastructure.messaging.topology import QUEUE_SEARCH, declare_topology

    settings = get_settings()
    database = Database(settings.database_url)
    cache: RedisCacheAdapter | None = None
    if settings.redis_url:
        cache = RedisCacheAdapter.from_settings(settings)

    indexer = ElasticsearchIndexAdapter.from_settings(settings)

    try:
        # Pre-declare indices and aliases
        await indexer.ensure_index(
            index_name=PRODUCTS_INDEX_V1,
            mappings=PRODUCTS_INDEX_MAPPINGS,
            aliases=(PRODUCTS_INDEX_ALIAS,),
        )
        await indexer.ensure_index(
            index_name=ORDERS_INDEX_V1,
            mappings=ORDERS_INDEX_MAPPINGS,
            aliases=(ORDERS_INDEX_ALIAS,),
        )
        await indexer.ensure_index(
            index_name=FULFILLMENT_EXCEPTIONS_INDEX_V1,
            mappings=FULFILLMENT_EXCEPTIONS_INDEX_MAPPINGS,
            aliases=(FULFILLMENT_EXCEPTIONS_INDEX_ALIAS,),
        )
        await indexer.ensure_index(
            index_name=AUDIT_INDEX_V1,
            mappings=AUDIT_INDEX_MAPPINGS,
            aliases=(AUDIT_INDEX_ALIAS,),
        )

        consumer, _ = create_search_projection_consumer(
            database=database,
            indexer=indexer,
            cache=cache,
            queue_name=QUEUE_SEARCH,
        )

        connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        async with connection:
            channel = await connection.channel()
            await channel.set_qos(prefetch_count=10)
            await declare_topology(channel)
            queue = await channel.get_queue(QUEUE_SEARCH)
            logger.info("Search consumer listening on %s...", QUEUE_SEARCH)
            async with queue.iterator() as queue_iter:
                async for message in queue_iter:
                    await consumer.process_message(message)
    finally:
        await indexer.close()
        if cache is not None:
            await cache.close()
        await database.dispose()


async def _run_reindex(
    tenant_id: str | None = None, retain_old_indices: bool = False
) -> dict[str, Any]:
    """Execute full reindex from PostgreSQL to Elasticsearch."""
    from search.adapters.elasticsearch import ElasticsearchIndexAdapter
    from search.adapters.postgres import SqlAlchemyCatalogSearchReader
    from search.application.reindex import ReindexingService
    from shared.infrastructure.cache.redis import RedisCacheAdapter

    settings = get_settings()
    database = Database(settings.database_url)
    indexer = ElasticsearchIndexAdapter.from_settings(settings)
    reader = SqlAlchemyCatalogSearchReader(database)
    cache = RedisCacheAdapter.from_settings(settings) if settings.redis_url else None

    try:
        service = ReindexingService(
            indexer=indexer,
            reader=reader,
            cache=cache,
            batch_size=settings.reindex_batch_size,
        )
        result = await service.reindex(
            tenant_id=tenant_id,
            retain_old_indices=retain_old_indices,
        )
        return {
            "index_name": result.index_name,
            "alias": result.alias,
            "total_indexed": result.total_indexed,
            "status": result.status,
        }
    finally:
        await indexer.close()
        if cache:
            await cache.close()
        await database.dispose()


@celery_app.task(
    name="search.reindex_products_task",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def reindex_products_task(
    self: Any,
    tenant_id: str | None = None,
    retain_old_indices: bool = False,
) -> dict[str, Any]:
    """Celery background task to rebuild search index from PostgreSQL."""
    try:
        return asyncio.run(_run_reindex(tenant_id, retain_old_indices))
    except Exception as exc:
        logger.error("Error in reindex_products_task: %s", exc, exc_info=True)
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc


async def _run_reconcile(tenant_id: str | None = None, auto_repair: bool = False) -> dict[str, Any]:
    """Execute reconciliation between PostgreSQL and Elasticsearch."""
    from search.adapters.elasticsearch import ElasticsearchIndexAdapter
    from search.adapters.postgres import SqlAlchemyCatalogSearchReader
    from search.application.reconciliation import ReconciliationService

    settings = get_settings()
    database = Database(settings.database_url)
    indexer = ElasticsearchIndexAdapter.from_settings(settings)
    reader = SqlAlchemyCatalogSearchReader(database)

    try:
        service = ReconciliationService(indexer=indexer, reader=reader)
        result = await service.reconcile(tenant_id=tenant_id, auto_repair=auto_repair)
        return {
            "total_authoritative": result.total_authoritative,
            "total_search": result.total_search,
            "matched": result.matched,
            "discrepancies_count": len(result.discrepancies),
            "repaired_count": result.repaired_count,
        }
    finally:
        await indexer.close()
        await database.dispose()


@celery_app.task(
    name="search.reconcile_products_task",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def reconcile_products_task(
    self: Any,
    tenant_id: str | None = None,
    auto_repair: bool = False,
) -> dict[str, Any]:
    """Celery background task to reconcile PostgreSQL and Elasticsearch read models."""
    try:
        return asyncio.run(_run_reconcile(tenant_id, auto_repair))
    except Exception as exc:
        logger.error("Error in reconcile_products_task: %s", exc, exc_info=True)
        raise self.retry(exc=exc, countdown=2**self.request.retries) from exc


def main(argv: list[str] | None = None) -> int:
    """Entry point for ``python -m bootstrap.workers`` / ``arad-worker``."""
    parser = argparse.ArgumentParser(
        prog="arad-worker",
        description="Arad background worker runner",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=[
            "relay",
            "expire-reservations",
            "search-consumer",
            "reindex",
            "reconcile",
            "help",
        ],
        default="relay",
        help="Command to run: relay, expire-reservations, search-consumer, reindex, reconcile",
    )
    parser.add_argument("--tenant-id", type=str, default=None, help="Optional tenant ID scope")
    parser.add_argument(
        "--retain-old", action="store_true", help="Retain old indices after reindex"
    )
    parser.add_argument(
        "--auto-repair",
        action="store_true",
        help="Automatically repair discrepancies during reconciliation",
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
    elif args.command == "search-consumer":
        logger.info("Starting search projection consumer...")
        asyncio.run(_run_search_consumer())
        return 0
    elif args.command == "reindex":
        logger.info("Running reindex from PostgreSQL to Elasticsearch...")
        res = asyncio.run(_run_reindex(args.tenant_id, args.retain_old))
        print(f"Reindex complete: {res}")
        return 0
    elif args.command == "reconcile":
        logger.info("Running reconciliation between PostgreSQL and Elasticsearch...")
        res = asyncio.run(_run_reconcile(args.tenant_id, args.auto_repair))
        print(f"Reconciliation complete: {res}")
        return 0
    else:
        parser.print_help()
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
