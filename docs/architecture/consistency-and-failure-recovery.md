# Consistency and Failure Recovery Guarantees

This document details the transactional boundaries, consistency guarantees, and automated failure-recovery mechanisms implemented across the platform.

---

## 1. Core Consistency Guarantees

### 1.1. Transaction Boundaries and Atomic Commits
Every transactional write operation executes within a strict single Unit of Work (`SqlAlchemyUnitOfWork`). The transaction boundary encapsulates:
1. Aggregate state mutation (e.g. order confirmation, inventory deduction).
2. Domain event insertion into `outbox_events`.
3. Idempotency record insertion/update in `idempotency_records`.

If any operation fails, the transaction is completely rolled back, guaranteeing zero orphaned outbox events or partial aggregate changes.

### 1.2. Optimistic Concurrency Control
Every aggregate root tracks a `version` integer. When saving an aggregate, the database adapter executes:
`UPDATE table SET ..., version = :new_version WHERE id = :id AND version = :expected_version`
If another process committed a change in the interim, the update affects 0 rows, raising a `ConcurrencyConflict` exception. The caller is notified immediately, preventing lost updates.

### 1.3. Row-Level Locking for Inventory
To prevent race conditions during high-demand inventory allocations, `InventoryRepository` executes `SELECT ... FOR UPDATE` ordered deterministically by SKU and Warehouse ID. This serializes competing writers at the PostgreSQL engine level, guaranteeing that available stock never falls below zero.

---

## 2. Asynchronous Messaging Guarantees

### 2.1. Transactional Outbox (At-Least-Once Delivery)
Domain events are buffered in PostgreSQL prior to broker publication. The outbox relay worker claims batches using `SELECT ... FOR UPDATE SKIP LOCKED` and publishes to RabbitMQ with publisher confirms enabled. The database record is marked `PUBLISHED` only after RabbitMQ confirms broker receipt.

### 2.2. Inbox Pattern (Exactly-Once Business Semantics)
Because network partitions between RabbitMQ and the outbox relay can cause redeliveries, downstream consumers implement the **Inbox Pattern**. Incoming messages are inserted into the `inbox` table using `ON CONFLICT (message_id) DO NOTHING`. If a duplicate message is received, the consumer acknowledges the broker message without re-executing business logic.

---

## 3. Failure Scenarios and Recovery Behavior

| Failure Scenario | Immediate System Behavior | Automated Recovery Mechanism | Final Consistency State |
|---|---|---|---|
| **RabbitMQ Outage** | Aggregate state commits to PostgreSQL normally. Outbox relay fails to publish and increments retry counter. HTTP clients receive 200/201 success. | When RabbitMQ recovers, outbox relay reconnects and resumes publishing pending records in chronological order. | Eventual consistency restored; zero lost events. |
| **Redis Outage** | Cache read fails or times out. Redis adapter catches connection exceptions and logs a warning. | Application transparently falls back to PostgreSQL system of record for queries. | Zero user-visible downtime; slightly increased DB query load. |
| **Elasticsearch Outage** | Projection event consumer fails to write to Elasticsearch. Message is moved to retry queue with exponential backoff. | When Elasticsearch recovers, buffered retry messages or the scheduled reconciliation task re-indexes documents. | PostgreSQL remains authoritative; search index reaches eventual consistency upon recovery. |
| **Worker Process Crash** | Celery or consumer worker process terminates mid-execution. Active database transaction rolls back. | RabbitMQ detects dropped TCP socket and re-queues unacknowledged messages. Replacement worker claims message via inbox lease. | No duplicate execution; task resumed cleanly. |
| **Duplicate HTTP Request** | Client sends identical request with duplicate `Idempotency-Key` within 24 hours. | Use case checks `idempotency_records`. If already completed, returns cached response view immediately without re-executing domain logic. | Exactly-one business operation executed; consistent client response. |
| **Concurrent Inventory Requests** | Multiple clients simultaneously request the last available stock unit. | First transaction acquires `FOR UPDATE` lock. Second transaction waits on lock. When first completes, second sees available stock = 0 and rejects cleanly. | Zero overselling; inventory never drops below zero. |
