# Arad — Distributed Order and Inventory Orchestration Platform

**Phase 2: Reliable Asynchronous Processing.** A modular-monolith, hexagonal implementation of the
platform's asynchronous processing layer — featuring a Transactional Outbox pattern, RabbitMQ message broker,
Outbox Relay with `FOR UPDATE SKIP LOCKED`, Inbox processed-message idempotency, Celery background workers,
Celery Beat scheduler, and reliable background reservation expiration sweepers.

---

## Quick start

### Docker (API + PostgreSQL + RabbitMQ + Celery Worker + Beat + Outbox Relay)

```bash
docker compose up --build
curl -s localhost:8000/healthz        # {"status":"ok","environment":"production","checks":{}}
curl -s localhost:8000/readyz         # {"status":"ok",...,"checks":{"database":"ok"}}
open http://localhost:15672           # RabbitMQ Management UI (arad / arad)
```

`docker compose` starts PostgreSQL & RabbitMQ, waits for health checks, applies database migrations, starts API, Outbox Relay runner, Celery Worker, and Celery Beat scheduler.

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

---

## Testing

```bash
docker compose run --rm test
```

Runs the full test suite including all Phase 1 unit, application, repository, api, integration, and concurrency tests (275 tests) plus Phase 2 outbox, inbox, relay, messaging, failure injection, and reservation expiration tests.

---

## License

Proprietary — see `pyproject.toml`.
