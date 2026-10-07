# System overview

Arad is a **Distributed Order and Inventory Orchestration Platform**. Phase 1 is the
foundation: a **modular monolith** built as a hexagonal application, with PostgreSQL
as the system of record and the concurrency guarantees for inventory already in
place. Later phases (fulfilment, search, asynchronous messaging, caching, ABAC) add
contexts and infrastructure without changing the dependency rule this phase
establishes.

The specification is `Arad-project.md`; this document explains how the Phase 1 code
is organised, how a request flows, and where each guarantee comes from.

## What Phase 1 does

* Multi-tenant catalog (products, SKUs), inventory (warehouses, stock, movements) and
  ordering (orders, lines, reservations) over HTTP.
* Inventory that **cannot oversell**, proven under real concurrent transactions.
* An order lifecycle with explicit transitions and no backwards moves.
* Tenancy enforced in every repository and in the database schema.
* Idempotent mutation endpoints for safely retried requests.
* A schema created by Alembic migrations that are tested from empty on every run.

## What Phase 1 deliberately does not do

No RabbitMQ, no Celery, no Elasticsearch, no Redis, no ABAC engine, no advanced
authentication, no distributed tracing, no Prometheus, no Kubernetes, no
PostgreSQL RLS. Those are Phase 2+ and are listed in the specification; the code
carries placeholders (`fulfillment`, `search`, `audit`) that mark where they land
without pretending to implement them. `python -m bootstrap.workers` prints what
Phase 2 will run there and exits 0.

## Runtime shape

```text
        HTTP client
             │  X-Tenant-Id / X-Actor-Id / X-Correlation-Id / Idempotency-Key / If-Match
             ▼
   ┌───────────────────────────────────────────────────────────────┐
   │ bootstrap (FastAPI)                                           │
   │  middleware: CorrelationId → RequestSizeLimit → AccessLog     │
   │  routers/catalog · inventory · ordering · platform · health   │
   │  errors.py  → one stable error envelope, no stack traces      │
   │  dependencies.py → request context, transaction scope, use cases│
   └───────────────┬───────────────────────────────────────────────┘
                   │  application service (use case)
                   ▼
   ┌───────────────────────────────────────────────────────────────┐
   │ <context>/application   command → load aggregate → decide →   │
   │                         save → commit, on ONE transaction     │
   ├───────────────────────────────────────────────────────────────┤
   │ <context>/domain        aggregates, invariants, transitions,  │
   │                         domain events — no framework imports  │
   ├───────────────────────────────────────────────────────────────┤
   │ <context>/ports         repository / UoW / publication        │
   │                         interfaces (Protocols)                │
   ├───────────────────────────────────────────────────────────────┤
   │ <context>/adapters      SQLAlchemy repositories, mappers,     │
   │                         in-process cross-context adapters     │
   └───────────────┬───────────────────────────────────────────────┘
                   ▼
             PostgreSQL 17 (system of record)
```

A request is handled by exactly one transaction owned by the application layer:

1. **Middleware** assigns/echoes `X-Correlation-Id`, rejects bodies over
   `ARAD_MAX_REQUEST_BYTES` and logs one structured line per request.
2. **`get_request_context`** builds the `RequestContext` from the headers
   (`X-Tenant-Id`, `X-Actor-Id`, `X-Actor-Type`, correlation id). A request without a
   tenant never reaches a use case.
3. **The router** validates a Pydantic schema, builds a command, and calls a use case
   obtained from `bootstrap.dependencies.UseCases`.
4. **The use case** (`shared.application.use_case.UseCase`) opens the context's unit
   of work, claims the idempotency key if the request carries one, loads aggregates
   through ports, asks *them* to decide, saves, collects domain events, commits.
5. **The adapter** turns aggregate state into rows; PostgreSQL enforces uniqueness,
   foreign keys and the CHECK constraints that mirror the domain invariants.
6. **`bootstrap/errors.py`** maps a domain or application exception to a stable HTTP
   status and error code; anything unexpected becomes a 500 with a correlation id and
   no internal detail.

## Dependency rule

