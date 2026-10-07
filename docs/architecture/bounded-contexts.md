# Bounded contexts

Arad is one deployable process containing several bounded contexts. Each context owns
its data, its language and its invariants; nothing outside a context reads its
tables. The split is not decoration — it is the seam along which the platform can be
distributed in later phases without rewriting business logic.

| Context | Phase 1 | Owns |
| --- | --- | --- |
| **Identity & Access** | implemented (tenants only) | who the caller is, which tenant |
| **Catalog** | implemented | products, SKUs, prices, purchasability |
| **Inventory** | implemented | warehouses, stock, movements, reservations |
| **Ordering** | implemented | orders, lines, lifecycle, pricing snapshot |
| **Fulfillment** | placeholder | shipping a confirmed order (Phase 2) |
| **Search** | placeholder | product/SKU search index (Phase 3) |
| **Audit** | placeholder | append-only audit trail (Phase 2) |

## Shared kernel

`src/shared/` is not a context; it is the small set of things every context agrees
on. It has three layers and the same dependency rule applies inside it.

* `shared/domain` — `Entity`/`AggregateRoot`, identifiers (`TenantId`, `OrderId`, …),
  `Quantity`, signed `Money`, `Clock`, `DomainEvent`, and the base errors
  (`InvariantViolation`, `InvalidValue`). No context-specific business rule lives
  here.
* `shared/application` — the application kernel: `RequestContext`, `UseCase` /
  `QueryUseCase`, `Command`, `TransactionScope` / `ApplicationUnitOfWork`,
  `EventDispatcher`, `IdempotencyStore`, `IdempotencyState`, pagination, and the
  application error taxonomy (`NotFound`, `InvalidRequest`, `ConcurrencyConflict`,
  `DuplicateResource`, `DuplicateIdempotencyKey`, `IdempotencyOperationInProgress`,
  `MissingReference`).
* `shared/infrastructure` — the async engine and transaction-scope implementation,
  the shared `metadata` object, custom SQLAlchemy types, the table registry, and the
  idempotency store's SQL.

`shared` deliberately holds **no** behaviour that belongs to a context: an
`InventoryItem` rule is inventory's, even if two contexts would both like it.

## Identity & Access

**Language:** tenant, tenant status, actor.

**Phase 1 scope:** a tenant is the unit of isolation. `Tenant` is an aggregate with
a slug (unique globally), a name and `ACTIVE|SUSPENDED` status; `RegisterTenant` and
`GetTenant` are the only use cases, and `POST /v1/tenants` is how a test or an
operator creates one.

**Explicitly out of scope:** authentication, credentials, roles, permissions, ABAC.
Phase 1 trusts the `X-Tenant-Id` header as the tenant context (the specification
calls advanced authentication a later concern) and says so here rather than
half-implementing security. `RequestContext` is the single place that decision is
made, so adding real authentication later changes one dependency provider.

**Owns:** `tenants`.

## Catalog

**Language:** product, SKU, attribute set, product status (`DRAFT|ACTIVE|ARCHIVED`),
SKU status (`ACTIVE|INACTIVE`), price.

**Responsibility:** what can be sold and at what price. Catalog answers "is this
purchasable, and for how much?" and nothing else — it has no idea how much stock
exists.

**Aggregates:** `Product` (identity, lifecycle, attributes) and `Sku` (code, price,
lifecycle, parent product reference). They are two aggregates, not one, and they are
loaded together only where a read model needs both (ADR-0003).

**Use cases:** `CreateProduct`, `ReviseProduct`, `ActivateProduct`,
`ArchiveProduct`, `CreateSku`, `ChangeSkuPrice`, `ActivateSku`, `DeactivateSku`,
plus the queries `GetProduct`, `GetSku`, `ListProducts`.

**Published contract:** `catalog.ports.publication.CatalogQuery` →
`PurchasableSku` (price, currency, product/SKU names, statuses) or
`ProductNotPurchasable`. This is the *only* thing ordering may use.

**Does not own:** stock levels, warehouses, reservations, order lines.

**Owns:** `products`, `skus`.

## Inventory

**Language:** warehouse, stock position (`on_hand`, `reserved`, `available`),
adjustment reason, movement, reservation (hold), allocation, shortfall.

**Responsibility:** the truth about stock, and the only context allowed to change it.
This is where Phase 1's hardest requirement lives: **available inventory never
becomes negative, even when requests arrive at the same instant**.

**Aggregates and rules:**

* `InventoryItem` (SKU × warehouse): `available = on_hand - reserved`, never
  negative; every change appends an `InventoryMovement`; changes are guarded by
  `SELECT … FOR UPDATE` on the stock rows in a deterministic order and by a `version`
  column for optimistic paths (ADR-0007).
