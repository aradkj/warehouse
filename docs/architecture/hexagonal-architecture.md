# Hexagonal architecture (ports & adapters)

The platform is a modular monolith built as a hexagonal application: the business
model sits in the middle, the outside world (HTTP, PostgreSQL, other contexts) plugs
in through ports, and **dependencies only ever point inwards**. This document
explains the rule, the mechanics, and what it buys Phase 1.

## Layers

```text
        ┌──────────────────────────────────────────────────────────────┐
        │ adapters   (FastAPI routers · SQLAlchemy repositories ·      │
        │             in-process cross-context adapters · CLI)         │
        │   ┌──────────────────────────────────────────────────────┐   │
        │   │ application   (use cases: orchestration, transaction │   │
        │   │                boundary, idempotency, events)        │   │
        │   │   ┌──────────────────────────────────────────────┐   │   │
        │   │   │ domain   (aggregates, invariants, value      │   │   │
        │   │   │           objects, domain events, errors)    │   │   │
        │   │   └──────────────────────────────────────────────┘   │   │
        │   │ ports   (Protocols the application depends on)       │   │
        │   └──────────────────────────────────────────────────────┘   │
        └──────────────────────────────────────────────────────────────┘
```

Inside a context:

```text
src/inventory/
  domain/        inventory_item.py reservation.py warehouse.py allocation.py
                 value_objects.py events.py errors.py     ← no framework imports
  application/   commands.py use_cases.py queries.py      ← orchestration only
  ports/         repositories.py unit_of_work.py publication.py
  adapters/      persistence/{tables,mappers,repositories,unit_of_work}.py
                 in_process.py                           ← published contract
```

## The dependency rule, and how it is enforced

| Layer | May import | Must not import |
| --- | --- | --- |
| `domain` | `shared.domain` (and itself) | SQLAlchemy, Pydantic, FastAPI, asyncpg, any adapter |
| `application` | own `domain` + own `ports` + `shared.application` | SQLAlchemy, FastAPI, another context's internals |
| `ports` | own `domain`, `shared` types | adapters |
| `adapters` | everything above | — |
| `bootstrap` | everything | — |

`tests/unit/test_architecture.py` parses every module with `ast` and fails the build
on a violation: framework imports under `*/domain` or `*/ports`, SQLAlchemy under
`*/application`, SQL or ORM calls in routers, relative imports across contexts, or a
context importing another context's non-published module. `ruff` enforces the
remaining mechanical parts (banned relative imports, formatting, unused code) and
`mypy` runs in strict-ish mode over all 141 source files.

The point is not purity for its own sake: this is what makes inventory's concurrency
rules testable in microseconds with fakes, and what makes the later extraction of a
context into a service a matter of writing a new adapter instead of rewriting the
domain.

## Ports

A port is a `typing.Protocol` describing what the application needs, in the
application's own language. There are three kinds.

**Repository ports** — persistence of aggregates. Examples: `ProductRepository`,
`SkuRepository`, `InventoryItemRepository`, `InventoryMovementRepository`,
`ReservationRepository`, `OrderRepository`, `WarehouseRepository`, `TenantRepository`.
They take primitives and return aggregates (`OrderRepository.get(tenant_id, order_id)`)
and never expose SQLAlchemy models or a session to the caller.

**Unit-of-work ports** — the transaction boundary plus the repositories that belong
together. `shared.application.unit_of_work.TransactionScope` knows how to enter,
commit and roll back; `ApplicationUnitOfWork` adds the idempotency store; each
context extends it with its own repositories (`InventoryUnitOfWork`,
`OrderingUnitOfWork`, `CatalogUnitOfWork`, `IdentityUnitOfWork`).

**Publication ports** — what one context offers another:

* `catalog.ports.publication.CatalogQuery.resolve(context, sku_ids)` →
  `tuple[PurchasableSku, ...]`, raising `ProductNotPurchasable`. Owns the
  `PurchasableSku` DTO.
* `inventory.ports.publication.InventoryOperations` — `reserve`, `release`,
  `confirm`, returning `ReservationOutcome` / `ReservationView` / `AllocationView` /
  `ShortfallView`. Owns those DTOs.

Publication ports are the *only* permitted cross-context dependency, and the owning
context defines them — the consumer does not get to invent the interface it would
like. Ordering imports `catalog.ports.publication` and
`inventory.ports.publication`, nothing else from those contexts (ADR-0008).

## Adapters

**Driving adapters** (the outside world calls us): `bootstrap/routers/*`. A router
validates a request schema, builds a command, calls a use case, and maps the result
to a response schema. It contains no business rule: there is no arithmetic, no state
check, no query. `apply_outcome()` sets the status code and the `Idempotent-Replay`
header; `parse_if_match()` turns the `If-Match` header into an expected version.

