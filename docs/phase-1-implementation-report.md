# Phase 1 implementation report

**Scope:** architecture foundation, bounded contexts, domain model, application layer,
PostgreSQL persistence, Alembic migrations, the Phase 1 FastAPI subset, inventory
concurrency protection, order lifecycle, idempotency foundation, and the tests that
prove all of it.

**Specification:** `uploads/Arad-project.md` (authoritative). Nothing outside it was
invented; where it was silent, the choice was made conservatively and recorded in an
ADR.

**Status:** complete and verified. 274 tests pass, lint and types are clean, the API
serves the documented walkthrough end to end against a real PostgreSQL 17.

---

## Definition of Done

| # | Item | Evidence |
| --- | --- | --- |
| 1 | Docker environment starts | `Dockerfile` (multi-stage, non-root, pinned, `HEALTHCHECK`) + `docker-compose.yml` (postgres → one-shot `migrate` → `api`, health-gated). Docker is **not installed in this environment**, so the artefacts were validated without building: the compose file parses with a real YAML loader and the service wiring, health conditions, read-only rootfs and healthcheck commands were asserted; `alembic upgrade head` was executed from the image's exact working layout (`alembic.ini` + `migrations/`, no `src/` on the path) to prove the runtime stage can migrate. Everything else below was executed, not reasoned about. |
| 2 | Migrations run | `ARAD_DATABASE_URL=…arad alembic upgrade head` → `0001_initial_schema` applied to a **freshly recreated** `arad` database; 11 tables + `alembic_version`. `tests/integration/test_migrations.py` builds the schema from empty on a scratch database, round-trips `upgrade → downgrade base → upgrade`, and asserts `compare_metadata` reports **no drift**. |
| 3 | FastAPI starts | `python -m bootstrap.cli serve` → `Uvicorn running on http://0.0.0.0:8000`; `/healthz` 200, `/readyz` 200 `{"database":"ok"}`. |
| 4 | Catalog works | Over HTTP: create product → activate → create SKU → read/revise (`If-Match`) → price change → deactivate. `tests/api/test_api.py::TestCatalogApi`, `tests/application/test_catalog_operations.py`, `tests/repository/…::TestCatalogPersistence`. |
| 5 | Inventory works | `POST /v1/inventory/adjustments` returns the new position **and** the ledger movement; `GET /v1/inventory/availability`; adjustments below the reserved quantity are refused (`409 stock_adjustment_below_reserved`); every change appends an immutable `inventory_movements` row. |
| 6 | Reservations work | `POST /v1/reservations` (standalone, `PARTIAL`) and order-driven (`ALL_OR_NOTHING`); `GET`/`DELETE /v1/reservations/{id}`; releasing returns the units; a released or expired hold can never be confirmed (`409 reservation_expired`). |
| 7 | Orders work | `POST /v1/orders` (201, `CONFIRMED` when fully covered, `BACKORDERED` under the default policy), `GET /v1/orders`, `GET /v1/orders/{id}`, `POST …/confirm`, `POST …/cancel`; illegal transitions are `409 invalid_order_transition` with `{from, to}`. |
| 8 | Idempotency works | Replay returns the same order with `Idempotent-Replay: true` and creates neither a second order nor a second reservation; mismatched payload → `409 idempotency_key_reused`; concurrent duplicate → `409 idempotency_in_progress`; keys are per tenant; a rolled-back attempt leaves no claim. |
| 9 | Tenant boundaries respected | `tests/api/test_api.py::TestTenantIsolationOverHttp` (read, cancel, reserve, release, list), `tests/repository/` (a tenant's repository cannot read another's row), and schema tests (every owned table has `tenant_id NOT NULL` + `RESTRICT` FK). Cross-tenant reads answer `404 order_not_found`, never 403. |
| 10 | Concurrent reservation tests pass | `pytest tests/concurrency -q` → **11 passed**: stock `1` with two simultaneous `quantity=1` requests → *exactly one* winner, loser `409 insufficient_inventory`, final position `on_hand 1 / reserved 1 / available 0`, never negative (5 repeats); 20 racers for 10 units → exactly `10` winners and `10` rejections with the final position `10/10/0`; 10 racers asking for 2 units each → 5 winners; **25 racers for 7 units → `reserved == 7`** with `0` rows in the whole table where `reserved > on_hand`; multi-warehouse contention (2 units in each of 2 warehouses → 4 winners) and a multi-row all-or-nothing reservation (1 winner, one reservation); a `FOR UPDATE NOWAIT` probe proving the row really is locked (SQLSTATE `55P03`), plus a bounded `asyncio.wait` probe and a sequential retry that also fails; and two identical order submissions racing on the same idempotency key → one order, one reservation, the hold applied once (`3/1/2`). |
| 11 | Unit / application / PostgreSQL repository / API tests pass | `125 + 58 + 13 + 51` → `274 passed` for the whole suite (incl. 16 integration, 11 concurrency). |
| 12 | OpenAPI valid | `GET /openapi.json` parses, `info.version = 0.1.0`: **23 paths / 28 operations / 42 schemas**; health probes outside `/v1`; `Idempotency-Key` declared on `POST /v1/orders`. `TestOpenApi` asserts the published contract. |
| 13 | No domain → infrastructure dependency | `tests/unit/test_architecture.py` walks every module with `ast`; a static sweep over `src/*/domain`, `src/*/ports`, `src/shared/domain` finds **0** framework/driver imports, **0** SQLAlchemy references in any `application/` layer, **0** SQL or session use in routers. `tests/unit/test_dependency_boundary.py` additionally proves the test suite's own conftest stays framework-free. |
| 14 | Architecture docs exist | `docs/architecture/` (system-overview, bounded-contexts, hexagonal-architecture, data-model, index-decisions, error-model), `docs/decisions/` (ADRs 0001–0010 + index), `docs/runbooks/migrations.md`, root `README.md`. |
| 15 | No later-phase functionality implemented | No RabbitMQ, Celery, Elasticsearch, Redis, ABAC, advanced auth, tracing, Prometheus, Kubernetes or RLS anywhere in `src/` (grep verified: 0 imports). `fulfillment`, `search`, `audit` are placeholders carrying `PLANNED_PHASE`, asserted by the architecture test. `python -m bootstrap.workers` documents the Phase 2 jobs and exits 0 without running anything. |
| 16 | Full suite / lint / types | `pytest -q` → **274 passed**; `ruff check src/ tests/ migrations/ scripts/` → clean; `ruff format --check` → 169 files formatted; `mypy src/` → no issues in 141 files. |
| 17 | Architectural violations inspected and fixed | Beyond the automated checks: no `asyncio.sleep`/`time.sleep` in `src/` (0), no test marked skip/xfail (0), no `except …: pass` (0). One genuine finding was fixed: `/readyz` caught every exception without logging it — it now logs the traceback at warning level while still returning the deliberately coarse 503 body. |
| 18 | Concise implementation report delivered | This document. |

