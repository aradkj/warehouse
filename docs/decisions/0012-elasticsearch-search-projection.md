# 12. Elasticsearch Search Projection, Event-Driven Indexing & Cache Invalidation

* Status: Accepted
* Date: 2026-10-07
* Phase: 3 (Part 2)

## Context

Following the establishment of Redis caching (Phase 3 Part 1), Phase 3 Part 2 introduces high-performance search capabilities through an event-driven read model powered by Elasticsearch.

The architectural requirements dictate that:
1. **PostgreSQL remains the immutable system of record**. Elasticsearch is strictly a read projection and must never be treated as authoritative for transactional state or consistency.
2. **Hexagonal Architecture & Dependency Inversion**: Domain and application layers must never import Elasticsearch or depend on external search engine drivers.
3. **Event-Driven Projection**: Changes to products and SKUs in the Catalog domain must be asynchronously published via the Transactional Outbox and consumed by a search projector from the `arad.search` RabbitMQ queue.
4. **Multi-Tenant Isolation**: Search documents must strictly isolate tenant data. Cross-tenant pollution, queries, or ID collisions must be prevented through deterministic document IDs (`{tenant_id}:{product_id}`) and tenant filtering.
5. **Idempotent & Ordered Processing**: The projector must handle duplicate and out-of-order event delivery using optimistic versioning (`version_type="external_gte"`) and consumer inbox deduplication.
6. **Selective Cache Invalidation**: As catalog events arrive and search read models update, related Redis cache keys (`product`, `sku`) must be invalidated targetedly without global database flushes (`FLUSHDB` is strictly prohibited).
7. **Resilience & Graceful Degradation**: Failures in Elasticsearch or Redis must not break transactional mutation flows in PostgreSQL or crash consumers fatally.

## Decision

We introduce **Elasticsearch search projections, event-driven indexing, and cache invalidation**:

1. **`SearchIndexer` Port**:
   Defined in `src/search/ports/indexer.py` as an outbound hexagonal protocol exposing `index`, `delete`, `get_document`, `ensure_index`, `ping`, and `close`.
2. **`ElasticsearchIndexAdapter`**:
   Implemented in `src/search/adapters/elasticsearch.py` using `elasticsearch.AsyncElasticsearch`. Uses external optimistic concurrency (`version_type="external_gte"`) to automatically ignore stale, out-of-order events.
3. **`InMemorySearchIndexer`**:
   Implemented in `src/search/adapters/memory.py` for isolated unit tests without external infrastructure dependencies.
4. **Denormalized Read Model & Deterministic IDs**:
   `ProductSearchDocument` defined in `src/search/application/models.py` denormalizes product attributes and embedded SKUs. Document IDs are deterministic: `f"{tenant_id}:{product_id}"`.
5. **Event-Driven `ProductSearchProjector`**:
   Implemented in `src/search/application/projector.py`. Consumes Catalog domain events (`ProductCreated`, `ProductRevised`, `ProductActivated`, `ProductArchived`, `SkuCreated`, `SkuPriceChanged`, `SkuActivated`, `SkuDeactivated`), projects them to `ProductSearchDocument`, updates Elasticsearch, and triggers targeted Redis cache key deletions via `default_key_builder`.
6. **RabbitMQ & Inbox Integration**:
   `arad.search` queue is bound to `product.#` and `sku.#` routing keys. `create_search_projection_consumer` in `src/search/adapters/messaging.py` wires `IdempotentConsumer` with PostgreSQL inbox deduplication to `ProductSearchProjector`.
7. **Worker CLI & Composition Root**:
   Added `search-consumer` command to `src/bootstrap/workers.py` (`arad-worker search-consumer`). Integrated `SearchIndexer` into `src/bootstrap/dependencies.py` and `src/bootstrap/app.py` lifespan management.
8. **Targeted Cache Invalidation**:
   Selective key invalidation via `CachePort.delete(key)` targeting `v1:{tenant}:{resource}:{id}`. Global flush commands remain forbidden.
9. **Docker Compose Service**:
   Added `elasticsearch:8.13.4` single-node service with healthcheck to `docker-compose.yml`, connected to `arad-network`.

## Consequences

* **Sub-millisecond Search Capabilities**: Read projections allow rich text search and faceted querying without putting load on transactional PostgreSQL tables.
* **Eventual Consistency**: Search index and cache updates occur asynchronously via message bus and outbox relay.
* **Tenant Safety**: Shared search indices remain strictly partitioned by deterministic compound document IDs and tenant filters.
* **Zero Hexagonal Leaks**: The core domain remains entirely uncoupled from Elasticsearch, RabbitMQ, and Redis.

## Alternatives Considered

* **Synchronous indexing inside HTTP requests** — rejected: couples transactional latency and availability to Elasticsearch; violates the transactional outbox pattern.
* **PostgreSQL full-text search (tsvector)** — rejected: lacks distributed faceting, nested document aggregations, and Elasticsearch query ecosystem needed for high-scale catalog search.
* **Global cache flushing on catalog updates** — rejected: destroys cache hit ratios and creates cache stampedes on unrelated tenants.
