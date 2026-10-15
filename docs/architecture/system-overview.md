# System Overview

The Warehouse platform is a **Distributed Order and Inventory Orchestration Platform** built as a **modular monolith with independent asynchronous workers**.

It coordinates multi-warehouse inventory, order lifecycle state, carrier fulfillment, near-real-time search projections, and attribute-based access control (ABAC) across multiple tenants, with PostgreSQL serving as the single authoritative system of record.

---

## 1. Architectural Scope & Current Status

The platform encompasses **Phase 1 through Phase 4** of the specification:

- **Phase 1 (Domain Foundation):** Bounded contexts (`Catalog`, `Inventory`, `Ordering`, `Identity`, `Fulfillment`, `Search`, `Audit`), pure domain models, explicit hexagonal architecture, PostgreSQL transaction-per-use-case Unit of Work, row-level locking, and idempotency guarantees.
- **Phase 2 (Reliable Asynchronous Processing):** Transactional outbox with `SKIP LOCKED` relay, durable RabbitMQ exchanges, dead-letter and retry routing, idempotent inbox consumers, and Celery asynchronous workers.
- **Phase 3 (Search & Caching):** Redis cache-aside with tenant-prefixed keys and event invalidation; Elasticsearch disposable search projections for Products, Orders, Fulfillment Exceptions, and Audit logs; zero-downtime aliases and reconciliation.
- **Phase 4 (Security & Tenancy):** Identity authentication (Argon2id, JWT), dual RBAC + dynamic ABAC policy evaluation, warehouse-scoped operator permissions, durable audit logging, and PostgreSQL Row-Level Security (RLS) defense-in-depth.
- **Phase 5 (Production Hardening):** Production Kubernetes manifests, Prometheus/Grafana infrastructure deployment, and production load/chaos testing are **explicitly deferred to Phase 5**.

---

## 2. Runtime Topology

```text
                                HTTP / REST (FastAPI)
                                       │
                ┌──────────────────────┴──────────────────────┐
                ▼                                             ▼
        ┌───────────────┐                             ┌───────────────┐
        │  Web Clients  │                             │ Sales Channels│
        └───────┬───────┘                             └───────┬───────┘
                │                                             │
                ▼                                             ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │  API Gateway / Application Monolith (FastAPI)                   │
    │  - Correlation ID & Context Middleware                         │
    │  - JWT Authentication & Tenant Scoping                          │
    │  - RBAC & ABAC Dynamic Policy Evaluation                        │
    │  - Hexagonal Application Layer & Use Cases                      │
    └───────────────┬─────────────────────────────┬───────────────────┘
                    │ Writes / Reads               │ Cache Aside
                    ▼                             ▼
    ┌───────────────────────────────┐     ┌───────────────┐
    │ PostgreSQL (System of Record) │     │ Redis Cache   │
    │ - Aggregates & Invariants     │     │ - Pre-warmed  │
    │ - Optimistic Concurrency      │     │ - Tenant keys │
    │ - Row-Level Security (RLS)    │     │ - TTL & Evict │
    │ - Transactional Outbox        │     └───────────────┘
    └───────────────┬───────────────┘
                    │ Polls with SKIP LOCKED
                    ▼
    ┌───────────────────────────────┐
    │ Outbox Relay Worker           │
    └───────────────┬───────────────┘
                    │ Publishes integration events
                    ▼
    ┌───────────────────────────────┐
    │ RabbitMQ Topic Exchange       │
    │ (arad.events + Retry + DLX)   │
    └───────────────┬───────────────┘
                    │ Consumes events
                    ▼
    ┌─────────────────────────────────────────────────────────────────┐
    │ Background Workers & Consumers (Celery & Asyncio)               │
    │ - Inbox Idempotent Deduplication                                │
    │ - Elasticsearch Projector (Products, Orders, Exceptions, Audit) │
    │ - Reservation Expiration Scheduler                              │
    │ - Fulfillment Carrier Dispatch & Compensation                   │
    └────────────────┬────────────────────────────────────────────────┘
                     │ Updates projections
                     ▼
    ┌───────────────────────────────┐
    │ Elasticsearch 8.x             │
    │ - products alias              │
    │ - orders alias                │
    │ - fulfillment_exceptions alias│
    │ - audit alias                 │
    └───────────────────────────────┘
```

---

## 3. Core Architectural Principles

1. **PostgreSQL as Single Source of Truth:** All domain state transitions are committed to PostgreSQL. Redis and Elasticsearch are strictly disposable read projections.
2. **Hexagonal Dependency Rule:** Inbound adapters (FastAPI, RabbitMQ consumers, Celery tasks) and outbound adapters (PostgreSQL, Redis, Elasticsearch) depend strictly on ports and application interfaces. The domain layer has zero infrastructure dependencies.
3. **Fail-Closed Security:** Authorization defaults to `DENY` unless explicitly granted by a matching RBAC permission and ABAC policy.
4. **Tenant Isolation at Every Layer:** Enforced at HTTP routing, application context, Redis key formatting, Elasticsearch search queries, and database RLS.