* `Reservation`: a hold with `ACTIVE → CONFIRMED | RELEASED | EXPIRED`. A released or
  expired hold can never be confirmed; an ACTIVE hold can. `Reservation.release()`
  applies its allocations back to the items so the units become available again.
* `allocation.py`: given a requirement and the candidate stock rows, decide which
  units to hold (`ALL_OR_NOTHING` or `PARTIAL`) and report the shortfall. All the
  strategy lives here, not in the use case.
* `Warehouse`: a location, with an active flag.

**Use cases:** `RegisterWarehouse`, `AdjustInventory`, `ReserveInventory`,
`ReleaseReservation`, `ConfirmReservation`; queries `GetWarehouse`,
`ListWarehouses`, `GetReservation`.

**Published contract:** `inventory.ports.publication.InventoryOperations` (see
[hexagonal-architecture.md](hexagonal-architecture.md) for the request/outcome
shapes). Ordering reserves, releases and reads holds through it and never sees a row.

**Does not own:** order state, prices, fulfilment.

**Owns:** `warehouses`, `inventory_items`, `inventory_movements`, `reservations`,
`reservation_allocations`.

## Ordering

**Language:** order, order line, line number, pricing snapshot, order status,
backorder, unavailable-stock policy, customer reference.

**Responsibility:** the customer's commitment. An order records *what was agreed* —
SKUs, quantities, prices at the time of submission — and its lifecycle state. It
does not own stock and cannot change it; it asks inventory for a hold.

**Aggregate:** `Order` with its `OrderLine`s.

* Transitions: `PENDING → INVENTORY_RESERVED → CONFIRMED → SHIPPED → DELIVERED`,
  plus `BACKORDERED` from the pending state and `CANCELLED` from `PENDING`,
  `BACKORDERED` and `INVENTORY_RESERVED`. `SHIPPED`, `DELIVERED` and `CANCELLED` are
  terminal; nothing can move backwards (ADR-0004).
* Invariants: at least one and at most 100 lines, one line per SKU, positive
  quantities, a total that is the sum of its line snapshots.
* `UnavailableStockPolicy`: `BACKORDER` (default, from
  `ARAD_UNAVAILABLE_STOCK_POLICY`) accepts an order that cannot be fully covered and
  parks it as `BACKORDERED`; `REJECT` refuses it with `InsufficientInventory`.
* `ConfirmOrder` re-checks coverage per line, releases a partial hold before asking
  for a complete one, and only then confirms. Confirmation **keeps the hold ACTIVE**:
  the units leave the warehouse when the order is fulfilled (Phase 2), so Phase 1
  must not consume them (ADR-0004).

**Use cases:** `CreateOrder`, `ConfirmOrder`, `CancelOrder`; queries `GetOrder`,
`ListOrders`.

**Depends on:** `CatalogQuery` (price and purchasability) and
`InventoryOperations` (holds). Both are ports, injected; ordering's tests run
against fakes and never against PostgreSQL.

**Owns:** `orders`, `order_lines`.

## Fulfillment — placeholder

**Phase 2.** Shipping a confirmed order, tracking parcels, decrementing physical
stock at dispatch. `src/fulfillment/ports/publication.py` exists and declares
`PLANNED_PHASE = 2`; there is no aggregate and no table. The reservation that
`ConfirmOrder` leaves ACTIVE is the interface this context will consume.

## Search — placeholder

**Phase 3.** Product and SKU search. `src/search/ports/indexer.py` declares the
`SearchIndexer` protocol and `PLANNED_PHASE = 3`; Phase 1's catalog list endpoint
reads PostgreSQL directly, and the specification puts Elasticsearch in a later
phase.

## Audit — placeholder

**Phase 2.** A durable, append-only trail of who did what. `src/audit/domain/entry.py`
sketches the entry and declares `PLANNED_PHASE = 2`. Phase 1's equivalent is the
`inventory_movements` ledger (for stock) and the structured access log with
correlation ids (for requests).

## Boundary rules the tests enforce

1. A context never imports another context's `domain`, `application` or `adapters`
   modules — only its `ports.publication`.
2. No context reads another context's tables; only its own adapter does.
3. Cross-context calls happen inside one transaction (Phase 1) through a port, so an
   order and its reservation commit or roll back together.
4. Each context owns its exceptions and maps them to its own HTTP codes; the
   framework only knows the shared taxonomy.
5. Placeholders contain no behaviour — a `PLANNED_PHASE` marker and a protocol, so
   nothing can claim a later phase has been implemented.

`tests/unit/test_architecture.py` asserts rules 1 and 5 statically.
