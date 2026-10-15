# 18. Separation of RabbitMQ Integration Messaging and Celery Task Workers

## Context
The platform requires both durable, event-driven domain integration (e.g. order placed, projection updated) and scheduled/asynchronous operational jobs (e.g. reservation expiration scans, bulk reconciliation, outbox dispatch).

## Decision
We cleanly separate:
1. **RabbitMQ Topic Exchange (`arad.events`):** Used for domain integration events and inter-context pub/sub messaging with explicit routing keys (`{context}.{entity}.{event}`), retry queues, and dead-letter exchanges (`arad.dlx`).
2. **Celery Workers & Beat Scheduler:** Used for operational tasks, cron-like schedules (periodic reservation expiration sweep), bulk projection reindexing, and database reconciliation jobs.

## Alternatives Considered
- *Using Celery for everything (including domain events):* Rejected because Celery couples message format to Python task signatures and lacks native AMQP topic routing topology flexibility.
- *Using RabbitMQ + custom daemon loops for everything:* Rejected because custom daemons lack Celery's mature retry policies, process supervision, and cron scheduling capabilities.

## Consequences
- Clean separation between business domain events and operational background jobs.
- Clear mental model and troubleshooting paths for developers and operators.
