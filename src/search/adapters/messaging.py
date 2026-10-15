"""RabbitMQ messaging adapter for Search projections.

Connects IdempotentConsumer from shared messaging infrastructure to
the ProductSearchProjector application service.
"""

from __future__ import annotations

import logging
from typing import Any

from search.application.projector import ProductSearchProjector
from search.ports.indexer import SearchIndexer
from shared.application.context import RequestContext
from shared.infrastructure.db.session import Database
from shared.infrastructure.messaging.consumer import IdempotentConsumer
from shared.infrastructure.messaging.topology import QUEUE_SEARCH
from shared.ports.cache import CachePort

logger = logging.getLogger(__name__)

CATALOG_EVENT_TYPES: tuple[str, ...] = (
    "ProductCreated",
    "ProductRevised",
    "ProductActivated",
    "ProductArchived",
    "SkuCreated",
    "SkuPriceChanged",
    "SkuActivated",
    "SkuDeactivated",
    "OrderPlaced",
    "OrderConfirmed",
    "OrderCancelled",
    "OrderShipped",
    "OrderBackordered",
    "FulfillmentFailed",
)


def create_search_projection_consumer(
    database: Database,
    indexer: SearchIndexer,
    cache: CachePort | None = None,
    queue_name: str = QUEUE_SEARCH,
    consumer_name: str = "search",
) -> tuple[IdempotentConsumer, ProductSearchProjector]:
    """Create and configure an IdempotentConsumer wired to ProductSearchProjector."""
    projector = ProductSearchProjector(indexer=indexer, cache=cache)
    consumer = IdempotentConsumer(
        database=database,
        consumer_name=consumer_name,
        queue_name=queue_name,
    )

    for event_type in CATALOG_EVENT_TYPES:
        _register_projector_handler(consumer, projector, event_type)

    return consumer, projector


def _register_projector_handler(
    consumer: IdempotentConsumer,
    projector: ProductSearchProjector,
    event_type: str,
) -> None:
    async def handler(payload: dict[str, Any], context: RequestContext) -> None:
        logger.debug(
            "Projecting event %s for tenant %s (correlation %s)",
            event_type,
            context.tenant_id,
            context.correlation_id,
        )
        event_dict = {
            "event_name": event_type,
            "payload": {
                **payload,
                "tenant_id": str(context.tenant_id or payload.get("tenant_id", "")),
            },
        }
        await projector.handle_event(event_dict)

    consumer.register_handler(event_type, handler)


__all__ = [
    "CATALOG_EVENT_TYPES",
    "create_search_projection_consumer",
]
