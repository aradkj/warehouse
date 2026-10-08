# Arad — Distributed Order and Inventory Orchestration Platform

**Phase 2: Reliable Asynchronous Processing.** A modular-monolith, hexagonal implementation of the
platform's asynchronous processing layer — featuring a Transactional Outbox pattern, RabbitMQ message broker,
Outbox Relay with `FOR UPDATE SKIP LOCKED`, Inbox processed-message idempotency, Celery background workers,
Celery Beat scheduler, and reliable background reservation expiration sweepers.

---

## Quick start

### Docker (API + PostgreSQL + RabbitMQ + Redis + Celery Worker + Beat + Outbox Relay)

```bash
docker compose up --build
curl -s localhost:8000/healthz        # {"status":"ok","environment":"production","checks":{}}
curl -s localhost:8000/readyz         # {"status":"ready",...,"checks":{"database":"ok","cache":"ok"}}
open http://localhost:15672           # RabbitMQ Management UI (arad / arad)
```

`docker compose` starts PostgreSQL, RabbitMQ, and Redis, waits for health checks, applies database migrations, and starts the API, Outbox Relay runner, Celery Worker, and Celery Beat scheduler.

---

## Phase 2 Reliable Asynchronous Processing Architecture

```text
PostgreSQL (Source of Truth)
      │
      ├── Business Transaction (Order, Inventory, etc.)
      │
      └── outbox_events (Atomically committed in same DB transaction)
              │
              ▼
        Outbox Relay (FOR UPDATE SKIP LOCKED batch claiming)
              │
              ▼
    RabbitMQ (Durable Exchange: arad.events)
              │
      ┌───────┼───────┐
      ▼       ▼       ▼
   Worker  Worker  Worker
      │       │       │
      └───────┼───────┘
              ▼
    inbox_messages (Consumer-side idempotency on message_id + consumer_name)
              │
              ▼
     Application Use Cases
```

### Key Guarantees & Features

1. **Transactional Outbox**: Domain events are serialized into standard envelopes and appended to `outbox_events` within the exact same PostgreSQL transaction as aggregate modifications.
2. **Outbox Relay**: Polls `outbox_events` using `FOR UPDATE SKIP LOCKED` for concurrent safety, publishes to RabbitMQ with publisher confirmations, and marks published status atomically.
3. **RabbitMQ Topology**:
   - Primary Exchange: `arad.events` (topic, durable)
   - Dead Letter Exchange: `arad.dlx` (topic, durable)
   - Queues: `arad.fulfillment`, `arad.search`, `arad.notifications` + DLQs (`.dlq`).
4. **Inbox Idempotency**: Consumers check `inbox_messages` with `(message_id, consumer_name)` uniqueness prior to processing events, preventing duplicate execution under at-least-once delivery.
5. **Celery Workers & Beat**: Celery processes background tasks and runs periodic reservation expiry sweeps (`inventory.expire_reservations_task`) to release expired holds safely and post `InventoryReservationExpired` outbox events.

## Phase 3: Search and Caching

### Part 1: Redis Infrastructure & Cache Foundation
- **`CachePort` protocol**: Outbound hexagonal port for cache-aside reads, TTL management, stampede locking, and negative caching.
- **`RedisCacheAdapter`**: Production Redis client using connection pooling and JSON serialization. Degrades gracefully to database queries on Redis timeouts.
- **Tenant Isolation**: Keys format `v{version}:{tenant_id}:{resource}:{identifier}`.
- **No Global Flushes**: Selective cache invalidation per entity key; `FLUSHDB` and `FLUSHALL` are prohibited.

### Part 2: Elasticsearch Search Projection, Event-Driven Indexing & Cache Invalidation
- **`SearchIndexer` port**: Hexagonal port for denormalized search document indexing, versioned writes, and deletions.
- **`ElasticsearchIndexAdapter`**: Official `elasticsearch.AsyncElasticsearch` implementation with optimistic external versioning (`version_type="external_gte"`) to tolerate out-of-order and duplicate events.
- **`InMemorySearchIndexer`**: Fast in-memory implementation for unit and offline tests.
- **`ProductSearchDocument` read model**: Denormalized search representation of products and nested SKUs in `products-v1` index with `products` alias.
- **`ProductSearchProjector`**: Subscribes to Catalog domain events (`ProductCreated`, `ProductRevised`, `ProductActivated`, `ProductArchived`, `SkuCreated`, `SkuPriceChanged`, `SkuActivated`, `SkuDeactivated`), updating Elasticsearch and triggering targeted Redis cache invalidation.
- **Worker command**: `python -m bootstrap.workers search-consumer` runs the idempotent consumer listening to `arad.search`.

---

## Testing

```bash
docker compose run --rm test
```

Runs the full test suite including:
- Phase 1 baseline: 275 tests
- Phase 2 asynchronous processing: 24 tests
- Phase 3 Part 1 Redis caching: 30 tests
- Phase 3 Part 2 Elasticsearch search projections: 15 tests
- **Total: 344 passed tests**

---

## License

Proprietary — see `pyproject.toml`.
