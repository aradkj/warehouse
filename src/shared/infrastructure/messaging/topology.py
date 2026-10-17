"""RabbitMQ exchange, queue and routing topology setup.

Declares durable exchange, queues, dead-letter queues, and binding topology.
"""

from __future__ import annotations

import logging

import aio_pika

logger = logging.getLogger(__name__)

EXCHANGE_EVENTS = "arad.events"
EXCHANGE_DLX = "arad.dlx"
EXCHANGE_RETRY = "arad.retry"

QUEUE_FULFILLMENT = "arad.fulfillment"
QUEUE_SEARCH = "arad.search"
QUEUE_NOTIFICATIONS = "arad.notifications"

QUEUE_FULFILLMENT_RETRY = "arad.fulfillment.retry"
QUEUE_SEARCH_RETRY = "arad.search.retry"
QUEUE_NOTIFICATIONS_RETRY = "arad.notifications.retry"

QUEUE_FULFILLMENT_DLQ = "arad.fulfillment.dlq"
QUEUE_SEARCH_DLQ = "arad.search.dlq"
QUEUE_NOTIFICATIONS_DLQ = "arad.notifications.dlq"

ROUTING_KEY_MAP = {
    "OrderConfirmed": "order.confirmed",
    "OrderCancelled": "order.cancelled",
    "OrderPlaced": "order.placed",
    "OrderBackordered": "order.backordered",
    "OrderInventoryReserved": "order.inventory_reserved",
    "OrderShipped": "order.shipped",
    "OrderDelivered": "order.delivered",
    "InventoryReserved": "inventory.reserved",
    "ReservationConfirmed": "inventory.reservation_confirmed",
    "ReservationReleased": "inventory.reservation_released",
    "InventoryReservationExpired": "inventory.reservation_expired",
    "ReservationExpired": "inventory.reservation_expired",
    "TenantRegistered": "tenant.registered",
    "ProductCreated": "product.created",
    "ProductRevised": "product.revised",
    "ProductActivated": "product.activated",
    "ProductArchived": "product.archived",
    "SkuCreated": "sku.created",
    "SkuPriceChanged": "sku.price_changed",
    "SkuActivated": "sku.activated",
    "SkuDeactivated": "sku.deactivated",
    "FulfillmentFailed": "fulfillment.failed",
    "AuditLogged": "audit.logged",
}


def event_type_to_routing_key(event_type: str) -> str:
    """Map a domain event name to a RabbitMQ routing key."""
    if event_type in ROUTING_KEY_MAP:
        return ROUTING_KEY_MAP[event_type]
    # Default fallback: lowercase snake_case
    return event_type.lower().replace("event", "")


async def declare_topology(channel: aio_pika.abc.AbstractChannel) -> None:
    """Declare all exchanges, queues, DLQs, and bindings."""
    # 1. Main topic exchange
    events_exchange = await channel.declare_exchange(
        EXCHANGE_EVENTS, type=aio_pika.ExchangeType.TOPIC, durable=True
    )

    # 2. Dead Letter Exchange
    dlx_exchange = await channel.declare_exchange(
        EXCHANGE_DLX, type=aio_pika.ExchangeType.DIRECT, durable=True
    )

    # 3. Retry Exchange
    retry_exchange = await channel.declare_exchange(
        EXCHANGE_RETRY, type=aio_pika.ExchangeType.DIRECT, durable=True
    )

    # 4. DLQs
    dlq_fulfillment = await channel.declare_queue(QUEUE_FULFILLMENT_DLQ, durable=True)
    await dlq_fulfillment.bind(dlx_exchange, routing_key=QUEUE_FULFILLMENT_DLQ)

    dlq_search = await channel.declare_queue(QUEUE_SEARCH_DLQ, durable=True)
    await dlq_search.bind(dlx_exchange, routing_key=QUEUE_SEARCH_DLQ)

    dlq_notifications = await channel.declare_queue(QUEUE_NOTIFICATIONS_DLQ, durable=True)
    await dlq_notifications.bind(dlx_exchange, routing_key=QUEUE_NOTIFICATIONS_DLQ)

    # 5. Retry Queues (message expires -> dead-lettered back to main queue)
    retry_queues = (
        (QUEUE_FULFILLMENT_RETRY, QUEUE_FULFILLMENT),
        (QUEUE_SEARCH_RETRY, QUEUE_SEARCH),
        (QUEUE_NOTIFICATIONS_RETRY, QUEUE_NOTIFICATIONS),
    )
    for retry_q_name, target_q_name in retry_queues:
        rq = await channel.declare_queue(
            retry_q_name,
            durable=True,
            arguments={
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": target_q_name,
            },
        )
        await rq.bind(retry_exchange, routing_key=target_q_name)

    # 6. Main Queues with DLQ policy
    queue_fulfillment = await channel.declare_queue(
        QUEUE_FULFILLMENT,
        durable=True,
        arguments={
            "x-dead-letter-exchange": EXCHANGE_DLX,
            "x-dead-letter-routing-key": QUEUE_FULFILLMENT_DLQ,
        },
    )
    for key in ("order.confirmed", "order.cancelled"):
        await queue_fulfillment.bind(events_exchange, routing_key=key)

    queue_search = await channel.declare_queue(
        QUEUE_SEARCH,
        durable=True,
        arguments={
            "x-dead-letter-exchange": EXCHANGE_DLX,
            "x-dead-letter-routing-key": QUEUE_SEARCH_DLQ,
        },
    )
    for key in ("product.#", "sku.#", "order.#", "fulfillment.#", "audit.#"):
        await queue_search.bind(events_exchange, routing_key=key)

    queue_notifications = await channel.declare_queue(
        QUEUE_NOTIFICATIONS,
        durable=True,
        arguments={
            "x-dead-letter-exchange": EXCHANGE_DLX,
            "x-dead-letter-routing-key": QUEUE_NOTIFICATIONS_DLQ,
        },
    )
    for key in ("order.#", "inventory.reservation_expired"):
        await queue_notifications.bind(events_exchange, routing_key=key)

    logger.info("RabbitMQ messaging topology declared successfully.")


__all__ = [
    "EXCHANGE_DLX",
    "EXCHANGE_EVENTS",
    "EXCHANGE_RETRY",
    "QUEUE_FULFILLMENT",
    "QUEUE_FULFILLMENT_DLQ",
    "QUEUE_FULFILLMENT_RETRY",
    "QUEUE_NOTIFICATIONS",
    "QUEUE_NOTIFICATIONS_DLQ",
    "QUEUE_NOTIFICATIONS_RETRY",
    "QUEUE_SEARCH",
    "QUEUE_SEARCH_DLQ",
    "QUEUE_SEARCH_RETRY",
    "ROUTING_KEY_MAP",
    "declare_topology",
    "event_type_to_routing_key",
]
