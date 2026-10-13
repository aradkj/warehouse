# Architecture decision records

Phase 1 decisions, in the order they were made. Each record states the context, the
decision, its consequences and the alternatives that were rejected — including the
ones that were implemented first and then reversed, because the reason matters more
than the outcome.

| ADR | Title | In one line |
| --- | --- | --- |
| [0001](0001-postgresql-system-of-record.md) | PostgreSQL is the system of record | stock correctness lives in transactions and row locks, never in a cache or an app lock |
| [0002](0002-modular-monolith-before-distribution.md) | A modular monolith with enforced boundaries | one process, many contexts, dependencies point inwards, enforced by tests |
| [0003](0003-product-and-sku-are-separate-aggregates.md) | Product and SKU are separate aggregates | two lifecycles, two version counters, no write amplification |
| [0004](0004-order-lifecycle.md) | Order lifecycle | confirmation **keeps** the hold; the units leave the warehouse at fulfilment |
| [0005](0005-migration-strategy.md) | Migration strategy | append-only revisions, every downgrade exercised, drift is a test failure |
| [0006](0006-idempotency-semantics.md) | Idempotency semantics | `(tenant, key)` claimed in the business transaction; replay re-reads the resource |
| [0007](0007-inventory-allocation-and-concurrency.md) | Inventory allocation and concurrency | `FOR UPDATE` in `id` order for allocation, `version` for edits, CHECK constraints as the backstop |
| [0008](0008-published-ports.md) | Published ports | a context exposes one contract module; the provider owns the DTOs |
| [0009](0009-in-process-cross-context-adapters.md) | In-process cross-context adapters | one transaction and real foreign keys now; a client and a saga later, with no domain change |
| [0010](0010-multi-tenancy.md) | Multi-tenancy | shared schema, `tenant_id` on every owned row, RLS deferred to Phase 4 |
| [0011](0011-redis-cache-foundation.md) | Redis cache foundation | cache-aside, tenant-prefixed keys, stampede protection, graceful degradation |
| [0012](0012-elasticsearch-search-projection.md) | Elasticsearch search projection | event-driven read models, optimistic versioning, selective cache invalidation |
| [0013](0013-search-api-reindex-and-reconciliation.md) | Search API, reindexing & reconciliation | tenant-safe queries, cache-aside, blue/green alias reindexing, unidirectional repair |
| [0014](0014-identity-authentication-and-memberships.md) | Identity, authentication & memberships | User & membership aggregates, Argon2id/scrypt, JWT sessions with reuse detection, trusted principal context |

Operational notes live in [`../runbooks/`](../runbooks/).