---

## What was built

### Layout (141 Python files, ~13.8k lines in `src/`)

```text
src/shared/{domain,application,infrastructure}   primitives, application kernel, DB plumbing
src/identity|​catalog|​inventory|​ordering/{domain,application,ports,adapters}
src/fulfillment/ (Phase 2)  src/search/ (Phase 3)  src/audit/ (Phase 2)   placeholders
src/bootstrap/{app,config,errors,logging,middleware,dependencies,cli,workers}.py
src/bootstrap/{routers,schemas}/…
migrations/versions/0001_initial_schema.py       11 tables, constraints, indexes
tests/{unit,application,repository,api,integration,concurrency}/ + tests/{fakes,support}
docs/{architecture,decisions,runbooks}/          README.md, Dockerfile, docker-compose.yml,
scripts/measure_indexes.py                       Makefile, .env.example
```

### Domain model

`Order` + `OrderLine` + pricing snapshot, `Product`, `Sku`, `InventoryItem`,
`InventoryMovement`, `Reservation` + allocations, `Warehouse`, `Tenant`. Aggregates
have identity, invariants, behaviour, explicit state transitions and domain events;
there is no anaemic model and no business rule in a router (0 SQL/session references in
`src/bootstrap/routers`).

### Application layer

27 use cases (`shared.application.usecase.UseCase` / `QueryUseCase`): every one opens
its own unit of work, commits exactly once, owns the transaction boundary, claims the
idempotency key, dispatches domain events inside the transaction, and depends on ports
only — no SQLAlchemy model crosses into application code.

