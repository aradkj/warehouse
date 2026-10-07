# 9. In-process cross-context adapters and cross-context foreign keys

* Status: Accepted
* Date: 2026-10-06
* Phase: 1
* Related: [ADR-0002](0002-modular-monolith-before-distribution.md), [ADR-0008](0008-published-ports.md)

## Context

Phase 1 is one process and one database, but the contexts are meant to be separable.
Two concrete questions have to be answered now, because the code cannot be written
without an answer:

1. When ordering calls inventory, does it join ordering's transaction?
2. Are cross-context references (`reservations.order_id`, `order_lines.sku_id`)
   database foreign keys, or loose identifiers?

The answers differ depending on whether the contexts stay in one database. Getting them
wrong in either direction is expensive: too loose, and Phase 1 loses referential
integrity it could have had; too tight, and extraction becomes a rewrite.

## Decision

**Phase 1: in-process calls, real foreign keys, one transaction.**

* `InventoryOperations` is implemented by
  `inventory/adapters/in_process.py::InProcessInventoryOperations`, which translates the
  published request into inventory's *own* use cases (`ReserveInventory`,
  `ReleaseReservation`, `ConfirmReservation`) and runs them **on the caller's
  transaction scope**. It never opens its own scope, never commits, and contains no
  business logic.
* `CatalogQuery` is implemented by
  `catalog/adapters/persistence/publication.py::SqlAlchemyCatalogQuery` on the same
  session, so a price read sees the same snapshot as the order write.
* `OrderingUnitOfWork` composes the order repository, the catalog port and the inventory
  port over one session. `CreateOrder` therefore commits the order, its lines, its
  reservation and the idempotency record in a single transaction: the "an order and its
  hold are all-or-nothing" property is free rather than engineered.
* Cross-context references are **real foreign keys** inside the monolith
  (`reservations.order_id → orders.id` with `ON DELETE RESTRICT`), because PostgreSQL
  can enforce them and Phase 1 should take every guarantee it can get.
* The in-process adapter **does not inherit the caller's `Idempotency-Key`** ([ADR-0006](0006-idempotency-semantics.md)):
  the key identifies the caller's operation, not each internal step, and reusing it
  would make an internal call look like an independent retry.
* A cross-context call is *not* a new transaction boundary, and it is not an event:
  nothing here publishes a message. Phase 1 has no broker ([ADR-0002](0002-modular-monolith-before-distribution.md)).

**Later phase: the same port, a different adapter.**

When a context is extracted, the changes are exactly:

1. the adapter becomes an HTTP/RPC client implementing the same protocol;
2. the cross-context foreign key becomes a plain identifier (the comment in
   `inventory/adapters/persistence/tables.py` marks the spot) and referential integrity
   is replaced by a compensating check;
3. the shared transaction becomes a saga or a transactional outbox — the seam already
   exists, because domain events are dispatched *inside* the use case's transaction
   (`shared/application/use_case.py`), so a Phase 2 outbox writer is a drop-in
   replacement for the in-process dispatcher;
4. `OrderingUnitOfWork` stops composing the other context's port directly and starts
   composing the remote client.

Nothing in `ordering/domain` or `ordering/application` changes. That is the test of
whether the boundary was drawn correctly, and the application suite already proves it:
`tests/fakes/` implements the published protocols, so the use cases run with no
database at all.

## Consequences

* Phase 1 gets strong consistency and referential integrity for free, which is the right
  trade for a foundation phase.
* The monolith must not grow a habit of *new* cross-context foreign keys; each one is a
  future migration.
* Distinguishing "the caller's operation" from "an internal step" is a design rule, not
  a mechanism: the adapter deliberately passes no key.

## Alternatives considered

* **Cross-context calls in their own transactions** — rejected: an order that fails after
  reserving would strand stock, and Phase 1 has neither a saga nor a compensation flow.
* **Loose identifiers and no foreign keys "to be ready for distribution"** — rejected:
  it gives up real integrity today for a hypothetical tomorrow, and Phase 1's actual
  requirement is correctness.
* **Publishing events between contexts now** — rejected: without a broker or an outbox
  (both Phase 2) it would be either synchronous coupling in disguise or unreliable
  delivery.
