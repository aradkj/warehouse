# Data model

PostgreSQL is the system of record for Phase 1 (ADR-0001). This document describes
the eleven tables, why they are shaped that way, and which invariants PostgreSQL
itself enforces. The schema is created by a single Alembic revision,
`0001_initial_schema` (see [ADR-0005](../decisions/0005-migration-strategy.md)).

The SQLAlchemy definitions live next to the context that owns them
(`src/<context>/adapters/persistence/tables.py`) and are collected by
`src/shared/infrastructure/db/registry.py`. `tests/integration/test_migrations.py`
asserts on every run that the migrations and those definitions agree
(`compare_metadata` reports zero drift) and that the constraints below really exist
in PostgreSQL.

## Overview

```text
                 ┌──────────┐
                 │ tenants  │  identity
                 └────┬─────┘
        ┌─────────────┼───────────────┬────────────────┬──────────────┐
        │             │               │                │              │
   ┌────▼─────┐  ┌────▼──────┐   ┌────▼──────┐   ┌─────▼─────┐  ┌─────▼──────┐
   │ products │  │ warehouses│   │  orders   │   │reservations│  │idempotency │
   └────┬─────┘  └────┬──────┘   └────┬──────┘   └─────┬─────┘  │ _records   │
        │             │               │                │        └────────────┘
   ┌────▼─────┐       │          ┌────▼───────┐   ┌────┴──────────────┐
   │   skus   │◄──────┼──────────│ order_lines│   │allocation rows    │
   └────┬─────┘       │          └────┬───────┘   │(reservation_      │
        │             │               │           │ allocations)      │
        └──────┬──────┴───────────────┴───────────┴───────────────────┘
               │
        ┌──────▼──────────┐        ┌───────────────────────┐
        │ inventory_items │───────►│ inventory_movements   │
        └─────────────────┘        └───────────────────────┘
```

Plain lines are foreign keys. `inventory_items` is the only mutable quantity table:
`on_hand` and `reserved` live there, every change appends an immutable
`inventory_movements` row, and `reservations` + `reservation_allocations` record
which units belong to whom.

## Tenancy

* Every business table carries `tenant_id uuid NOT NULL` with
  `FOREIGN KEY … ON DELETE RESTRICT` to `tenants.id`.
* Every tenant-scoped index is prefixed with `tenant_id`, because every query is.
* Cross-tenant references are impossible by construction: a child row carries its
  own `tenant_id`, and the repository layer only ever writes a child with the
  caller's tenant (ADR-0010). A row whose `tenant_id` disagrees with its parent is
  therefore not reachable through the API.
* `tenants` is the only table without a `tenant_id`; it is the tenant.
* No PostgreSQL RLS in Phase 1 (ADR-0010; RLS is Phase 4). Isolation is enforced by
  the application design above, and `tests/api/test_api.py::TestTenantIsolationOverHttp`
  proves it over HTTP.

## Tables

Column lists are the authoritative set. `?` marks a nullable column; everything else
is `NOT NULL`. All timestamps are `timestamptz` (asserted by the test suite). Money
is always an integer count of minor units (`unit_price_minor`, plus `currency` as a
three-letter ISO code) — no floating point anywhere.

### `tenants` (identity)

`id`, `slug`, `name`, `status`, `created_at`, `updated_at`, `version`.

* `uq_tenants_slug`, `ck_tenants_slug_format` (`^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$`).
* `status ∈ {ACTIVE, SUSPENDED}`.

Phase 1 creates tenants so that every request has a tenant context; deletion is not
part of Phase 1, hence `RESTRICT` everywhere.

### `products`, `skus` (catalog)

`products`: `id`, `tenant_id`, `slug`, `name`, `description?`, `attributes` (jsonb),
`status`, `created_at`, `updated_at`, `version`.
`skus`: `id`, `tenant_id`, `product_id`, `code`, `name`, `attributes` (jsonb),
`unit_price_minor`, `currency`, `status`, `created_at`, `updated_at`, `version`.

* `uq_products_tenant_id_slug`, `uq_skus_tenant_id_code` — a slug is unique *within
  a tenant*, never globally, so two tenants can both have `blue-tee`.
* `ck_products_status_valid` (`DRAFT|ACTIVE|ARCHIVED`),
  `ck_skus_status_valid` (`ACTIVE|INACTIVE`),
  `ck_products_slug_format`, `ck_skus_code_format`,
  `ck_skus_unit_price_non_negative`, `ck_*_version_positive` (`version >= 1`).
* Product and SKU are separate aggregates (ADR-0003): two independent lifecycles,
  two optimistic-concurrency counters, and inventory/ordering reference the SKU —
  the thing that is actually stocked and sold — never the product.
