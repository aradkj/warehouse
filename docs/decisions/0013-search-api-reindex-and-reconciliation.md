# 13. Search API, Query Port, Zero-Downtime Reindexing & Reconciliation

* Status: Accepted
* Date: 2026-10-08
* Phase: 3 (Part 3 - Final)

## Context

Following Redis caching (Phase 3 Part 1) and Elasticsearch search projections (Phase 3 Part 2), the final part of Phase 3 establishes the complete read-query lifecycle, client search API, zero-downtime index reindexing, and drift reconciliation.

The architectural requirements dictate that:
1. **PostgreSQL is the Sole Authoritative Source of Truth**: Search and reconciliation must never mutate PostgreSQL domain records. Repair is strictly unidirectional: PostgreSQL -> Elasticsearch.
2. **Search is Read-Only**: The search API must never mutate domain state or transactional outbox records.
3. **Strict Multi-Tenant Isolation**: Normal API users derive tenant identity solely from authenticated request context (`RequestContext.require_tenant()`). Cross-tenant search by parameter tampering is impossible. All Elasticsearch queries mandatorily filter by `tenant_id`. Cache keys partition by tenant.
4. **Decoupled API & Query Port**: The application layer and HTTP API must never expose Elasticsearch DSL, `_source`, shard metadata, or raw cluster exceptions. Queries and responses use explicit, clean DTOs with safe sorting allowlists and bounded pagination.
5. **Cache-Aside Search with Graceful Degradation**: Search results are cached in Redis under tenant-partitioned keys (`v1:{tenant}:search:products:{hash}`) with bounded TTL. If Redis is unavailable or fails, search degrades gracefully directly to Elasticsearch.
6. **Zero-Downtime Reindexing (Blue/Green Alias Strategy)**: Rebuilding the search index reads authoritative records from PostgreSQL in memory-bounded batches, bulk-indexes into a new timestamped index (`products-YYYYMMDDHHMMSS`), verifies completeness, and atomically swaps the production alias (`products`). Partial or failed rebuilds never switch into production and are automatically cleaned up.
7. **Safe Reconciliation & Drift Detection**: Detects missing documents, stale versions, and tenant mismatches between PostgreSQL and Elasticsearch without unbounded memory usage. Unidirectional repair restores missing or stale records in Elasticsearch.
8. **Administrative Safeguards**: Reindex and reconciliation operations are restricted to internal/admin contexts or CLI/Celery background tasks (`arad-worker reindex`, `arad-worker reconcile`).

## Decision

We introduce the complete **Search Application Layer, Search API, Zero-Downtime Reindexing, and Drift Reconciliation**:

1. **`SearchIndexer` Query Extensions & `CatalogSearchReader` Port**:
   - `SearchIndexer` protocol (`src/search/ports/indexer.py`) extended with `search(query: SearchQuery) -> SearchPageResult`, `bulk_index`, `switch_alias`, `list_indices_for_alias`, `delete_index`, `count_documents`, and `get_document_keys`.
   - `CatalogSearchReader` protocol (`src/search/ports/reader.py`) declared in the search boundary for reading authoritative product search documents and counts from PostgreSQL.
2. **PostgreSQL Adapter (`SqlAlchemyCatalogSearchReader`)**:
   - Implemented in `src/search/adapters/postgres.py`. Reads products and joined SKUs via async SQLAlchemy, streaming in bounded batches with eager relationships to avoid N+1 queries.
3. **Elasticsearch Adapter Query & Maintenance Implementation**:
   - Implemented in `src/search/adapters/elasticsearch.py`. Employs explicit Elasticsearch bool queries with mandatory `filter: [{"term": {"tenant_id": query.tenant_id}}]`, optional status and SKU filters, text search across `name`, `description`, `slug`, and `skus.sku_code`, allowlisted sorting (`created_at`, `updated_at`, `name`, `status`, `slug`), and bounded pagination.
   - Provides alias atomic switching via `client.indices.update_aliases`.
   - Supports `force=True` on indexing to forcefully overwrite deleted tombstones during authoritative reconciliation repair.
4. **Search Application Service (`SearchProductsService`)**:
   - Implemented in `src/search/application/service.py`. Handles query sanitization, sorting allowlist validation, deterministic SHA256 query hashing, cache-aside lookups via `CachePort`, and graceful fallback to Elasticsearch when Redis is down.
5. **Zero-Downtime Reindexing Service (`ReindexingService`)**:
   - Implemented in `src/search/application/reindex.py`. Creates new physical index `products-{timestamp}`, bulk-indexes authoritative data in memory-safe batches, verifies indexed count against source, atomically switches the production alias, and purges superseded indices (unless `--retain-old` is specified). Automatically rolls back and purges incomplete indices on error.
6. **Bounded Reconciliation Service (`ReconciliationService`)**:
   - Implemented in `src/search/application/reconciliation.py`. Performs bounded comparison between PostgreSQL and Elasticsearch keys, detects discrepancies (`missing_in_elasticsearch`, `missing_in_postgresql`, `version_mismatch`, `tenant_mismatch`), and offers unidirectional auto-repair (PostgreSQL -> Elasticsearch only). PostgreSQL is never mutated.
7. **FastAPI Search Router & Administrative Endpoints**:
   - Implemented in `src/bootstrap/routers/search.py`:
     - `GET /search/products`: Tenant-scoped product search endpoint (authenticated, tenant derived from token/header).
     - `POST /search/reindex`: Administrative reindex trigger.
     - `POST /search/reconcile`: Administrative reconciliation trigger.
   - Standardized Pydantic schemas in `src/bootstrap/schemas/search.py`.
8. **Operational Worker CLI & Celery Background Tasks**:
   - Added `reindex` and `reconcile` tasks to `src/bootstrap/workers.py` and Celery configuration.
   - CLI commands `python -m bootstrap.workers reindex` and `python -m bootstrap.workers reconcile` with tenant scoping and auto-repair flags.

## Consequences

* **Complete Search Capability**: High-performance, faceted catalog search with sub-millisecond cached responses and bounded pagination.
* **Resilience**: Redis outages degrade seamlessly to Elasticsearch; Elasticsearch outages return standardized HTTP 503 `SearchUnavailable` responses without exposing internal infrastructure details.
* **Source of Truth Integrity**: PostgreSQL remains 100% authoritative. Reindexing and reconciliation guarantee that Elasticsearch read model can always be rebuilt from scratch or repaired from PostgreSQL.
* **Tenant Isolation**: Complete isolation across query generation, cache keys, pagination, reindexing, and reconciliation.
* **Clean Architecture**: Domain and application boundaries remain strictly decoupled from Elasticsearch and Redis.

## Alternatives Considered

* **Direct PostgreSQL querying for search requests** — rejected: full text search, complex multi-field matching, and faceted filters degrade database OLTP performance under production load.
* **In-place reindexing without aliases** — rejected: dropping or wiping active indices during reindexing causes downtime and data loss for active search queries.
* **Bidirectional reconciliation repair** — rejected: PostgreSQL is the sole authoritative system of record; Elasticsearch documents must never overwrite or delete PostgreSQL records.
