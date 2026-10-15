# Event Flow and Asynchronous Messaging Architecture

## 1. Overview of Messaging Topology

The Warehouse platform uses an event-driven architecture combining:
1. **Transactional Outbox Pattern:** Guarantees atomic persistence of aggregate mutations and outgoing domain events in PostgreSQL.
2. **RabbitMQ Topic Exchange (`arad.events`):** Provides durable message delivery with routing keys formatted as `{context}.{entity}.{event_name}`.
3. **Dead-Letter (DLX) & Retry Queues:** Handles temporary consumer failures with exponential backoff and dead-letter queues (`arad.dlx`).
4. **Idempotent Inbox Consumers:** Guarantees at-least-once delivery does not lead to duplicate processing.
5. **Celery Asynchronous Tasks:** Handles background workers, reservation expiration, outbox polling, and scheduled reconciliation.

---

## 2. End-to-End Event Lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor Client
    participant API as FastAPI Router
    participant App as UseCase / UoW
    participant DB as PostgreSQL (Aggregates & Outbox)
    participant Relay as Outbox Relay Worker
    participant RMQ as RabbitMQ Exchange (arad.events)
    participant Queue as Worker Queue
    participant Inbox as Inbox Consumer
    participant ES as Elasticsearch Projection
    participant Redis as Redis Cache

    Client->>API: POST /v1/orders (Idempotency-Key)
    API->>App: Execute CreateOrderCommand
    App->>DB: BEGIN TX
    App->>DB: INSERT orders
    App->>DB: INSERT outbox_events (Status: PENDING)
    App->>DB: COMMIT TX
    App-->>API: OrderView (201 Created)
    API-->>Client: HTTP 201 Response

    Note over Relay,DB: Outbox Relay loops with SKIP LOCKED
    Relay->>DB: SELECT * FROM outbox_events WHERE status = 'PENDING' FOR UPDATE SKIP LOCKED
    Relay->>RMQ: BasicPublish (confirm_publish=True)
    RMQ-->>Relay: Ack
    Relay->>DB: UPDATE outbox_events SET status = 'PUBLISHED'

    RMQ->>Queue: Route by topic (ordering.order.confirmed)
    Queue->>Inbox: Deliver message
    Inbox->>DB: BEGIN TX
    Inbox->>DB: INSERT inbox (message_id) ON CONFLICT DO NOTHING
    alt Duplicate Message
        Inbox-->>Queue: BasicAck (Ignored)
    else First Delivery
        Inbox->>ES: Update Order Projection
        Inbox->>Redis: Invalidate Cache Keys
        Inbox->>DB: UPDATE inbox SET status = 'PROCESSED'
        Inbox->>DB: COMMIT TX
        Inbox-->>Queue: BasicAck
    end
```

---

## 3. Message Envelope & Headers

Every message published by the outbox relay is wrapped in a standard `OutboxEnvelope`:

```json
{
  "event_id": "018f2d50-7f2e-7cb3-9fa1-cfd128394821",
  "event_type": "ordering.order.confirmed",
  "aggregate_type": "order",
  "aggregate_id": "018f2d50-7f2e-7cb3-9fa1-cfd128394822",
  "tenant_id": "018f2d50-7f2e-7cb3-9fa1-cfd128394823",
  "correlation_id": "req-c189283-99a",
  "causation_id": "018f2d50-7f2e-7cb3-9fa1-cfd128394820",
  "schema_version": 1,
  "occurred_at": "2026-10-14T19:30:00.000000Z",
  "payload": {
    "order_id": "018f2d50-7f2e-7cb3-9fa1-cfd128394822",
    "status": "CONFIRMED",
    "line_count": 2,
    "total_minor": 15000,
    "currency": "USD"
  }
}
```

---

## 4. Error Handling, Retries, and DLQ

- **Broker Failure on Commit:** If RabbitMQ is down when the order commits, the database commit completes successfully. The event remains in `outbox_events` with status `PENDING`. When RabbitMQ recovers, the relay resumes publishing automatically.
- **Consumer Failure:** If a consumer encounters a temporary database connection error:
  1. The message is rejected with `requeue=False` and forwarded to `arad.retry.{queue}` with a TTL.
  2. After TTL expiration, the message is routed back to the main queue for retry.
  3. If retries exceed the retry limit (e.g. 5 attempts), the message is routed to `arad.dlx` for manual inspection and alert generation.
- **Worker Crash:** If a Celery worker crashes mid-task, unacknowledged messages are automatically re-queued by RabbitMQ when the worker connection terminates.
