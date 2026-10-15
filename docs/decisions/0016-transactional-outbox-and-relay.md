# 16. Transactional Outbox and Independent Relay Worker

## Context
When an aggregate mutates (e.g. an order is placed or inventory is reserved), integration events must be published to RabbitMQ. Publishing directly to RabbitMQ inside the application request introduces a dual-write failure mode: if the database transaction commits but network connectivity to RabbitMQ fails, events are lost. Conversely, if RabbitMQ publish succeeds but the database transaction rolls back, phantom events are broadcast.

## Decision
We implement the **Transactional Outbox Pattern**:
1. When a use case executes, aggregate changes and outgoing domain events are written to the `outbox_events` table within the same PostgreSQL transaction.
2. An independent outbox relay worker periodically claims pending outbox records using `SELECT ... FOR UPDATE SKIP LOCKED`, batches them, and publishes them to RabbitMQ with publisher confirms enabled.
3. The outbox record is marked `PUBLISHED` only after RabbitMQ confirms broker receipt.

## Alternatives Considered
- *Direct RabbitMQ publish in use case:* Rejected due to potential dual-write inconsistency and phantom messages.
- *Two-Phase Commit (2PC / XA):* Rejected due to high latency, operational complexity, and poor cloud support.

## Consequences
- Guarantees at-least-once message delivery without lost committed events.
- Requires downstream consumers to be idempotent (handled via the Inbox pattern).
