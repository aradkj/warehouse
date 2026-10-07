# 4. Order lifecycle: confirmation keeps the hold, stock leaves at fulfilment

* Status: Accepted
* Date: 2026-10-06
* Phase: 1

## Context

An order must not be confirmable without inventory to back it, must not move
backwards, and must not leave stock in an ambiguous state. The specification defines
the lifecycle states and forbids arbitrary backwards transitions, but it does not say
*when* the reserved units stop being reserved — at confirmation, or when the goods
physically leave the warehouse. That choice decides what a "confirmed order" means, and
the two options are not equivalent.

## Decision

**Confirmation keeps the reservation ACTIVE; the units leave the warehouse when the
order is fulfilled (Phase 2).**

The transition table is explicit in `src/ordering/domain/order.py`:

```text
PENDING ──► INVENTORY_RESERVED ──► CONFIRMED ──► SHIPPED ──► DELIVERED
   │  ▲              │
   │  │              │
   ├──┴─ BACKORDERED │
   │                 │
   └─────────────────┴──► CANCELLED

reachable in Phase 1:  PENDING → {INVENTORY_RESERVED, BACKORDERED, CANCELLED}
                       BACKORDERED → {INVENTORY_RESERVED, CANCELLED}
                       INVENTORY_RESERVED → {CONFIRMED, BACKORDERED, CANCELLED}
fulfilment only (Phase 2): CONFIRMED → SHIPPED → DELIVERED
terminal:              DELIVERED, CANCELLED
```

* A cancellation may be *initiated* from `PENDING`, `BACKORDERED` or
  `INVENTORY_RESERVED` (`_CANCELLABLE`). Cancelling a `CONFIRMED` order is deliberately
  not an order-level move in Phase 1: once an order is confirmed the units are held and
  committed, and undoing that belongs to fulfilment's return/reversal flow (Phase 2),
  not to a status change. `DELIVERED` and `CANCELLED` are terminal. Any other move —
  including confirming an already-confirmed order — raises `InvalidOrderTransition`,
  which the API maps to 409 with `{"from": …, "to": …}`.
* `CreateOrder` asks inventory to hold the required quantity. Full coverage ⇒
  `INVENTORY_RESERVED`; the `BACKORDERED` policy (default, `ARAD_UNAVAILABLE_STOCK_POLICY`)
  accepts the order anyway and parks it in `BACKORDERED` holding whatever could be
  reserved; the `REJECT` policy raises `InsufficientInventory` and writes nothing.
* `ConfirmOrder` re-checks coverage line by line, releases a partial hold before asking
  for a complete one (the aggregate refuses a second ACTIVE hold for the same order —
  see the partial unique index in [data-model.md](../architecture/data-model.md)), and
  confirms only when every line is covered. The reservation stays `ACTIVE`.
* **The units therefore remain reserved after confirmation**, and the stock position is
  unchanged by `confirm`: `on_hand` and `reserved` both still include them. They are
  decremented from both columns at fulfilment (`InventoryItem.commit_reservation`,
  called by Phase 2's fulfilment flow), which is the only moment the units physically
  leave.

## Consequences

* A confirmed order is a promise secured by stock that no one else can take, but the
  warehouse has not shipped it yet: physically it is still there. Decrementing `on_hand`
  at confirmation would make the ledger lie about what is on the shelf and would break
  Phase 2's "pick this reservation" step, which needs the units to still be there.
* Availability for a confirmed but unshipped order is unchanged — correct, because the
  units are still reserved and not available to anyone else.
* `SHIPPED` and `DELIVERED` exist in the transition table without an endpoint in
  Phase 1: the states are part of the model, the endpoints belong to fulfilment
  (Phase 2). They are unreachable in Phase 1, and the architecture test asserts that
  `src/fulfillment` is still a placeholder.
* Cancelling a reserved-but-unconfirmed order releases the hold, because `CancelOrder`
  releases whatever reservation is still ACTIVE (and is safe to call twice: the
  aggregate treats a repeat cancellation as a no-op rather than a backwards
  transition).

## Alternatives considered

* **Consume the reservation at confirmation** (`on_hand -= q`, `reserved -= q`) —
  rejected: it makes the stock ledger claim the goods have left while they are still in
  the warehouse, and it leaves Phase 2 fulfilment with nothing to pick. It was
  implemented first and then reversed; the tests were updated with it.
* **Let SHIPPED/DELIVERED be reachable through a generic status endpoint** — rejected:
  it would put business transitions behind an API that bypasses the aggregate and the
  inventory checks.