* `attributes` is unvalidated jsonb on purpose: Phase 1 must not invent a catalogue
  attribute schema the specification does not define. It is opaque data carried
  through the API.

### `warehouses` (inventory)

`id`, `tenant_id`, `code`, `name`, `is_active`, `created_at`, `updated_at`,
`version`.

* `uq_warehouses_tenant_id_code`, `ck_warehouses_code_format`.
* A warehouse is an inventory location, not a catalogue concept: it lives in the
  inventory context even though fulfilment will later want more of it.

### `inventory_items` (inventory)

`id`, `tenant_id`, `warehouse_id`, `sku_id`, `on_hand`, `reserved`, `created_at`,
`updated_at`, `version`.

* `uq_inventory_items_tenant_id_warehouse_id_sku_id` — one stock row per SKU per
  warehouse. This is also the arbiter for `INSERT … ON CONFLICT DO NOTHING` when two
  requests race to create the same row.
* `ck_inventory_items_available_non_negative` — `reserved <= on_hand`. **This is the
  overselling guarantee at the storage layer.** The aggregate enforces
  `available = on_hand - reserved >= 0` before it writes (ADR-0007), and PostgreSQL
  refuses the write if a bug or a rogue statement ever tries to get below zero.
* `ck_inventory_items_on_hand_non_negative`, `ck_inventory_items_reserved_non_negative`,
  `ck_inventory_items_version_non_negative` (`>= 0`: a row is created by
  `INSERT … ON CONFLICT` **before** an `UPDATE` touches it, so 0 is legal and
  expected).
* `version` is the optimistic-concurrency counter, incremented on every write; the
  pessimistic path takes `SELECT … FOR UPDATE` instead.

### `inventory_movements` (inventory)

`id`, `tenant_id`, `inventory_item_id`, `warehouse_id`, `sku_id`, `movement_type`,
`reason?`, `quantity_delta`, `on_hand_after`, `reserved_after`, `reference_type?`,
`reference_id?`, `note?`, `occurred_at`, `created_at`.

* Append-only: there is no `updated_at` and no `version`, and nothing in the
  application ever updates or deletes a row.
* `ck_inventory_movements_quantity_delta_non_zero` — a movement that moves nothing is
  a bug, not a record.
* `ck_inventory_movements_on_hand_after_non_negative`,
  `ck_inventory_movements_reserved_after_non_negative` — the ledger cannot record an
  impossible state either.
* `ck_inventory_movements_movement_type_valid`:
  `RECEIPT|ADJUSTMENT|RESERVATION|RESERVATION_RELEASE|RESERVATION_CONFIRMATION`.
* `reference_type` / `reference_id` point at whatever caused the movement (an
  adjustment, a reservation). They are deliberately *not* foreign keys: the ledger
  outlives the objects it mentions, and Phase 1 has two reference kinds.

### `reservations`, `reservation_allocations` (inventory)

`reservations`: `id`, `tenant_id`, `order_id?`, `status`, `expires_at`,
`confirmed_at?`, `released_at?`, `release_reason?`, `created_at`, `updated_at`,
`version`.
`reservation_allocations`: `id`, `tenant_id`, `reservation_id`, `inventory_item_id`,
`warehouse_id`, `sku_id`, `order_line_id?`, `quantity`, `created_at`.

* `ck_reservations_status_valid`: `ACTIVE|CONFIRMED|RELEASED|EXPIRED`.
  `ck_reservations_expiry_after_creation` (`expires_at > created_at`) — a hold that
  expires before it exists is nonsense.
* `ck_reservations_released_has_timestamp`,
  `ck_reservations_confirmed_has_timestamp` — terminal states carry their evidence.
  The status/column pairing is enforced in both directions, so a row can never claim
  `RELEASED` without saying when.
* `uq_reservations_active_per_order` — a **partial** unique index,
  `UNIQUE (tenant_id, order_id) WHERE status = 'ACTIVE' AND order_id IS NOT NULL`.
  An order may hold at most one live reservation, while the history of released and
  expired holds stays. `order_id IS NULL` covers standalone reservations that the
  `POST /v1/reservations` endpoint creates, which no order owns.
* `ck_reservation_allocations_quantity_positive` — no zero-quantity allocations.
* Allocations carry `inventory_item_id`, not just the SKU: the hold is on concrete
  stock rows, which is what makes multi-warehouse allocation and release precise.

### `orders`, `order_lines` (ordering)

`orders`: `id`, `tenant_id`, `customer_reference?`, `currency`, `status`,
`confirmed_at?`, `cancelled_at?`, `cancellation_reason?`, `created_at`, `updated_at`,
`version`.
`order_lines`: `id`, `tenant_id`, `order_id`, `line_number`, `sku_id`, `product_id`,
`sku_code`, `product_name`, `quantity`, `unit_price_minor`, `currency`, `created_at`.

