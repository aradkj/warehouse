# 7. Inventory allocation and concurrency: row locks first, unique constraints last

* Status: Accepted
* Date: 2026-10-06
* Phase: 1

## Context

Stock is the one resource where being wrong is unrecoverable: selling a unit twice
cannot be undone by an apology. The specification requires that concurrent requests
for the last unit produce exactly one winner, that the loser fails cleanly, that
available inventory is never negative, that reservations have a lifecycle in which
released or expired holds can never be confirmed, and that correctness comes from
PostgreSQL transactions and row-level locking — not from Redis or application locks
([ADR-0001](0001-postgresql-system-of-record.md)).

## Decision

### The aggregate

`InventoryItem` is a **SKU × warehouse** stock position with three quantities:

```text
available = on_hand - reserved        # never negative
```

`available` is derived, never stored, so it cannot disagree with its inputs. Every
mutation (`receive`, `adjust`, `reserve`, `release`, `commit_reservation`) validates
the invariant inside the aggregate and returns a new state; the invariant is
*decided* in the domain and *enforced* by `ck_inventory_items_available_non_negative`.

### Locking strategy

1. **Pessimistic for allocation.** `ReserveInventory` locks exactly the candidate rows
   it is about to change with `SELECT … FOR UPDATE`, **ordered by `id`**. Deterministic
   order is what stops two multi-row allocations from deadlocking by taking the same
   rows in different orders. Two racers for the last unit serialise here: the first
   commits, the second re-reads the committed state and fails with
   `InsufficientInventory` (or holds nothing, under `ALL_OR_NOTHING`).
2. **Optimistic for edits that are not contended.** Every mutable row carries
   `version`; `save(aggregate, expected_version=…)` writes
   `UPDATE … WHERE version = :expected` and raises `ConcurrencyConflict` (HTTP 409) when
   zero rows match. Adjustment and confirmation paths use this so a stale read cannot
   silently overwrite a newer one.
3. **Registration is race-safe.** A stock row may not exist yet when the first
   adjustment or reservation arrives. `get_or_register_for_update` does: lock-read →
   `INSERT … ON CONFLICT (tenant, warehouse, sku) DO NOTHING RETURNING` → re-lock. Two
   simultaneous requests therefore end up with one row and both see a locked version of
   it. A freshly inserted row is legal at `version = 0`; `ck_inventory_items_version_non_negative`
   allows exactly that and forbids anything lower.
4. **The ledger is append-only.** Every change writes an `InventoryMovement` with
   `quantity_delta`, `on_hand_after`, `reserved_after` and an optional
   `reference_type`/`reference_id`. The ledger is the audit trail for stock and the only
   way to explain a position after the fact; nothing updates or deletes a movement.

### Reservations

* `Reservation` is an aggregate with `ACTIVE → CONFIRMED | RELEASED | EXPIRED`.
  `Reservation.release()` hands the allocated units back; `confirm()` is refused for a
  released or expired hold, and `EXPIRED` is derived from `expires_at` versus the
  clock. An expired hold that nobody swept yet is still not confirmable.
* Allocations are per **stock row**, not per SKU: `reservation_allocations` records
  `inventory_item_id`, `warehouse_id`, `sku_id`, `quantity` and the order line, so a
  release can decrement precisely the rows it incremented.
* One ACTIVE reservation per order is enforced by a partial unique index
  (`uq_reservations_active_per_order`), so a bug cannot leave an order with two live
  holds; the history of released/expired holds is preserved.
* `ALL_OR_NOTHING` is the strategy for order confirmation: if the requirement cannot be
  fully covered, no units are held at all, `reservation` is `None` and the shortfall
  reports the **whole** requirement as missing. `PARTIAL` (used by the standalone
  reservation endpoint) holds only the lines it can fully cover and reports the rest.
* TTL is bounded: `ARAD_RESERVATION_TTL_SECONDS` (default 1800) with a ceiling of
  `ARAD_MAX_RESERVATION_TTL_SECONDS` (86400). Expiring them is a Phase 2 sweep; the
  index `(status, expires_at)` is already there for it.

### Cross-context participation

`OrderingUnitOfWork` composes catalog reads and `InventoryOperations` on the **same
session**, so `CreateOrder` reserves stock in the order's own transaction: an order
that fails to persist cannot leave a hold behind, and a hold cannot be lost while the
order commits ([ADR-0009](0009-in-process-cross-context-adapters.md)).

## Consequences

* Exactly one winner for the last unit, every time; the concurrency suite runs the race
  repeatedly (5 rounds for the single-unit case) and asserts the *outcome*, not the
  mechanism.
* A loser fails with a stable `409 insufficient_inventory` and leaves inventory
  untouched — no partial writes, no negative counters.
* Lock hold times are short (one row set, one transaction, no external calls inside the
  locks), so contention degrades throughput rather than correctness.
* Phase 1 has no reservation-expiry worker, so a hold that is never released keeps its
  stock. That is a deliberate Phase 1 boundary: the timestamp, the state and the sweep
  query all exist, and only the scheduled job is missing.

## Alternatives considered

* **Read-then-write without locking** — rejected: it is a lost-update bug with a
  friendly name.
* **An optimistic-only design (retry on conflict)** — rejected for allocation: it
  produces livelock-ish retry storms exactly under the load that matters, and the
  loser's outcome would depend on retry timing rather than on state.
* **`SERIALIZABLE` isolation everywhere** — rejected as the default: it makes unrelated
  traffic abort, and it does not remove the need for the invariant checks. Locks on the
  rows that matter are cheaper and easier to reason about.
* **Reserving against a cached availability number** — rejected: any cache of a
  quantity that must not go negative is a correctness hazard ([ADR-0001](0001-postgresql-system-of-record.md)).
