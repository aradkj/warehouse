# Arad — Distributed Order and Inventory Orchestration Platform

[![CI Quality & Test Suite](https://github.com/aradkj/warehouse/actions/workflows/ci.yml/badge.svg)](https://github.com/aradkj/warehouse/actions/workflows/ci.yml)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Type Checker: Mypy](https://img.shields.io/badge/type%20checker-mypy-blue.svg)](https://mypy-lang.org/)
[![Test Suite: 687 Passed](https://img.shields.io/badge/tests-687%20passed%20(634%20py%20%2B%2053%20vitest)-brightgreen.svg)]()
[![i18n: FA & EN](https://img.shields.io/badge/i18n-Persian%20%7C%20English-blue.svg)]()
[![Phase: 1-5 Complete](https://img.shields.io/badge/phase-1--5%20complete-success.svg)]()

Production-grade, multi-tenant distributed orchestration platform coordinating orders, atomic inventory reservations across multiple warehouses, external carrier fulfillment, near-real-time search projections, and attribute-based access control (ABAC).

Built as a **modular monolith with independent asynchronous workers**, using Hexagonal Architecture (Ports and Adapters) with PostgreSQL as the authoritative system of record, coupled with an integrated **React 18 / TypeScript / Vite bilingual administrative SPA** supporting full Persian (FA) and English (EN) bidirectional layouts.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Business Problem](#2-business-problem)
3. [Architecture](#3-architecture)
4. [Bounded Contexts](#4-bounded-contexts)
5. [Technology Stack](#5-technology-stack)
6. [Repository Structure](#6-repository-structure)
7. [Service & Container Architecture](#7-service--container-architecture)
8. [Frontend Single-Page Application (SPA) & Internationalization (FA/EN)](#8-frontend-single-page-application-spa--internationalization-faen)
9. [Frontend Routing, Cache Invalidation & Query Preservation](#9-frontend-routing-cache-invalidation--query-preservation)
10. [PostgreSQL Architecture](#10-postgresql-architecture)
11. [Inventory Concurrency Model](#11-inventory-concurrency-model)
12. [Transactional Outbox](#12-transactional-outbox)
13. [RabbitMQ Topology](#13-rabbitmq-topology)
14. [Inbox & Consumer Idempotency](#14-inbox--consumer-idempotency)
15. [Celery Workers & Scheduler](#15-celery-workers--scheduler)
16. [Redis Caching](#16-redis-caching)
17. [Elasticsearch Disposable Projections](#17-elasticsearch-disposable-projections)
18. [Search Capabilities](#18-search-capabilities)
19. [ABAC & RBAC Authorization](#19-abac--rbac-authorization)
20. [Tenant Isolation](#20-tenant-isolation)
21. [PostgreSQL Row-Level Security (RLS)](#21-postgresql-row-level-security-rls)
22. [Audit Architecture](#22-audit-architecture)
23. [API & Frontend Routes Overview](#23-api--frontend-routes-overview)
24. [Testing Strategy](#24-testing-strategy)
25. [Test Suite Metrics & Verification (687 Passed)](#25-test-suite-metrics--verification-687-passed)
26. [CI/CD Workflow & Diagnostic Smoke Tests](#26-cicd-workflow--diagnostic-smoke-tests)
27. [Docker Deployment Instructions](#27-docker-deployment-instructions)
28. [Database Migration & Disaster Recovery](#28-database-migration--disaster-recovery)
29. [Local Development (Backend & Frontend)](#29-local-development-backend--frontend)
30. [Security Guarantees](#30-security-guarantees)
31. [Consistency Guarantees](#31-consistency-guarantees)
32. [Failure Recovery & Resiliency](#32-failure-recovery--resiliency)
33. [Specification Compliance](#33-specification-compliance)
34. [Phase Status & Production Hardening Capabilities](#34-phase-status--production-hardening-capabilities)

---

## 1. Project Overview

The Warehouse platform orchestrates high-volume retail fulfillment across physical stores, distribution centers, and digital channels. It resolves the core challenge of distributed retail: **preventing overselling under high concurrency while remaining resilient to network partitions, broker outages, and external carrier failures.**

---

## 2. Business Problem

Retailers operate under demanding conditions:
- **Concurrent Stock Battles:** Flash sales cause thousands of concurrent checkout attempts on the last available stock item.
- **Asynchronous Workflows:** Carrier integration, shipping labels, and payment confirmations take seconds or minutes and cannot block HTTP checkout threads.
- **Partial Failure & Network Partitions:** Message brokers, caches, or search nodes can crash without halting transactional operations.
- **Multi-Tenancy & Regulatory Compliance:** Distinct tenant datasets must remain isolated at application, caching, search, and database engine layers.
- **Bilingual & Global Operations:** Operations teams require seamless localization in both English (LTR) and Persian (RTL) across administrative and operational interfaces.

---

## 3. Architecture

The platform follows **Hexagonal Architecture (Ports and Adapters)** organized as a modular monolith:

```text
Inbound Adapters (FastAPI, React SPA, RabbitMQ Consumers, Celery Tasks)
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

| Bounded Context | Status | Key Aggregates / Modules | Core Responsibilities |
|---|---|---|---|
| **Identity & Access** | Complete | `User`, `Role`, `AccessPolicy`, `Tenant`, `TenantMembership`, `ServiceAccount` | Authentication (Argon2id, JWT), RBAC, dynamic ABAC evaluation, warehouse scoping, service accounts. |
| **Catalog** | Complete | `Product`, `Sku` | Multi-channel catalog, searchable attributes, category hierarchy, SKU definitions. |
| **Inventory** | Complete | `InventoryItem`, `Warehouse`, `Reservation` | Stock on-hand/reserved/allocated tracking, atomic allocation, expiration sweeps, stock adjustments. |
| **Ordering** | Complete | `Order`, `OrderLine` | Order lifecycle transitions, price snapshots, reservation verification, cancellation and refunds. |
| **Fulfillment** | Complete | `Fulfillment`, `FulfillmentTask` | Warehouse task dispatch, carrier tracking, retries, compensation. |
| **Search & Reporting** | Complete | `SearchDocument` projections | Denormalized projections for Products, Orders, Exceptions, and Audit logs in Elasticsearch. |
| **Audit** | Complete | `AuditLog` | Durable append-only audit records with correlation tracking and actor context. |
| **Frontend SPA** | Complete | React 18, TypeScript 5, Vite 6, Lucide, i18n | Responsive administrative console, complete Persian/English bilingual support, dynamic RTL/LTR layout. |

---

## 5. Technology Stack

### Backend & Infrastructure
- **Runtime:** Python 3.12+ (tested on Python 3.13)
- **Web Framework:** FastAPI, Uvicorn, Pydantic v2
- **Authoritative Database:** PostgreSQL 16+ with asyncpg, SQLAlchemy 2 (asyncio), and Alembic
- **Distributed Cache:** Redis 7+ (cache-aside, tenant keys, stampede protection)
- **Message Broker:** RabbitMQ 3.13+ (topic exchange, durable queues, retry/DLQ topology)
- **Task Worker & Scheduler:** Celery 5.4+, Celery Beat
- **Search Engine:** Elasticsearch 8.x (zero-downtime aliases, disposable projections)
- **Security & Crypto:** Argon2id, PyJWT, PostgreSQL Row-Level Security (RLS)
- **Testing & Quality:** pytest, pytest-asyncio, Hypothesis, Ruff, Mypy (strict)

### Frontend Single-Page Application
- **Core Framework:** React 18, TypeScript 5, Vite 6
- **Routing:** React Router 6 (with search preservation & trailing-slash normalization)
- **Styling & Theming:** Modern CSS design system, dark-mode styling, responsive layout
- **Typography:** Vazirmatn (Persian RTL) and Inter (English LTR)
- **Icons:** Lucide React
- **Internationalization (i18n):** Custom reactive bilingual dictionary (FA / EN) with bidirectional layout management
- **Testing:** Vitest, @testing-library/react, @testing-library/jest-dom, jsdom

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
└── bootstrap/          # FastAPI entry, frontend SPA mount, Celery workers, routers

frontend/
├── src/
│   ├── api/            # Typed HTTP client, endpoint definitions, error formatting
│   ├── app/            # App entry, router configuration, Auth/Tenant/Toast contexts
│   ├── components/     # UI primitives (Buttons, Inputs, Modals, Breadcrumbs, Headers, Sidebars)
│   ├── features/       # Feature pages (Access, Audit, Auth, Catalog, Dashboard, Fulfillment, Inventory, Orders, Search, Tenants)
│   ├── i18n/           # English and Persian translation dictionaries, bilingual hook, and switcher
│   └── styles/         # Global design tokens, typography, RTL utilities, CSS variables
├── tests/
│   ├── components/     # Integration tests (AuthFlow, LanguageSwitch, RootRouting, TenantSwitch, OrderForm, etc.)
│   └── unit/           # Unit tests for utilities and error formatters
└── vite.config.ts      # Vite bundler configuration with Vitest setup

tests/
├── unit/               # Pure domain, value object, and architecture tests
├── application/        # Use-case orchestration tests with fake ports
├── integration/        # Real container tests (Postgres, Redis, RabbitMQ, Elasticsearch)
├── concurrency/        # Multi-worker concurrent race condition tests
└── api/                # End-to-end HTTP API tests with auth, tenancy, and frontend mount

migrations/             # Alembic forward database schema revisions (up to 0009_harden_rls_and_roles)
scripts/                # Load tests (run_benchmarks.py), backup & disaster recovery (backup_postgres.py, restore_postgres.py)
docs/                   # Architecture documentation, ADRs, compliance matrix, and runbooks
```

---

## 7. Service & Container Architecture

Managed via `docker-compose.yml`:
- `arad-api`: FastAPI REST API + compiled frontend SPA served at `http://localhost:8000/`.
- `arad-postgres`: PostgreSQL 16 authoritative system of record (`localhost:5432`).
- `arad-rabbitmq`: RabbitMQ broker with Management UI (`localhost:15672`).
- `arad-redis`: Redis cache and distributed locking backend (`localhost:6379`).
- `arad-elasticsearch`: Elasticsearch 8.x search cluster (`localhost:9200`).
- `arad-outbox-relay`: Background relay worker claiming outbox events via `SKIP LOCKED`.
- `arad-worker`: Celery worker executing projection updates and async tasks.
- `arad-beat`: Celery Beat scheduler triggering periodic reservation expirations.
- `test`: Isolated ephemeral test execution container with independent test database.

Multi-stage build in `Dockerfile`:
1. `frontend-builder` (Node 22): Compiles React SPA into production static bundle (`/build/frontend/dist`).
2. `builder` (Python 3.13): Installs Python dependencies into virtual environment (`/opt/venv`).
3. `runtime` (Python 3.13-slim): Packages API, backend packages, and copies frontend bundle into `/app/frontend/dist` served directly by FastAPI.

---

## 8. Frontend Single-Page Application (SPA) & Internationalization (FA/EN)

The administrative web console provides full operational capabilities across all domains:

### Complete Bilingual Support (English & Persian)
- **Dual Language Support:** Instant switching between English (`en`) and Persian (`fa`).
- **Dynamic Bidirectional Layout:** 
  - English sets `dir="ltr"` and renders with **Inter** typography.
  - Persian sets `dir="rtl"` and renders with **Vazirmatn** typography.
- **No-Reload Language Toggle:** Switching languages preserves the current route, active filters, open dialogs, and form input states without refreshing the page.
- **Persistent Preference:** Stored in `localStorage` under `arad_locale` and restored on subsequent visits.
- **Full Coverage:** 100% of user-facing strings are localized across:
  - Navigation, headers, breadcrumbs, and tenant switchers
  - Forms, inputs, placeholders, and validation errors
  - Tables, status badges, and pagination controls
  - Dynamic alerts, toasts, and modal dialogs
  - Interactive policy simulator and JSON inspection views

### Operational Feature Consoles
- **Dashboard:** Operational KPI metrics, active order statuses, stock utilization, and recent audit events.
- **Catalog Management:** Product creation, SKU assignment, categorized attributes, and detail views.
- **Inventory & Warehouses:** Multi-warehouse stock tracking (on hand, reserved, allocated), atomic stock adjustments, and reservation lifecycle tables.
- **Orders:** Order creation with idempotency keys, order line item management, price snapshots, confirmation, and cancellation.
- **Fulfillment:** Warehouse allocation orchestration, fulfillment tasks, carrier interface boundary (`FulfillmentProviderPort`), and retry/compensation workflows (currently backed by `MockFulfillmentProvider`; live carrier adapters such as DHL/FedEx/UPS require external integration credentials).
- **Search Console:** Faceted search over Orders, Products, and Audit events powered by Elasticsearch projections.
- **Access Control:** RBAC role management, dynamic ABAC policies, and real-time interactive policy simulator.
- **Tenancy & Security:** Multi-tenant memberships, role assignment, service account management, and API security settings.
- **Resilient Recovery:** In-app connection error banner with manual retry mechanism when backend connectivity is temporarily interrupted.

---

## 9. Frontend Routing, Cache Invalidation & Query Preservation

### Anti-Stale Caching Architecture
To prevent browsers from caching stale SPA shells or legacy applications:
- **FastAPI Headers:** All `index.html` responses return:
  ```http
  Cache-Control: no-cache, no-store, must-revalidate
  Pragma: no-cache
  Expires: 0
  ```
- **Hashed Assets:** Vite bundle assets (`/assets/*.js`, `/assets/*.css`) are served with unique content hashes, enabling immutable long-term caching while guaranteeing instant activation of new deployments.

### Legacy Cache & Service Worker Purging
- An inline self-cleaning script runs in `<head>` and `main.tsx` to automatically unregister any stale ServiceWorkers and delete old CacheStorage entries installed by prior applications on `localhost:8000`.

### Query Parameter Preservation & Trailing-Slash Normalization
- Navigating to `/` as an unauthenticated user redirects to `/login` while preserving arbitrary query parameters (e.g. `/?utm_source=chatgpt.com` -> `/login?utm_source=chatgpt.com`).
- Authenticated users opening `/` are routed to `/dashboard` (or `/select-tenant` if tenant selection is pending) with all query parameters intact.
- Both `/login` and `/login/` routes are explicitly supported.

---

## 10. PostgreSQL Architecture

PostgreSQL is the single authoritative system of record:
- **Transaction-per-Use-Case:** Every command executes inside a single `SqlAlchemyUnitOfWork`.
- **Optimistic Concurrency:** Aggregate tables carry `version: int` columns. Concurrency conflicts trigger `ConcurrencyConflict` (HTTP 409).
- **Deterministic ID Ordering:** High-contention locks sort resource IDs lexicographically before locking to eliminate deadlocks.
- **Time-Based Partitioning Design:** Documented monthly range partitioning (`PARTITION BY RANGE (created_at)`) for high-volume `outbox_events` and `audit_logs` tables.

---

## 11. Inventory Concurrency Model

Overselling is physically impossible:
1. Available stock invariant: $\text{Available} = \text{On Hand} - \text{Reserved} - \text{Allocated} \ge 0$.
2. Inbound reservation requests lock target rows using `SELECT ... FOR UPDATE` ordered deterministically by ID to prevent deadlocks.
3. If concurrent requests compete for the last unit, exactly one transaction secures the physical reservation. Competing requests handle unavailable stock according to configured `ARAD_UNAVAILABLE_STOCK_POLICY`: under `BACKORDER` (default), the order is created with status `BACKORDERED`; under `REJECT`, the order is refused with HTTP 409 Conflict. Available inventory never falls below zero.
4. Verified by automated concurrency test suites running parallel async database transactions.

---

## 12. Transactional Outbox

Prevents dual-write discrepancies between the database and the message broker:
1. Aggregate mutations and domain events are written to `outbox_events` in the **same database transaction**.
2. If RabbitMQ is down, the database transaction commits safely and events buffer in PostgreSQL.
3. The outbox relay worker polls `outbox_events` using `SELECT ... FOR UPDATE SKIP LOCKED` and publishes to RabbitMQ with publisher confirmations.
4. Records transition to `PUBLISHED` only after RabbitMQ confirms broker delivery.

---

## 13. RabbitMQ Topology

- **Exchange:** `arad.events` (topic, durable).
- **Routing Key Format:** `{context}.{entity}.{event_name}` (e.g. `ordering.order.confirmed`).
- **Dead-Letter Exchange (DLX):** `arad.dlx` with dead-letter routing for exhausted retries.
- **Retry Mechanism:** Messages failing due to transient errors route to `arad.retry.{queue}` with TTL backoff before returning to the main queue.

---

## 14. Inbox & Consumer Idempotency

Downstream consumers implement the **Inbox Pattern**:
1. Incoming messages are recorded in `inbox` with unique constraint on `(message_id, consumer_name)`.
2. Duplicate deliveries (arising from network redeliveries or broker failover) hit the unique constraint and are acknowledged without re-executing domain operations.
3. Guarantees **exactly-once processing semantics** on top of at-least-once transport.

---

## 15. Celery Workers & Scheduler

- **Reservation Expiration Sweeper:** Celery Beat periodically triggers a sweep identifying expired reservations and releasing held inventory.
- **Projection Reindex & Reconciliation:** Background workers rebuild search projections and verify aggregate checksum consistency between PostgreSQL and Elasticsearch.
- **Fail-Closed Worker Security:** Background jobs execute under explicit `SystemPrincipal` credentials.

---

## 16. Redis Caching

Implements cache-aside with strict tenancy safeguards:
- **Tenant-Prefixed Keys:** All keys follow `{tenant_id}:{context}:{entity}:{id}:{version}`.
- **Explicit TTLs:** Mandatory expiration windows protect against stale reads.
- **Stampede Protection:** Probabilistic early expiration and distributed negative caching.
- **Graceful Fallback:** If Redis is down, calls log a warning and fall back to PostgreSQL with zero downtime.

---

## 17. Elasticsearch Disposable Projections

Elasticsearch is strictly a disposable read projection:
- Rebuildable from PostgreSQL at any time.
- Managed across four distinct versioned indices:
  1. `arad_products_v1` (alias `products`)
  2. `arad_orders_v1` (alias `orders`)
  3. `arad_fulfillment_exceptions_v1` (alias `fulfillment_exceptions`)
  4. `arad_audit_v1` (alias `audit`)
- Zero-downtime reindexing via atomic index alias swapping.

---

## 18. Search Capabilities

- **Order Search:** Search by customer reference, SKU, order status, warehouse, and date range.
- **Product Search:** Full-text keyword search across product names, descriptions, and dynamic attribute filters.
- **Fulfillment Exception Search:** Search by failure reason, carrier, and warehouse.
- **Audit Search:** Search policy evaluations, decision reasons, and actor actions.
- **Mandatory Tenant Scoping:** Every Elasticsearch query hardcodes a top-level `{"term": {"tenant_id": ...}}` filter.

---

## 19. ABAC & RBAC Authorization

Dual-layer authorization engine:
1. **RBAC:** Enforces baseline permissions (e.g. `orders.create`, `fulfillments.modify`).
2. **ABAC:** Dynamically evaluates subject attributes (e.g. `assigned_warehouses`), resource attributes (e.g. `warehouse_id`), and environment context.
3. **Warehouse Scoping:** An operator assigned to warehouse `LAX` is rejected when attempting to modify a fulfillment for warehouse `SEA`.
4. **Audit Trail:** Every access evaluation records an immutable audit entry with actor, decision, and explicit decision reason.

---

## 20. Tenant Isolation

Tenant isolation is enforced across all architectural layers:
- **API:** Authentication extracts tenant ID from signed JWT claims.
- **Application:** Use cases reject mismatched cross-tenant IDs.
- **Cache:** Redis keys are partitioned by tenant prefix.
- **Search:** Elasticsearch queries enforce tenant filtering.
- **Database:** PostgreSQL Row-Level Security (RLS) blocks cross-tenant reads and writes.

---

## 21. PostgreSQL Row-Level Security (RLS)

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

## 22. Audit Architecture

The platform maintains an immutable, append-only audit trail in the `audit_logs` table:
- Captures actor identity, action type, resource type, resource ID, policy decision, decision reason, and correlation ID.
- Accessible via `GET /v1/audit/events` with tenant scoping and cursor pagination.
- Retained for 90 days in operational storage prior to cold-storage export.

---

## 23. API & Frontend Routes Overview

### Web UI Application Routes (SPA)
```text
GET    /                                        Root redirect (preserves query, routes to /login or /dashboard)
GET    /login, /login/                          User sign-in with tenant option & language toggle
GET    /register, /register/                    User account registration
GET    /select-tenant                           Tenant membership selector
GET    /dashboard                               Executive KPI overview & recent activity
GET    /catalog/products                        Product catalog listing & SKU creation
GET    /catalog/products/:id                    Detailed product view & attributes
GET    /inventory                               Multi-warehouse stock levels & adjustments
GET    /inventory/warehouses                    Warehouse locations & active status
GET    /inventory/reservations                  Stock reservations & expiration timers
GET    /orders                                  Order search and status listing
GET    /orders/new                              Interactive order placement form
GET    /orders/:id                              Order detail, lines, and lifecycle actions
GET    /fulfillments                            Fulfillment lookup and carrier status
GET    /fulfillments/:id                        Fulfillment task details & dispatch retry
GET    /search/orders                           Elasticsearch faceted order search
GET    /search/products                         Full-text product search
GET    /search/audit                            Audit event search
GET    /access/roles                            Role definitions & permission grants
GET    /access/policies                         ABAC policy rules
GET    /access/evaluate                         Interactive ABAC policy simulator
GET    /settings/members                        Tenant membership & user management
GET    /settings/service-accounts               Service accounts & API credentials
GET    /settings/security                       Security settings & session controls
```

### Core REST Endpoints
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

## 24. Testing Strategy

The test suite enforces full pyramid coverage across pure unit, application orchestration, concurrency, integration, security, and BDD acceptance:
- **Unit Tests:** Verify aggregate invariants without database or network I/O.
- **Concurrency Tests:** Multi-threaded async workers verify race condition safety.
- **Integration Tests:** Execute against real Dockerized PostgreSQL, Redis, RabbitMQ, and Elasticsearch services.
- **Frontend Component & Routing Tests:** Validate UI rendering, bilingual translation toggles, query preservation, authentication guards, and API integration in Vitest.
- **BDD Scenarios:** Validate end-to-end user journeys (e.g. concurrent last-unit reservation, idempotent replay, outbox delivery across broker restarts).

---

## 25. Test Suite Metrics & Verification (687 Passed)

All tests pass 100% in local and continuous integration environments:

```text
Backend Tests (pytest):   634 passed (0 failed, 1 skipped)
Frontend Tests (vitest):   53 passed across 9 test suites
Total Automated Tests:    687 passed
```

### Frontend Vitest Suites:
- `AuthFlow.test.tsx` (9 tests): Login inputs, input validation, authentication submission, error alerts, profile loading, network error recovery, HTTP 401 handling.
- `LanguageSwitch.test.tsx` (6 tests): Persian/English toggle, document `dir="rtl"` / `dir="ltr"` attribute switching, font family application, persistence in `localStorage`.
- `RootRouting.test.tsx` (5 tests): Unauthenticated root `/` redirection to `/login`, preserving `?utm_source=chatgpt.com` across redirects, trailing-slash `/login/` support, authenticated query preservation.
- `TenantSwitch.test.tsx` (10 tests): Tenant list rendering, tenant switching, auth token tenant scoping, navigation guards.
- `OrderForm.test.tsx` (4 tests): Order placement form, SKU validation, idempotency key generation and reuse.
- `PolicySimulator.test.tsx` (4 tests): Policy evaluation inputs, real-time decision display, attribute validation.
- `StockAdjustment.test.tsx` (3 tests): Warehouse stock adjustments, reason codes, quantity constraints.
- `errorFormatter.test.ts` (4 tests): API error parsing, validation message translation, fallback formatting.

### Quality Checks:
- **Ruff Code Style:** **0 errors** (clean, formatted).
- **Mypy Static Typing:** **0 errors** (strict type safety in `src/`).
- **Frontend TypeScript (`tsc -b`):** **0 errors**.

---

## 26. CI/CD Workflow & Diagnostic Smoke Tests

Defined in `.github/workflows/ci.yml`:
1. **Lint & Static Analysis:** Executes `ruff check`, `mypy src/`, and frontend `tsc -b`.
2. **Container Composition:** Validates `docker compose config --quiet`.
3. **Database Migration:** Executes Alembic forward migrations against a clean database.
4. **Backend Test Suite:** Runs all 634 pytest tests in the isolated test container.
5. **Frontend Test Suite:** Executes vitest test runner (`npm test -- --run`).
6. **Robust Diagnostic Smoke Test:**
   - Validates live HTTP requests to `/healthz`, `/readyz`, `/`, and `/v1/openapi.json`.
   - Emits detailed diagnostic output for each probe: requested URL, HTTP status code, Content-Type, and content assertions.
   - On assertion failure, prints sanitized response excerpts for immediate diagnosis.
   - Shell cleanup trap (`trap cleanup EXIT`) guarantees non-destructive Docker cleanup even if a smoke assertion fails.

---

## 27. Docker Deployment Instructions

### Start All Services

```bash
docker compose up --build -d
```

### Rebuild Only the API / Frontend Service (Non-Destructive)

```bash
docker compose up -d --build --no-deps api
```

### Check Service Health

```bash
curl -s http://localhost:8000/healthz   # {"status":"ok","environment":"production"}
curl -s http://localhost:8000/readyz    # {"status":"ready","checks":{"database":"ok","cache":"ok"}}
```

### Access Management Interfaces

- **Web Application UI:** `http://localhost:8000/` (Integrated Bilingual SPA)
- **FastAPI OpenAPI Docs:** `http://localhost:8000/docs`
- **RabbitMQ Management UI:** `http://localhost:15672` (Username: `arad` / Password: `arad`)
- **Elasticsearch API:** `http://localhost:9200`

---

## 28. Database Migration & Disaster Recovery

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

## 29. Local Development (Backend & Frontend)

### Prerequisites

- Python 3.12+ (or `uv`)
- Node.js 20+ and npm
- Docker and Docker Compose

### Backend Local Testing

```bash
# Run pytest tests
uv run pytest

# Run linting and type checking
uv run ruff check src/ tests/
uv run mypy src/
```

### Frontend Local Development

```bash
cd frontend

# Install dependencies
npm install

# Run Vite development server (proxies API to localhost:8000)
npm run dev

# Run Vitest tests
npm test -- --run

# Run TypeScript type check and production build
npm run build
```

---

## 30. Security Guarantees

1. **Fail-Closed Authorization:** Access is denied unless an explicit RBAC permission and matching ABAC policy allow it.
2. **PostgreSQL RLS Defense-in-Depth:** Queries without tenant context return 0 rows at database engine level.
3. **Password Security:** Hashes passwords using Argon2id with 64MB memory cost.
4. **Credential Redaction:** Access logs, error envelopes, and audit records redact Authorization headers and sensitive secrets.
5. **Rootless Containers:** Production containers run under dedicated non-root users (`1001:1001`) with dropped Linux capabilities.

---

## 31. Consistency Guarantees

1. **No Negative Stock:** Verified under concurrent transactions; inventory never drops below zero.
2. **No Lost Outbox Events:** Committed domain events remain in PostgreSQL until confirmed by RabbitMQ.
3. **Exactly-Once Business Semantics:** Inbox deduplication ensures retried messages are not processed twice.
4. **Optimistic Concurrency:** Aggregate updates fail with `ConcurrencyConflict` if versions do not match.

---

## 32. Failure Recovery & Resiliency

- **RabbitMQ Outage:** Outbox relay buffers events in PostgreSQL; publishing resumes automatically upon broker reconnection.
- **Redis Outage:** Caching gracefully degrades to direct PostgreSQL queries with zero downtime.
- **Elasticsearch Outage:** Search projections buffer in retry queues or resync via scheduled reconciliation.
- **Worker Crash:** RabbitMQ re-queues unacknowledged messages; Inbox ensures safe re-delivery.
- **Client Network Drop:** Frontend displays connection error with manual retry capability without dropping session tokens.

---

## 33. Specification Compliance

A complete traceability matrix mapping all 25 sections of `docs/specification.md` is available in:
[`docs/spec-compliance.md`](docs/spec-compliance.md)

---

## 34. Phase Status & Production Hardening Capabilities

- **Phase 1: Domain Foundation** — **COMPLETED**
- **Phase 2: Reliable Asynchronous Processing** — **COMPLETED**
- **Phase 3: Search and Caching** — **COMPLETED**
- **Phase 4: Security and Tenancy** — **COMPLETED**
- **Phase 5: Production Hardening & Bilingual Frontend** — **COMPLETED**

### Key Production Capabilities:
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
7. **Frontend SPA & Bilingual (FA/EN) Architecture**:
   - Integrated React 18 / TypeScript 5 / Vite 6 single-page application served directly from FastAPI with zero-stale cache headers.
   - Full English (LTR) and Persian (RTL) localization across all views, dialogs, tables, and policy tools.
   - Automatic browser cache and legacy service-worker purging.
