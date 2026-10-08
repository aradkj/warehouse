# 11. Redis infrastructure and cache foundation

* Status: Accepted
* Date: 2026-10-07
* Phase: 3 (Part 1)

## Context

Phase 3 introduces read acceleration and search projections. While PostgreSQL remains the immutable system of record for inventory, reservations, and orders, read operations (such as product queries, catalog lookups, and idempotent requests) benefit from low-latency caching.

The architectural requirements dictate that:
1. PostgreSQL is the authoritative system of record. Inventory and order consistency must never depend on Redis.
2. The domain must remain strictly infrastructure-free (no direct imports or knowledge of Redis).
3. The cache strategy must follow cache-aside with explicit TTLs.
4. Multi-tenancy must be enforced at the key level to prevent cross-tenant cache contamination.
5. Protection against cache stampedes must be built-in.
6. The system must support negative caching for non-existent resources.
7. The application must degrade gracefully when Redis is unavailable or corrupted, failing back to PostgreSQL without interrupting business operations.

## Decision

We introduce **Redis infrastructure and a port-based cache foundation**:

1. **`CachePort` Protocol**: Defined in `src/shared/ports/cache.py` as an outbound hexagonal port exposing typed operations (`get`, `get_entry`, `set`, `set_negative`, `delete`, `exists`, `expire`, `acquire_lock`, `release_lock`, `get_or_set`).
2. **`RedisCacheAdapter`**: Implemented in `src/shared/infrastructure/cache/redis.py` using `redis.asyncio` with connection pooling.
3. **`InMemoryCache`**: Implemented in `src/shared/infrastructure/cache/memory.py` for pure unit and application-layer tests without infrastructure.
4. **Tenant-Scoped, Versioned Keys**: `CacheKeyBuilder` in `src/shared/infrastructure/cache/keys.py` formats all keys as `v{version}:{tenant_id}:{resource}:{identifier}`.
5. **Deterministic JSON Serialization**: `CacheSerializer` uses canonical JSON encoding. Python `pickle` is strictly forbidden. Corrupted or malformed cache payloads are safely logged and treated as cache misses, triggering background key eviction.
6. **Graceful Degradation**: Redis connection errors, timeouts, and network partitions are logged at warning level and downgraded to cache misses or write no-ops.
7. **Stampede Protection**: Short-lived distributed locks with owner tokens (`SET ... NX PX`) and Lua atomic releases coordinate cache population under high contention (`get_or_set`).
8. **Negative Caching**: Explicit `NegativeCacheMarker` with a short TTL caches negative query results to avoid cache penetration.
9. **Ephemeral Containerization**: Added an official `redis:7-alpine` service with a `redis-cli ping` health check to Docker Compose, configured as an ephemeral cache without persistent volume overhead.

## Consequences

* **Read Performance**: High-throughput read endpoints can be accelerated via cache-aside.
* **Transactional Invariants Preserved**: State changes, inventory locks, and financial invariants continue to be verified against PostgreSQL.
* **Resilience**: Redis outages degrade read performance to direct database queries without bringing down the application.
* **Clean Architecture Maintained**: Domain entities remain pure; application services interact only through `CachePort`.

## Alternatives Considered

* **In-memory process cache (e.g. `cachetools`)** — rejected: does not scale horizontally across multiple API container instances and risks inconsistent state.
* **Redis as a source of truth for stock / reservations** — rejected: violates Core Guarantee #1 ([ADR-0001](0001-postgresql-system-of-record.md)).
* **Pickle serialization** — rejected: severe security risks, language lock-in, and fragile schema evolutions.