### Ports and adapters

`ProductRepository`, `SkuRepository`, `WarehouseRepository`,
`InventoryItemRepository`, `InventoryMovementRepository`, `ReservationRepository`,
`OrderRepository`, `TenantRepository` as `Protocol`s; SQLAlchemy implementations,
mappers and unit-of-work compositions in `*/adapters/persistence`. Cross-context
communication uses published ports only (`catalog.ports.publication`,
`inventory.ports.publication`), implemented in-process on the caller's transaction
scope.

### Database

`tenants`, `products`, `skus`, `warehouses`, `inventory_items`, `inventory_movements`,
`reservations`, `reservation_allocations`, `orders`, `order_lines`,
`idempotency_records` — with FKs (`RESTRICT`, `CASCADE` only for owned children),
tenant-scoped unique constraints, partial unique index for one ACTIVE reservation per
order, CHECK constraints mirroring the domain invariants (including
`reserved <= on_hand`), `timestamptz`, and `version` columns. Index decisions were
**measured** with `EXPLAIN (ANALYZE, BUFFERS)` on 50 000 rows/table and are documented
in `docs/architecture/index-decisions.md`.

---

## Files created / modified

**Created (this phase)**

* `src/**` (141 modules across `shared`, `identity`, `catalog`, `inventory`, `ordering`,
  `fulfillment`, `search`, `audit`, `bootstrap`), all with `py.typed`.
* `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_initial_schema.py`.
* `tests/**`: `conftest.py`, `support/{seed,database,events}.py`,
  `fakes/{ports,stores,unit_of_work,idempotency,application}.py`,
  `unit/` (6 files), `application/` (4), `repository/` (1),
  `api/test_api.py`, `integration/test_migrations.py`,
  `concurrency/test_inventory_concurrency.py`.
* `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `Makefile`, `.env.example`,
  `README.md`, `alembic.ini`, `pyproject.toml`, `.gitignore`.
* `docs/architecture/{system-overview,bounded-contexts,hexagonal-architecture,data-model,index-decisions,error-model}.md`,
  `docs/decisions/0001…0010` + index, `docs/runbooks/migrations.md`, this report.
* `scripts/measure_indexes.py`.

**Modified during verification (deliberate, not cosmetic)**

| Change | Why |
| --- | --- |
| `bootstrap/schemas/common.py`: `HealthResponse.checks: dict[str, str]`; `bootstrap/routers/health.py`: `checks={}` / `checks={"database": …}` | the probes reported nothing machine-readable about what they checked |
| `bootstrap/routers/health.py`: log the readiness failure | it caught every exception without recording the reason (rule: never swallow) |
| `ordering/application/use_cases.py::ConfirmOrder`: no longer consumes the reservation | confirmation keeps the hold ACTIVE; units leave at fulfilment ([ADR-0004](decisions/0004-order-lifecycle.md)) — the tests were updated with it |

---

## Validation commands (all executed)

```bash
# schema on a fresh database
psql -c "DROP DATABASE IF EXISTS arad WITH (FORCE)" -c "CREATE DATABASE arad OWNER arad"
ARAD_DATABASE_URL=postgresql+asyncpg://arad:arad@127.0.0.1:5432/arad alembic upgrade head

