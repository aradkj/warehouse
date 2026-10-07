# 1. PostgreSQL is the system of record, including for inventory correctness

* Status: Accepted
* Date: 2026-10-06
* Phase: 1

## Context

The platform must never oversell. The specification requires that concurrent
reservations of the same stock are serialised by PostgreSQL transactions and row-level
locking, and explicitly rules out Redis or application-level locks as the mechanism
that protects inventory. Requests arrive concurrently through several workers, and a
process can die at any moment between "check stock" and "write stock".

There are three places a stock decision could be made:

1. in the application process, guarded by a lock (in-process or Redis);
2. in PostgreSQL, guarded by row locks inside a transaction;
3. in a queue with a single writer.

## Decision

**PostgreSQL is the system of record.** Every write that can affect availability
happens inside a transaction that takes `SELECT … FOR UPDATE` row locks on the stock
rows it is about to change, in a deterministic order (`ORDER BY id`), with an
optimistic `version` column as a second line of defence for read-modify-write paths
that do not lock. The database also carries the invariant as a `CHECK` constraint
(`reserved <= on_hand`), so a bug cannot write an oversold row even if it bypasses the
aggregate.

The application owns the *transaction boundary* (one unit of work per command,
`src/shared/infrastructure/db/session.py`), but not the *correctness boundary* — that
belongs to the database.

No cache, message broker or in-process lock participates in a stock decision in
Phase 1. Redis appears nowhere in the codebase; RabbitMQ and Celery are Phase 2+ and
are not installed.

## Consequences

* Correctness survives process restarts, multiple API workers and network retries,
  because it never depended on process memory.
* Concurrent reservations serialise on row locks; contention is bounded by the number
  of stock rows touched, and the concurrency suite proves the outcome (exactly one
  winner for the last unit) rather than the mechanism.
* A transaction that is rolled back leaves nothing behind — including its idempotency
  claim, which is deliberately written in the same transaction ([ADR-0006](0006-idempotency-semantics.md)).
* Lock ordering (`ORDER BY id`) prevents deadlocks between two multi-row allocations
  that touch the same rows in different order.
* Scale-out is bounded by one primary for writes. That is the intended Phase 1
  trade-off: correctness first. Read replicas and partitioning are later, and neither
  changes the invariant.

## Alternatives considered

* **Redis distributed locks (`SETNX`)** — rejected: the specification rules it out, and
  it is not a correctness mechanism. A lock with a TTL either blocks legitimate work
  when it expires early or, when it expires late, does not stop a second worker from
  writing. It also adds a second system whose failure mode corrupts stock.
* **Application-level `asyncio.Lock` / `threading.Lock`** — rejected: it only protects
  one process, and it is exactly the kind of "global mutable state" the project rules
  forbid.
* **A single-writer queue per SKU** — rejected as premature infrastructure for Phase 1;
  it also does not solve correctness, only concurrency, and would need a durable broker
  before it is trustworthy.
* **PostgreSQL advisory locks** — viable, but row locks are simpler, give up
  automatically with the transaction, and lock the actual rows the decision depends on.
