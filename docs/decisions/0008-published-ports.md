# 8. Published ports are the only way one context uses another

* Status: Accepted
* Date: 2026-10-06
* Phase: 1
* Related: [ADR-0002](0002-modular-monolith-before-distribution.md), [ADR-0009](0009-in-process-cross-context-adapters.md)

## Context

Ordering needs two things it does not own: the price and purchasability of a SKU
(catalog) and a hold on stock (inventory). The cheap way to get them is to import the
other context's repository, or to read its tables directly from a query. That works
until the other context changes a column, and it makes the boundary meaningless — the
two contexts then have to be deployed, tested and migrated together regardless of what
the diagram says.

## Decision

A context may expose a **published contract** in `<context>/ports/publication.py`, and
that module is the only thing another context may import from it.

* **The provider owns the contract, including its DTOs.** `AllocationView`,
  `ReservationView`, `ShortfallView`, `ReservationRequest`, `ReservationOutcome` are
  defined in `inventory/ports/publication.py`; `CatalogQuery`, `PurchasableSku` and the
  `ProductNotPurchasable` error are defined in (or re-exported from)
  `catalog/ports/publication.py`. A consumer cannot invent the interface it would have
  preferred: if ordering wants a field, that is a conversation with the provider, which
  is exactly the pressure that keeps contracts honest.
* **The contract is expressed in the provider's language and the consumer's terms.**
  Ordering asks "reserve these requirements for this order" and receives an outcome; it
  does not see aggregates, rows or sessions.
* **No shared tables, ever.** Each context's adapters own their tables; a cross-context
  read goes through the provider's query implementation (for example
  `SqlAlchemyCatalogQuery`, which reads catalog's tables through catalog's session).
* **Errors cross the boundary as types, not as strings.** `ProductNotPurchasable` is
  raised by catalog and caught by ordering, which decides what it means for an order.
* **The dependency direction is enforced statically.** `tests/unit/test_architecture.py`
  fails the build if a context imports another context's `domain`, `application` or
  `adapters` module.

Ordering's imports today are exactly:
`catalog.ports.publication` and `inventory.ports.publication`.

## Consequences

* Each context can be tested without the others: the application suite's fakes
  implement the published protocols (`tests/fakes/ports.py`), so ordering's use cases
  run with no PostgreSQL at all.
* The contract is already shaped like a remote one — plain data, explicit errors, no
  identity map, no lazy loading — which is what makes
  [ADR-0009](0009-in-process-cross-context-adapters.md) a small change later.
* Renaming or reshaping a published DTO is a breaking change and must be treated as
  one, even inside the monolith.
* There is a real cost: cross-context reads are less convenient than a join. Phase 1
  pays it in one place only (order pricing and holds).

## Alternatives considered

* **Reading the other context's tables from a repository in the consumer** — rejected:
  it silently couples schemas, and it is the single most common way a modular monolith
  turns into a distributed-data mess.
* **A shared "integration" package with both sides' models** — rejected: it becomes a
  third place to keep in sync and hides which context owns the data.
* **Publishing domain aggregates instead of DTOs** — rejected: the consumer would then
  depend on the provider's aggregate internals, including its invariants and its
  persistence state.
* **Deferring the boundary until extraction** — rejected: without a rule now, the
  extraction never happens; the graph of direct imports grows instead.