# the service, and the documented walkthrough against it
ARAD_DATABASE_URL=… python -m bootstrap.cli serve
# healthz 200 · readyz 200 {"database":"ok"} · order 201 CONFIRMED · replay 201 + Idempotent-Replay: true
# availability on_hand 10 / reserved 3 / available 7 · mismatched key 409 idempotency_key_reused
# cross-tenant read 404 order_not_found · X-Correlation-Id echoed · openapi 23 paths / 28 ops / 42 schemas

pytest tests/unit -q        # 125 passed
pytest tests/application -q # 58 passed
pytest tests/repository -q  # 13 passed
pytest tests/api -q         # 51 passed
pytest tests/integration -q # 16 passed
pytest tests/concurrency -q # 11 passed   (re-run repeatedly: stable)
pytest -q                   # 274 passed

ruff check src/ tests/ migrations/ scripts/     # All checks passed
ruff format --check src/ tests/ migrations/ scripts/   # 169 files already formatted
mypy src/                                        # no issues in 141 files
python -m bootstrap.workers                      # documents Phase 2 jobs, exit 0
python scripts/measure_indexes.py --rows 50000    # EXPLAIN ANALYZE evidence for the indexes
```

---

## Assumptions and design decisions

Every choice the specification left open is recorded as an ADR; the ones that change
observable behaviour are listed here.

1. **Confirmation does not consume stock.** A confirmed order's hold stays `ACTIVE`;
   `on_hand` and `reserved` are decremented at fulfilment (Phase 2). ([ADR-0004](decisions/0004-order-lifecycle.md))
2. **Phase 1 trusts `X-Tenant-Id`.** No authentication or ABAC; the tenant context is
   built in one dependency provider, so real auth changes one function. ([ADR-0010](decisions/0010-multi-tenancy.md))
3. **Idempotency is per `(tenant, key)` and in the business transaction.** Replays
   re-read the resource; errors leave no claim. ([ADR-0006](decisions/0006-idempotency-semantics.md))
4. **Money is an integer count of minor units** plus an ISO-4217 currency; no floats
   anywhere. Amounts are signed at the value-object level, with never-negative rules in
   the aggregates that need them.
5. **Unavailable stock defaults to `BACKORDER`** (`ARAD_UNAVAILABLE_STOCK_POLICY`); the
   alternative `REJECT` refuses the order with `409 insufficient_inventory`.
6. **Reservation TTL defaults to 30 minutes with a 24-hour ceiling.** Expiry semantics
   (never confirmable) exist; the sweeper is Phase 2.
7. **Product and SKU are separate aggregates**, priced per SKU, with `attributes` jsonb
   left unvalidated because the specification does not define an attribute schema.
   ([ADR-0003](decisions/0003-product-and-sku-are-separate-aggregates.md))
8. **Cross-tenant resources answer 404**, never 403, so existence is not leaked.
9. **Idempotency records are kept 7 days** and cleaned by a Phase 2 job; nothing in
   Phase 1 deletes them.
10. **The API prefix is `/v1`; health probes are unprefixed** and never touch the
    database (liveness) or report only `ok`/`unreachable` (readiness).
11. **`InvariantViolation` maps to 500.** An aggregate invariant firing means a bug
    reached the domain, not that the caller did something wrong.
12. **PostgreSQL 17 is the only stateful dependency.** No cache, broker or search
    cluster is installed, imported or required.

## Known limitations (by design, in scope for later phases)

* No reservation-expiry sweep and no fulfilment/shipping endpoints (`SHIPPED` and
  `DELIVERED` exist in the transition table, unreachable until Phase 2).
* No outbox: domain events are dispatched in-process inside the transaction, which is
  the seam the Phase 2 transactional outbox replaces.
* No RLS (Phase 4), no search index (Phase 3), no audit trail beyond
  `inventory_movements` and the structured access log.
* Docker artefacts are authored and statically validated but could not be built here
  (no Docker daemon available in this environment); the runtime migration path was
  executed in the image's exact working layout instead.
