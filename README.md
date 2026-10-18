# Arad — Distributed Order and Inventory Orchestration Platform

[![CI Quality & Test Suite](https://github.com/aradkj/warehouse/actions/workflows/ci.yml/badge.svg)](https://github.com/aradkj/warehouse/actions/workflows/ci.yml)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type Checker: Mypy](https://img.shields.io/badge/type%20checker-mypy-blue.svg)](https://mypy-lang.org/)
[![Test Suite: 573 Passed](https://img.shields.io/badge/tests-573%20passed-brightgreen.svg)]()
[![Phase: 1-5 Complete](https://img.shields.io/badge/phase-1--5%20complete-success.svg)]()

Production-grade, multi-tenant distributed orchestration platform coordinating orders, atomic inventory reservations across multiple warehouses, external carrier fulfillment, near-real-time search projections, and attribute-based access control (ABAC).

Built as a **modular monolith with independent asynchronous workers**, using Hexagonal Architecture (Ports and Adapters) with PostgreSQL as the authoritative system of record.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Business Problem](#2-business-problem)
3. [Architecture](#3-architecture)
4. [Bounded Contexts](#4-bounded-contexts)
5. [Technology Stack](#5-technology-stack)
6. [Repository Structure](#6-repository-structure)
7. [Service & Container Architecture](#7-service--container-architecture)
8. [PostgreSQL Architecture](#8-postgresql-architecture)
9. [Inventory Concurrency Model](#9-inventory-concurrency-model)
10. [Transactional Outbox](#10-transactional-outbox)
11. [RabbitMQ Topology](#11-rabbitmq-topology)
12. [Inbox & Consumer Idempotency](#12-inbox--consumer-idempotency)
13. [Celery Workers & Scheduler](#13-celery-workers--scheduler)
14. [Redis Caching](#14-redis-caching)
15. [Elasticsearch Disposable Projections](#15-elasticsearch-disposable-projections)
16. [Search Capabilities](#16-search-capabilities)
17. [ABAC & RBAC Authorization](#17-abac--rbac-authorization)
18. [Tenant Isolation](#18-tenant-isolation)
19. [PostgreSQL Row-Level Security (RLS)](#19-postgresql-row-level-security-rls)
20. [Audit Architecture](#20-audit-architecture)
21. [API Overview](#21-api-overview)
22. [Testing Strategy](#22-testing-strategy)
23. [Current Test Count & Metrics](#23-current-test-count--metrics)
24. [CI Workflow](#24-ci-workflow)
25. [Docker Instructions](#25-docker-instructions)
26. [Database Migration Instructions](#26-database-migration-instructions)
27. [Local Development](#27-local-development)
28. [Security Guarantees](#28-security-guarantees)
29. [Consistency Guarantees](#29-consistency-guarantees)
30. [Failure Recovery & Resiliency](#30-failure-recovery--resiliency)
31. [Specification Compliance](#31-specification-compliance)
32. [Phase Status](#32-phase-status)
33. [Known Limitations](#33-known-limitations)
34. [Phase 5 Roadmap (Deferred Scope)](#34-phase-5-roadmap-deferred-scope)

---

## 1. Project Overview

The Warehouse platform orchestrates high-volume retail fulfillment across physical stores, distribution centers, and digital channels. It resolves the core challenge of distributed retail: **preventing overselling under high concurrency while remaining resilient to network partitions, broker outages, and external carrier failures.**

---

## 2. Business Problem

Retailers operate under demanding conditions:
- **Concurrent Stock Battles:** Flash sales cause thousands of concurrent checkout attempts on the last available stock item.
- **Asynchronous Workflows:** Carrier integration, shipping labels, and payment confirmations take seconds or minutes and cannot block HTTP checkout threads.
- **Partial Failure & Network Partitions:** Message brokers, caches, or search nodes can crash without halting transactional operations.
- **Multi-Tenancy & Regulatory Compliance:** Distinct tenant datasets must remain isolated at both application and database engine layers.

---

## 3. Architecture

The platform follows **Hexagonal Architecture (Ports and Adapters)** organized as a modular monolith:

```text
Inbound Adapters (FastAPI, RabbitMQ Consumers, Celery Tasks)
                     │
                     ▼
Application Layer (Commands, Queries, Use Cases, Transaction Boundaries)
                     │
                     ▼
Domain Layer (Aggregates, Value Objects, Domain Events, Invariants)
                     │
                     ▼
Outbound Ports (Repository, Publisher, Cache, Search, Provider Interfaces)
                     │
                     ▼
Outbound Adapters (PostgreSQL, Redis, Elasticsearch, RabbitMQ)
```

- **Domain Isolation:** The domain layer has zero third-party dependencies (no FastAPI, no SQLAlchemy, no Redis, no RabbitMQ).
- **Inward Dependency Rule:** Adapters implement interfaces defined by application ports.

---

## 4. Bounded Contexts

| Bounded Context | Status | Key Aggregates | Core Responsibilities |
|---|---|---|---|
| **Identity & Access** | Complete | `User`, `Role`, `AccessPolicy`, `Tenant`, `TenantMembership` | Authentication (Argon2id, JWT), RBAC, dynamic ABAC evaluation, warehouse scoping. |
| **Catalog** | Complete | `Product`, `Sku` | Multi-channel catalog, searchable attributes, category hierarchy. |
| **Inventory** | Complete | `InventoryItem`, `Warehouse`, `Reservation` | Stock on-hand/reserved/allocated tracking, atomic allocation, expiration sweeps. |
| **Ordering** | Complete | `Order`, `OrderLine` | Order lifecycle transitions, price snapshots, reservation verification. |
| **Fulfillment** | Complete | `Fulfillment`, `FulfillmentTask` | Warehouse task dispatch, carrier tracking, retries, compensation. |
| **Search & Reporting**| Complete | `SearchDocument` projections | Denormalized projections for Products, Orders, Exceptions, and Audit logs. |
| **Audit** | Complete | `AuditLog` | Durable append-only audit records with correlation tracking and actor context. |
| **Frontend SPA** | Complete | React 18, TypeScript 5, Vite 6 | Administrative UI for inventory, orders, catalog, fulfillment, policies, and audit. |

---

## 5. Technology Stack

- **Runtime:** Python 3.12+ (tested on Python 3.13)
- **Web Framework:** FastAPI, Uvicorn, Pydantic v2
- **Frontend SPA:** React 18, TypeScript 5, Vite 6, React Router 6, Lucide Icons, Vitest
- **Authoritative Database:** PostgreSQL 16+ with asyncpg, SQLAlchemy 2 (asyncio), and Alembic
- **Distributed Cache:** Redis 7+ (cache-aside, tenant keys, stampede protection)
- **Message Broker:** RabbitMQ 3.13+ (topic exchange, durable queues, retry/DLQ topology)
- **Task Worker & Scheduler:** Celery 5.4+, Celery Beat
- **Search Engine:** Elasticsearch 8.x (zero-downtime aliases, disposable projections)
- **Security & Crypto:** Argon2id, PyJWT, PostgreSQL Row-Level Security (RLS)
- **Testing & Quality:** pytest, pytest-asyncio, Hypothesis, Vitest, Ruff, Mypy (strict)

---

## 6. Repository Structure

```text
src/
├── shared/             # Domain primitives, IDs, UoW, cache, messaging envelopes
├── identity/           # Authentication, RBAC, ABAC policy engine, tenant memberships
├── catalog/            # Product and SKU aggregates, attributes, pricing
├── inventory/          # Warehouses, inventory items, reservations, row-level locking
├── ordering/           # Orders, order lines, status transitions, idempotency
├── fulfillment/        # Fulfillment aggregate, tasks, carrier dispatch, compensation
├── search/             # Elasticsearch mappings, aliases, projectors, reconciliation
├── audit/              # Immutable audit logging port and repository
└── bootstrap/          # FastAPI entry, dependency wiring, Celery workers, routers
tests/
├── unit/               # Pure domain, value object, and architecture tests
├── application/        # Use-case orchestration tests with fake ports
├── integration/        # Real container tests (Postgres, Redis, RabbitMQ, Elasticsearch)
├── concurrency/        # Multi-worker concurrent race condition tests
└── api/                # End-to-end HTTP API tests with auth and tenancy
migrations/             # Alembic forward/rollback database schema revisions
docs/                   # Comprehensive technical documentation, ADRs, compliance matrix
```

---

## 7. Service & Container Architecture

Managed via `docker-compose.yml`:
- `arad-api`: FastAPI REST API handling inbound requests.
- `arad-postgres`: PostgreSQL 16 authoritative system of record.
- `arad-rabbitmq`: RabbitMQ broker with Management UI (`localhost:15672`).
- `arad-redis`: Redis cache and distributed locking backend.
- `arad-elasticsearch`: Elasticsearch 8.x search cluster (`localhost:9200`).
- `arad-outbox-relay`: Background relay worker claiming outbox events via `SKIP LOCKED`.
- `arad-celery-worker`: Worker executing projection updates and async tasks.
- `arad-celery-beat`: Scheduler triggering periodic reservation expirations.
- `test`: Isolated ephemeral test execution container with independent test database.

---

## 8. PostgreSQL Architecture

PostgreSQL is the single authoritative system of record:
- **Transaction-per-Use-Case:** Every command executes inside a single `SqlAlchemyUnitOfWork`.
- **Optimistic Concurrency:** Aggregate tables carry `version: int` columns. Concurrency conflicts trigger `ConcurrencyConflict` (HTTP 409).
- **Deterministic ID Ordering:** High-contention locks sort resource IDs lexicographically before locking to eliminate deadlocks.
- **Time-Based Partitioning Design:** Documented monthly range partitioning (`PARTITION BY RANGE (created_at)`) for high-volume `outbox_events` and `audit_logs` tables.

---

## 9. Inventory Concurrency Model

Overselling is physically impossible:
1. Available stock invariant: $\text{Available} = \text{On Hand} - \text{Reserved} - \text{Allocated} \ge 0$.
2. Inbound reservation requests lock target rows using `SELECT ... FOR UPDATE`.
3. If concurrent requests compete for the last unit, exactly one transaction succeeds; competing transactions observe zero available stock and reject cleanly.
4. Verified by automated concurrency test suites running parallel async tasks.

---

## 10. Transactional Outbox

Prevents dual-write discrepancies between the database and the message broker:
1. Aggregate mutations and domain events are written to `outbox_events` in the **same database transaction**.
2. If RabbitMQ is down, the database transaction commits safely and events buffer in PostgreSQL.
3. The outbox relay worker polls `outbox_events` using `SELECT ... FOR UPDATE SKIP LOCKED` and publishes to RabbitMQ with publisher confirmations.
4. Records transition to `PUBLISHED` only after RabbitMQ confirms broker delivery.

---

## 11. RabbitMQ Topology

- **Exchange:** `arad.events` (topic, durable).
- **Routing Key Format:** `{context}.{entity}.{event_name}` (e.g. `ordering.order.confirmed`).
- **Dead-Letter Exchange (DLX):** `arad.dlx` with dead-letter routing for exhausted retries.
- **Retry Mechanism:** Messages failing due to transient errors route to `arad.retry.{queue}` with TTL backoff before returning to the main queue.

---

## 12. Inbox & Consumer Idempotency

Downstream consumers implement the **Inbox Pattern**:
1. Incoming messages are recorded in `inbox` with unique constraint on `(message_id, consumer_name)`.
2. Duplicate deliveries (arising from network redeliveries or broker failover) hit the unique constraint and are acknowledged without re-executing domain operations.
3. Guarantees **exactly-once processing semantics** on top of at-least-once transport.

---

## 13. Celery Workers & Scheduler

- **Reservation Expiration Sweeper:** Celery Beat periodically triggers a sweep identifying expired reservations and releasing held inventory.
- **Projection Reindex & Reconciliation:** Background workers rebuild search projections and verify aggregate checksum consistency between PostgreSQL and Elasticsearch.
- **Fail-Closed Worker Security:** Background jobs execute under explicit `SystemPrincipal` credentials.

---

## 14. Redis Caching

Implements cache-aside with strict tenancy safeguards:
- **Tenant-Prefixed Keys:** All keys follow `{tenant_id}:{context}:{entity}:{id}:{version}`.
- **Explicit TTLs:** Mandatory expiration windows protect against stale reads.
- **Stampede Protection:** Probabilistic early expiration and distributed negative caching.
- **Graceful Fallback:** If Redis is down, calls log a warning and fall back to PostgreSQL with zero downtime.

---

## 15. Elasticsearch Disposable Projections

Elasticsearch is strictly a disposable read projection:
- Rebuildable from PostgreSQL at any time.
- Managed across four distinct versioned indices:
  1. `arad_products_v1` (alias `products`)
  2. `arad_orders_v1` (alias `orders`)
  3. `arad_fulfillment_exceptions_v1` (alias `fulfillment_exceptions`)
  4. `arad_audit_v1` (alias `audit`)
- Zero-downtime reindexing via atomic index alias swapping.

---

## 16. Search Capabilities

- **Order Search:** Search by customer reference, SKU, order status, warehouse, and date range.
- **Product Search:** Full-text keyword search across product names, descriptions, and dynamic attribute filters.
- **Fulfillment Exception Search:** Search by failure reason, carrier, and warehouse.
- **Audit Search:** Search policy evaluations, decision reasons, and actor actions.
- **Mandatory Tenant Scoping:** Every Elasticsearch query hardcodes a top-level `{"term": {"tenant_id": ...}}` filter.

---

## 17. ABAC & RBAC Authorization

Dual-layer authorization engine:
1. **RBAC:** Enforces baseline permissions (e.g. `orders.create`, `fulfillments.modify`).
2. **ABAC:** Dynamically evaluates subject attributes (e.g. `assigned_warehouses`), resource attributes (e.g. `warehouse_id`), and environment context.
3. **Warehouse Scoping:** An operator assigned to warehouse `LAX` is rejected when attempting to modify a fulfillment for warehouse `SEA`.
4. **Audit Trail:** Every access evaluation records an immutable audit entry with actor, decision, and explicit decision reason.

---

## 18. Tenant Isolation

Tenant isolation is enforced across all architectural layers:
- **API:** Authentication extracts tenant ID from signed JWT claims.
- **Application:** Use cases reject mismatched cross-tenant IDs.
- **Cache:** Redis keys are partitioned by tenant prefix.
- **Search:** Elasticsearch queries enforce tenant filtering.
- **Database:** PostgreSQL Row-Level Security (RLS) blocks cross-tenant reads and writes.

---

## 19. PostgreSQL Row-Level Security (RLS)

All tenant-owned tables have RLS enforced at the database engine level:
```sql
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_orders ON orders
    AS RESTRICTIVE
    USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
```
Even if an application query omits `WHERE tenant_id = ...`, PostgreSQL returns zero rows unless the session variable `app.current_tenant_id` matches.

---

## 20. Audit Architecture

The platform maintains an immutable, append-only audit trail in the `audit_logs` table:
- Captures actor identity, action type, resource type, resource ID, policy decision, decision reason, and correlation ID.
- Accessible via `GET /v1/audit/events` with tenant scoping and cursor pagination.
- Retained for 90 days in operational storage prior to cold-storage export.

---

## 21. API Overview

Key REST endpoints:

```text
# Ordering
POST   /v1/orders                               Create pending order (Idempotency-Key)
GET    /v1/orders/{order_id}                    Retrieve order status and lines
POST   /v1/orders/{order_id}/confirm            Confirm order with reservations
POST   /v1/orders/{order_id}/cancel             Cancel order and release stock
GET    /v1/orders/search                        Elasticsearch order search

# Inventory
POST   /v1/inventory/adjustments                Adjust warehouse stock levels
GET    /v1/inventory/availability               Query available stock by SKU
POST   /v1/reservations                         Reserve stock for checkout
DELETE /v1/reservations/{reservation_id}        Release held reservation

# Fulfillment
POST   /v1/fulfillments                         Allocate fulfillment tasks
GET    /v1/fulfillments/{fulfillment_id}        Get fulfillment status and carrier info
POST   /v1/fulfillments/{fulfillment_id}/retry  Retry failed carrier dispatch

# Identity & Security
POST   /v1/auth/token                           Acquire JWT bearer token
POST   /v1/access/policies                      Create dynamic ABAC policies
GET    /v1/audit/events                         Query immutable audit trail

# Search & Catalog
GET    /v1/search/products                      Full-text faceted product search
GET    /v1/search/orders                        Elasticsearch order query
GET    /v1/search/audit                         Tenant-isolated audit log search
```

---

## 22. Testing Strategy

The test suite enforces full pyramid coverage across pure unit, application orchestration, concurrency, integration, security, and BDD acceptance:
- **Unit Tests:** Verify aggregate invariants without database or network I/O.
- **Concurrency Tests:** Multi-threaded async workers verify race condition safety.
- **Integration Tests:** Execute against real Dockerized PostgreSQL, Redis, RabbitMQ, and Elasticsearch services.
- **BDD Scenarios:** Validate end-to-end user journeys (e.g. concurrent last-unit reservation, idempotent replay, outbox delivery across broker restarts).

---

## 23. Current Test Count & Metrics

Execution in clean environment against active services:

```text
======================= 573 passed, 1 skipped in 142.58s ========================
```

- **Total Automated Tests:** **573 passed** (1 skipped, 0 failed, 0 errors across 574 collected tests).
- **Ruff Code Style:** **0 errors** (clean, formatted).
- **Mypy Static Typing:** **0 errors** (strict type safety in `src/`).
- **Architecture Rules:** Enforced via `test_architecture.py` (dependency direction verified).

---

## 24. CI Workflow

Defined in `.github/workflows/ci.yml`:
1. **Lint & Static Analysis:** Executes `ruff check` and `mypy src/`.
2. **Container Composition:** Validates `docker compose config --quiet`.
3. **Database Migration:** Executes Alembic forward migrations against a clean database.
4. **Test Suite Execution:** Runs the complete test suite in the isolated test service.
5. **Multi-Stage Docker Builds:** Builds both `runtime` and `test` Docker targets.

---

## 25. Docker Instructions

### Start All Services

```bash
docker compose up --build -d
```

### Check Service Health

```bash
curl -s http://localhost:8000/healthz   # {"status":"ok","environment":"production"}
curl -s http://localhost:8000/readyz    # {"status":"ready","checks":{"database":"ok","cache":"ok"}}
```

### Access Management Interfaces

- **Web Application UI:** `http://localhost:8000/` (Integrated SPA)
- **FastAPI OpenAPI Docs:** `http://localhost:8000/docs`
- **RabbitMQ Management UI:** `http://localhost:15672` (Username: `arad` / Password: `arad`)
- **Elasticsearch API:** `http://localhost:9200`

---

## 26. Database Migration Instructions

### Apply Migrations (Forward)

```bash
docker compose run --rm api alembic upgrade head
```

### Migration Recovery & Rollback Strategy

> [!WARNING]
> **Forward-Only Migration Policy:** Migration `0009_harden_rls_and_roles` establishes least-privilege PostgreSQL runtime roles, row-level security policies, and permission revocation that cannot safely be downgraded automatically. Attempting `alembic downgrade -1` will raise an intentional `RuntimeError` to prevent security regression.

To recover from a schema defect or deployment failure:
1. **Forward-Fix Migration (Preferred):** Author and apply a new forward migration (`0010_...`) that rectifies the schema or permissions.
   ```bash
   docker compose run --rm api alembic upgrade head
   ```
2. **Point-in-Time Disaster Recovery Restore:** For catastrophic failures, restore the pre-migration verified database backup into an isolated restore target:
   ```bash
   python scripts/restore_postgres.py backups/arad_backup_<timestamp>.sql --database arad_restore --expected-revision 0009_harden_rls_and_roles
   ```

### Create New Migration Revision

```bash
docker compose run --rm api alembic revision -m "add_table"
```

---

## 27. Local Development

### Prerequisites

- Python 3.12+
- Docker and Docker Compose
- uv or pip

### Run Local Tests via Docker

```bash
docker compose run --rm test pytest
```

### Run Ruff and Mypy

```bash
docker compose run --rm test ruff check src/ tests/
docker compose run --rm test mypy src/
```

---

## 28. Security Guarantees

1. **Fail-Closed Authorization:** Access is denied unless an explicit RBAC permission and matching ABAC policy allow it.
2. **PostgreSQL RLS Defense-in-Depth:** Queries without tenant context return 0 rows.
3. **Password Security:** Hashes passwords using Argon2id with 64MB memory cost.
4. **Credential Redaction:** Access logs and error envelopes redact Authorization headers and sensitive secrets.

---

## 29. Consistency Guarantees

1. **No Negative Stock:** Verified under concurrent transactions; inventory never drops below zero.
2. **No Lost Outbox Events:** Committed domain events remain in PostgreSQL until confirmed by RabbitMQ.
3. **Exactly-Once Business Semantics:** Inbox deduplication ensures retried messages are not processed twice.
4. **Optimistic Concurrency:** Aggregate updates fail with `ConcurrencyConflict` if versions do not match.

---

## 30. Failure Recovery & Resiliency

- **RabbitMQ Outage:** Outbox relay buffers events in PostgreSQL; publishing resumes automatically upon broker reconnection.
- **Redis Outage:** Caching gracefully degrades to direct PostgreSQL queries with zero downtime.
- **Elasticsearch Outage:** Search projections buffer in retry queues or resync via scheduled reconciliation.
- **Worker Crash:** RabbitMQ re-queues unacknowledged messages; Inbox ensures safe re-delivery.

---

## 31. Specification Compliance

A complete traceability matrix mapping all 25 sections of `docs/specification.md` is available in:
[`docs/spec-compliance.md`](docs/spec-compliance.md)

---

## 32. Phase Status

- **Phase 1: Domain Foundation** — **COMPLETED**
- **Phase 2: Reliable Asynchronous Processing** — **COMPLETED**
- **Phase 3: Search and Caching** — **COMPLETED**
- **Phase 4: Security and Tenancy** — **COMPLETED**
- **Phase 5: Production Hardening** — **COMPLETED**

---

## 33. Phase 5 Production Hardening Capabilities

1. **Performance & Load Testing Suite**:
   - `scripts/run_benchmarks.py`: Reproducible async load runner supporting smoke, baseline, stress, and spike concurrency profiles.
   - Verified P50/P95 latencies and throughput across catalog reads, inventory availability, order creation, order replay, filtered search, and mixed read/write workloads.
2. **Observability Stack**:
   - Standard Prometheus metrics exposition format on `GET /metrics` (`src/bootstrap/metrics.py`).
   - Deep dependency readiness probe on `GET /readyz` verifying PostgreSQL connection, pool stats, and Alembic migration version.
   - W3C TraceContext (`traceparent`) parsing and propagation across HTTP boundaries.
   - Prometheus alerting rules (`docker/prometheus/alerts.yml`) and Grafana dashboards (`docker/grafana/dashboards/platform_overview.json`).
3. **Disaster Recovery (Backup & Restore)**:
   - `scripts/backup_postgres.py`: Automated PostgreSQL logical backups with SHA-256 checksums, atomic rename, and retention management.
   - `scripts/restore_postgres.py`: Verified restoration into isolated databases with invariant and migration validation.
   - Documented RPO (< 15 min) and RTO (< 30 min) in [`docs/architecture/disaster-recovery.md`](docs/architecture/disaster-recovery.md).
4. **Operational Runbooks**:
   - 21 verified incident runbooks in [`docs/runbooks/`](docs/runbooks/README.md) covering API outages, pool exhaustion, outbox stalls, DLQ growth, worker failures, overselling, tenant isolation incidents, credential rotation, and resource exhaustion.
5. **Container & Deployment Security**:
   - Production Docker Compose manifest (`docker-compose.prod.yml`) enforcing rootless execution (`user: "1001:1001"`), `security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`, `read_only: true` with tmpfs, and resource limits.
   - Production secret validation enforcing high-entropy 32+ character JWT secrets.
6. **Property-Based Testing & Concurrency Invariants**:
   - Hypothesis state-machine property tests (`tests/unit/test_domain_properties.py`) validating non-negative inventory and forward-only state machines.
   - Multi-tenant parallel order race integration tests (`tests/concurrency/test_inventory_concurrency.py`).