**Driven adapters** (we call the outside world): `*/adapters/persistence/*`. Tables,
mappers (row ↔ aggregate), repositories implementing the ports, and the unit of work
that owns the session and the transaction. `bootstrap/dependencies.py` is the
composition root: it builds the engine, the scope factory and the use cases and hands
them to the routers through FastAPI dependencies.

**Cross-context adapters**: `inventory/adapters/in_process.py`
(`InProcessInventoryOperations`) and
`catalog/adapters/persistence/publication.py` (`SqlAlchemyCatalogQuery`). The first
delegates to inventory's use cases **on the caller's transaction scope** and never
commits; the second reads catalog's own tables through catalog's own session. Both
are swappable for HTTP/RPC clients with no change to ordering (ADR-0009).

## Transaction participation

The most important mechanic in Phase 1, and the one that makes "an order and its
reservation are all-or-nothing" true:

1. The use case opens **one** `TransactionScope`. Its unit of work composes every
   repository the operation needs — for `CreateOrder`: orders, catalog reads and
   inventory operations on the same session.
2. `InProcessInventoryOperations` receives the caller's scope. It runs inventory's
   own use cases inside it, so a hold is written in the order's transaction; if the
   order fails afterwards, the hold disappears with it.
3. Repositories never commit. Only the outermost use case commits, exactly once, at
   the end (`shared/infrastructure/db/session.py`).
4. The in-process adapter does **not** inherit the caller's `Idempotency-Key`: the
   caller's key belongs to the caller's operation. Inventory's internal call is not a
   separate idempotent operation.

This is what a distributed system eventually replaces with a saga or an outbox
(Phase 2+). Until then, the cheapest correct answer to "should the reservation and
the order commit together?" is "they are the same transaction" (ADR-0007).

## Idempotency as an application-kernel concern

Idempotency is not repeated per endpoint. It lives once in
`shared.application.use_case.UseCase.__call__`:

```text
enter UoW
  claim (tenant_id, key) with INSERT … ON CONFLICT DO NOTHING
    ├─ row inserted          → STARTED: run the business write, then complete() the record
    ├─ row existed, completed→ REPLAY: refuse a different fingerprint, otherwise
    │                          _reload() the stored resource and return it, replayed=True
    ├─ row existed, completed, different request_hash → DuplicateIdempotencyKey (409)
    └─ row existed, in progress (concurrent)          → IdempotencyOperationInProgress (409)
  dispatch domain events (same transaction)
  complete() or abandon() the record
commit
```

Because the claim is written in the same transaction as the business change, a
rollback removes the claim: the client's next retry is a clean first attempt, not a
poisoned `in_progress` record. `abandon()` covers the use case that legitimately
produced no durable resource (a PARTIAL reservation that could hold nothing), so a
retry with the same key is not blocked by a stale claim. The full semantics, including
retention and the `Idempotent-Replay` header, are in
[ADR-0006](../decisions/0006-idempotency-semantics.md).

## Domain events

Aggregates record events (`Order.placed`, `Reservation.released`, …) and expose
`pull_events()`. The use case hands them to the `EventDispatcher` **inside** the
database transaction. The active system implementation is `SqlAlchemyOutboxEventDispatcher`
(wired in `bootstrap.dependencies.wire_use_cases`), which atomically persists each event
to the `outbox_events` table before commit. Downstream delivery is handled asynchronously
by the `OutboxRelayService` runner (polling with `SELECT ... FOR UPDATE SKIP LOCKED` and
publishing to RabbitMQ with publisher confirms) and consumed idempotently via consumer inbox
tables. Nothing dispatches after commit without transactional outbox persistence.

## Error mapping

The domain raises domain errors (`InsufficientInventory`, `InvalidOrderTransition`,
`ReservationExpired`, …). The application raises application errors (`NotFound`,
`InvalidRequest`, `ConcurrencyConflict`, `DuplicateIdempotencyKey`, …). Both are
framework-free. `bootstrap/errors.py` is the single place that turns them into the
HTTP contract `{"error": {code, message, details, correlation_id, type}}`; unknown
codes map to 500, so a new domain error must be registered deliberately. Nothing
leaks a stack trace, a SQL statement or a driver message.

## Testing seams

The layering is what makes the test pyramid possible:

| Layer | Test style | What it proves |
| --- | --- | --- |
| domain | pure unit tests | invariants, transitions, allocation maths |
| application | `tests/fakes/` — in-memory ports | orchestration, idempotency, policy, events |
| adapters | `tests/repository/`, real PostgreSQL | mapping, constraints, optimistic concurrency, tenant scoping |
| driving | `tests/api/`, ASGI client | status codes, validation, error contract, headers |
| end-to-end | `tests/concurrency/`, `tests/integration/` | no overselling under real parallel transactions; schema reproducibility |

The fakes implement the *same ports* the SQLAlchemy adapters implement
(`tests/fakes/ports.py`), which is why a use case cannot accidentally depend on a
database detail: if it did, the fake-backed suite would not compile.
