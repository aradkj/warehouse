# 2. A modular monolith with enforced boundaries, before distribution

* Status: Accepted
* Date: 2026-10-06
* Phase: 1

## Context

The platform is described as distributed, with distinct bounded contexts (identity,
catalog, inventory, ordering, fulfillment, search, audit). Phase 1, however, has to
deliver a working foundation: schema, domain model, application layer, HTTP API,
concurrency safety and tests. Splitting into services first would mean paying
network, serialisation, partial-failure and deployment costs before a single business
rule is proven — and the specification's Phase 1 asks for a modular monolith with
hexagonal architecture and an explicit dependency direction.

## Decision

**One deployable process, many bounded contexts.** Each context is a package under
`src/` with the same internal shape (`domain`, `application`, `ports`, `adapters`), and
the dependency direction is always inwards:

* a context never imports another context's domain, application or adapter modules;
* cross-context calls go through the other context's **published port**
  ([ADR-0008](0008-published-ports.md));
* no context reads another context's tables;
* contexts share only `src/shared`, which contains no context-specific business rule.

The boundaries are executable: `tests/unit/test_architecture.py` walks the AST of every
module and fails on a violation, so the boundary cannot quietly rot.

## Consequences

* One process to run, one migration chain, one test suite — Phase 1 is verifiable end
  to end.
* Extracting a context later is a matter of implementing the existing publication port
  with a network adapter ([ADR-0009](0009-in-process-cross-context-adapters.md)); the
  domain and application layers do not change.
* The cost is discipline: nothing stops a developer from writing
  `from inventory.adapters.persistence import tables` in ordering — except the
  architecture test, which is exactly why it exists.
* Transactional consistency between contexts is currently free, because they share a
  transaction. Once they do not, this decision is revisited with a saga/outbox design
  (Phase 2).

## Alternatives considered

* **Microservices from day one** — rejected: it makes every business rule harder to
  prove and adds distributed failure modes to a system whose Phase 1 goal is
  correctness of stock under concurrency.
* **A layered monolith** (`models/`, `services/`, `api/` folders) — rejected: layering
  by technical concern does not create boundaries between contexts, so ordering and
  inventory would share tables and drift into one model.
* **A shared "core" package with all aggregates** — rejected: that is a single
  context with extra steps; it makes the eventual split a rewrite.
