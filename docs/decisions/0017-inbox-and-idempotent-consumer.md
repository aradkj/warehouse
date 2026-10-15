# 17. Inbox Pattern and Idempotent Consumers

## Context
At-least-once message delivery from RabbitMQ means network blips or worker restarts can cause identical messages to be delivered more than once. Without deduplication, business logic (such as decrementing inventory or charging customers) would execute multiple times.

## Decision
We implement the **Inbox Pattern** across all asynchronous consumers:
1. Every event carries a globally unique `event_id` / `message_id`.
2. Consumers record incoming messages into an `inbox` table using `INSERT INTO inbox (message_id, tenant_id, ...) ON CONFLICT DO NOTHING`.
3. If an event has already been processed, the consumer acknowledges the broker message immediately without re-executing domain use cases.

## Alternatives Considered
- *Stateless in-memory cache deduplication (e.g. Redis):* Rejected because if Redis restarts or evicts keys, duplicate events would be re-executed against the database.
- *Relying solely on aggregate versioning:* Rejected because read-side projections and external notifications have no aggregate version to guard against duplicate execution.

## Consequences
- Guarantees exactly-once business processing semantics on top of at-least-once transport.
- Requires atomic lease claiming and periodic inbox table pruning.
