# Architecture Decision Records (ADRs)

Architecture Decision Records for the Warehouse platform, documenting key decisions, context, consequences, and rejected alternatives.

| ADR | Title | Summary |
|---|---|---|
| [0001](0001-postgresql-system-of-record.md) | PostgreSQL is the system of record | Stock correctness lives in transactions and row locks, never in a cache or an app lock. |
| [0002](0002-modular-monolith-before-distribution.md) | A modular monolith with enforced boundaries | One deployable process, isolated bounded contexts, dependencies pointing inward. |
| [0003](0003-product-and-sku-are-separate-aggregates.md) | Product and SKU are separate aggregates | Independent lifecycles, separate version counters, no write amplification. |
| [0004](0004-order-lifecycle.md) | Order lifecycle | Forward-only lifecycle transitions; confirmation maintains stock reservation. |
| [0005](0005-migration-strategy.md) | Migration strategy | Forward and backward Alembic migrations tested automatically in CI. |
| [0006](0006-idempotency-semantics.md) | Idempotency semantics | Tenant-scoped idempotency keys claimed atomically within business transactions. |
| [0007](0007-inventory-allocation-and-concurrency.md) | Inventory allocation and concurrency | `SELECT FOR UPDATE` in deterministic ID order to eliminate race conditions and deadlocks. |
| [0008](0008-published-ports.md) | Published ports | Bounded contexts expose published contracts; providers own their DTOs. |
| [0009](0009-in-process-cross-context-adapters.md) | In-process cross-context adapters | Clean seams enabling future microservice extraction without domain refactoring. |
| [0010](0010-multi-tenancy.md) | Multi-tenancy | Shared schema with mandatory `tenant_id` column on all tenant-owned entities. |
| [0011](0011-redis-cache-foundation.md) | Redis cache foundation | Cache-aside, tenant-prefixed keys, TTLs, stampede protection, graceful fallback. |
| [0012](0012-elasticsearch-search-projection.md) | Elasticsearch search projection | Event-driven read models, optimistic versioning, selective cache invalidation. |
| [0013](0013-search-api-reindex-and-reconciliation.md) | Search API, reindexing & reconciliation | Tenant-safe queries, zero-downtime blue/green alias swaps, unidirectional repair. |
| [0014](0014-identity-authentication-and-memberships.md) | Identity, authentication & memberships | Argon2id hashing, JWT sessions, refresh token families, principal context. |
| [0015](0015-authorization-rbac-and-abac.md) | Authorization RBAC and ABAC | Fail-closed dynamic attribute evaluation, warehouse-scoped operator checks. |
| [0016](0016-transactional-outbox-and-relay.md) | Transactional outbox and relay | Atomic outbox commits, non-blocking `SKIP LOCKED` relay, broker confirmations. |
| [0017](0017-inbox-and-idempotent-consumer.md) | Inbox pattern and idempotent consumers | Deduplication via database-backed inbox table, atomic lease acquisition. |
| [0018](0018-rabbitmq-and-celery-separation.md) | RabbitMQ and Celery separation | RabbitMQ for domain pub/sub; Celery for operational jobs and cron sweeps. |
| [0019](0019-elasticsearch-as-disposable-projection.md) | Elasticsearch as disposable projection | Search cluster as a rebuildable projection; PostgreSQL remains authoritative. |
| [0020](0020-postgresql-rls-defense-in-depth.md) | PostgreSQL RLS defense in depth | Engine-level row security blocking cross-tenant access even if SQL lacks tenant clause. |
| [0021](0021-fulfillment-bounded-context-and-compensation.md) | Fulfillment context and compensation | Warehouse allocation, carrier dispatch, retry tracking, and compensation workflow. |