`domain` depends on nothing but `shared.domain`. `application` depends on its own
domain and ports. `ports` depend on domain types. `adapters` and `bootstrap` depend
on everything. Nothing points the other way.

`tests/unit/test_architecture.py` parses the source with `ast` and fails if any
module under `*/domain` or `*/ports` imports FastAPI, SQLAlchemy, asyncpg, Alembic,
Redis, Celery, RabbitMQ or an HTTP client; if application modules import SQLAlchemy;
if routers contain SQL; or if a context imports another context's internals instead
of its published port. The rule is executable, not aspirational.

## Where each Phase 1 guarantee comes from

| Guarantee | Mechanism |
| --- | --- |
| Inventory never goes negative | `InventoryItem` refuses the allocation; every write goes through `SELECT … FOR UPDATE` on the stock rows in a deterministic order; `ck_inventory_items_available_non_negative` is the database backstop (ADR-0007) |
| No duplicate order from one idempotency key | `(tenant_id, idempotency_key)` claimed with `INSERT … ON CONFLICT DO NOTHING` inside the same transaction as the order; replays re-read the stored order (ADR-0006) |
| An order cannot skip or reverse a state | Transition table in `ordering.domain.order`; illegal moves raise `InvalidOrderTransition` → 409 (ADR-0004) |
| A confirmed order really has stock | `ConfirmOrder` asks inventory to hold or release per line and only confirms when every line is covered (ADR-0004) |
| Tenants cannot see each other | Tenant-scoped repositories, `tenant_id NOT NULL` + `RESTRICT` foreign keys, and repository-level cross-tenant checks (ADR-0010) |
| A retry after a crash cannot double-reserve | The claim and the business write share a transaction; a rollback leaves no claim, so the next attempt starts clean (ADR-0006) |
| The schema is reproducible | One Alembic revision, tested from an empty database with `compare_metadata` drift detection (ADR-0005) |

## Layout

```text
src/
  shared/       domain primitives, application kernel (use case, UoW, idempotency),
                infrastructure (async engine, tables, registry)
  identity/     tenants
  catalog/      products, SKUs
  inventory/    warehouses, stock, movements, reservations
  ordering/     orders, order lines
  fulfillment/  placeholder (Phase 2)
  search/       placeholder (Phase 3)
  audit/        placeholder (Phase 2)
  bootstrap/    FastAPI app, routers, schemas, config, errors, middleware, CLI
migrations/     Alembic revisions
tests/          unit · application · repository · api · integration · concurrency
docs/           this document set, ADRs, operational notes
```

## Cross-context communication (Phase 1 style)

Two contexts that need each other in Phase 1 — ordering needs catalog prices and
inventory holds — talk through **published ports**, never through each other's
tables or aggregates:

* `catalog.ports.publication.CatalogQuery` returns `PurchasableSku` for a set of
  SKUs (implemented by `SqlAlchemyCatalogQuery`, which reads catalog's own tables).
* `inventory.ports.publication.InventoryOperations` reserves, releases and confirms
  holds (implemented by `InProcessInventoryOperations`, which delegates to
  inventory's use cases **on the caller's transaction scope**, so the order and its
  reservation commit together).

Both are in-process calls today and both are shaped like a remote contract ready for
the day one of the contexts becomes a service: replacing the adapter is the only
change (ADR-0008, ADR-0009).

## Running it

```bash
docker compose up --build          # API on :8000, PostgreSQL on :5432
curl localhost:8000/healthz
curl localhost:8000/readyz
open localhost:8000/docs           # OpenAPI
```

Without Docker: install PostgreSQL 17, set `ARAD_DATABASE_URL`, then
`alembic upgrade head && python -m bootstrap.cli serve`. See the repository
`README.md` for the full setup and the test commands.

## Failure behaviour

* `/healthz` never touches PostgreSQL: it reports liveness only.
* `/readyz` pings the database and returns 503 with
  `{"checks": {"database": "unreachable"}}` when it cannot. It is deliberately terse
  — no connection strings, no driver messages.
* Every 5xx carries a correlation id that matches the log line, and never a
  traceback.
* Concurrency conflicts surface as 409 `concurrency_conflict`, never as a 500.