* `ck_orders_status_valid`:
  `PENDING|BACKORDERED|INVENTORY_RESERVED|CONFIRMED|SHIPPED|DELIVERED|CANCELLED`.
  The allowed transitions are *not* a database concern — they are business rules and
  live in the aggregate (ADR-0004).
* `ck_orders_confirmed_has_timestamp`, `ck_orders_cancelled_has_reason`
  (`cancelled_at` and `cancellation_reason` both set when `CANCELLED`) — the
  evidence rule again.
* `uq_order_lines_order_id_line_number` — line numbers are unique within an order;
  the aggregate also enforces one line per SKU and at most 100 lines.
* `ck_order_lines_quantity_positive`, `ck_order_lines_unit_price_non_negative`,
  `ck_order_lines_line_number_positive`.
* `order_lines` stores a **pricing snapshot**: `sku_code`, `product_name`,
  `unit_price_minor` and `currency` are copied at submission time, and
  `product_id`/`sku_id` remain as references. Re-pricing a SKU tomorrow must not
  rewrite yesterday's order, and a later catalogue change must not break history.
  The line total is `quantity * unit_price_minor`; the order total is the sum of its
  lines (ADR-0004) — no `total_minor` column is stored, because the two could then
  disagree.
* `order_lines` has no `updated_at`: lines are written once, with the order.

### `idempotency_records` (platform)

`id`, `tenant_id`, `idempotency_key`, `operation`, `request_hash`, `state`,
`resource_type?`, `resource_id?`, `response_status?`, `created_at`, `updated_at`,
`expires_at`.

* `uq_idempotency_records_tenant_id_idempotency_key` — the whole idempotency
  guarantee in one line: a key is unique **per tenant**, so two tenants may use the
  same key without interfering, and a retry can never create a second order.
* `ck_idempotency_records_request_hash_sha256` (`length = 64`) — the fingerprint is a
  SHA-256 digest, so the same key with different content is detectable.
* `ck_idempotency_records_state_valid` (`in_progress|completed`),
  `ck_idempotency_records_key_length` (1–255),
  `ck_idempotency_records_completed_has_result` (a completed record must name the
  resource and the response status it produced).
* Claiming uses `INSERT … ON CONFLICT DO NOTHING`: the loser of a race gets zero rows
  back and knows a claim exists, without a second round trip. The record is written
  in the same transaction as the business change, so a rollback removes the claim
  too (ADR-0006).

## Constraints as the last line of defence

The application is the first line of defence — aggregates refuse invalid states
before anything is written. PostgreSQL is the last one, and the two lists overlap on
purpose:

| Invariant | Domain | Database |
| --- | --- | --- |
| available stock never negative | `InventoryItem` refuses the allocation | `ck_inventory_items_available_non_negative` |
| no overselling under concurrency | `SELECT … FOR UPDATE` + optimistic `version` | row lock + the same CHECK inside the transaction |
| one live hold per order | aggregate releases a partial hold before re-reserving | `uq_reservations_active_per_order` |
| no duplicate operation per key | use case consults the idempotency record | `uq_idempotency_records_tenant_id_idempotency_key` |
| no negative price | `Sku.change_price` / `OrderLine.create` | `ck_skus_unit_price_non_negative`, `ck_order_lines_unit_price_non_negative` |
| valid lifecycle states | transition tables in `Order`, `Product`, `Sku`, `Reservation` | `ck_*_status_valid` CHECKs |
| tenant ownership | tenant-scoped repositories | `tenant_id NOT NULL` + `RESTRICT` FKs |

A test that "fixes" a failing constraint by deleting it is not a fix; the failure is
the database doing its job.

## Indexes

Every index and the `EXPLAIN (ANALYZE, BUFFERS)` evidence behind it is documented in
[index-decisions.md](index-decisions.md), with a reproducible script
(`scripts/measure_indexes.py`). Short version: tenant-prefixed indexes for the
tenant-scoped read paths, uniqueness where uniqueness is a rule, partial uniqueness
where the rule is partial, and nothing on low-cardinality columns.

## Migrations

`migrations/versions/0001_initial_schema.py` creates all eleven tables parents-first
and drops them children-first, so a rollback never hits a foreign key violation.
Migrations in history are never edited — a schema change is a new revision (ADR-0005).

## What Phase 2+ adds (deliberately absent)

`inventory_movements` has no shipment/fulfilment movement type yet, because
fulfilment does not ship anything in Phase 1. No outbox, no event table, no search
index tables, no audit table: `audit` and `search` are placeholder boundaries, and
inventing their storage now would be designing a context that is not implemented.
