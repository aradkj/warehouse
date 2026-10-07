# Index decisions (measured)

Phase 1 does not create indexes "just in case". Every index below exists because a
query the API actually runs needs it, and the decision was checked with
`EXPLAIN (ANALYZE, BUFFERS)` rather than by intuition. This document records the
query, the plan and the numbers so the next person can re-measure instead of
guessing.

Reproduce everything with:

```bash
python scripts/measure_indexes.py --rows 50000      # creates a scratch DB, drops it again
```

The script migrates a throwaway database with `alembic upgrade head`, seeds 50 000
rows per owning table (50 000 SKUs, one `inventory_items` row per SKU — the table is
unique on `(tenant_id, warehouse_id, sku_id)`, exactly as in production — 50 000
orders with a line and 43 750 reservations), runs `ANALYZE`, and prints the plan and
`Execution Time` of each hot query.

## Measured plans (PostgreSQL 17, 50 000 rows/table, warm cache)

| Query | Plan | Time |
| --- | --- | --- |
| availability for one SKU, newest first | `Index Scan` + top-N `Sort` on `ix_inventory_items_tenant_id_sku_id` | 0.078 ms |
| availability for one warehouse | `Index Scan` on `ix_inventory_items_tenant_id_created_at_id` | 0.093 ms |
| newest-first order page | `Index Scan` on `ix_orders_tenant_id_created_at_id` | ~0.02 ms |
| order page filtered by status | `Index Scan` on `ix_orders_tenant_id_status_created_at_id` | 0.088 ms |
| movements of one item, newest first | `Index Scan` on `ix_inventory_movements_tenant_id_inventory_item_id_occurred_at` | 0.032 ms |
| reservation lookup for an order | `Index Scan` on `ix_reservations_tenant_id_order_id` | 0.020 ms |
| idempotency claim lookup (every mutation) | `Index Scan` on `uq_idempotency_records_tenant_id_idempotency_key` | 0.031 ms |

No query in the table degrades into a sequential scan, and no planner is forced:
the plans above are what PostgreSQL chose on its own.

## Why each index exists

**Every tenant-scoped index is prefixed by `tenant_id`.** Multi-tenancy is the
primary access pattern — a request can only ever read its own tenant's rows — so the
leading column makes the index usable for the tenant-filtered query and useless for
nothing else. A tenant prefix also keeps the index selective as the platform grows:
the table grows by adding tenants, not by fattening one tenant's slice.

| Index | Serves |
| --- | --- |
| `ix_inventory_items_tenant_id_sku_id` | availability for one SKU (the endpoint's main filter) |
| `ix_inventory_items_tenant_id_warehouse_id` | availability for one warehouse, and the per-warehouse stock report |
| `ix_inventory_items_tenant_id_created_at_id` | keyset pagination for stock listings |
| `uq_inventory_items_tenant_id_warehouse_id_sku_id` | the aggregate's own rule: one stock row per SKU per warehouse. Also the arbiter that makes `INSERT … ON CONFLICT DO NOTHING` safe when two requests race to create the same stock row |
| `ix_inventory_movements_tenant_id_inventory_item_id_occurred_at` | the stock ledger of one item, newest first |
| `ix_inventory_movements_tenant_id_reference_type_reference_id` | "which movements did reservation R / order O cause?" — the audit question |
| `ix_reservations_tenant_id_order_id` | order confirmation and cancellation look up the order's hold |
| `uq_reservations_active_per_order` (partial, `WHERE status = 'ACTIVE'`) | at most one live hold per order, enforced by PostgreSQL, while released/expired/confirmed history stays |
| `ix_reservations_status_expires_at` | the Phase 2 expiry sweep scans ACTIVE rows by expiry — the index is here now so the sweep is a Phase 2 change of job, not of schema |
| `ix_orders_tenant_id_created_at_id`, `ix_orders_tenant_id_status_created_at_id` | the order list, unfiltered and status-filtered |
| `ix_order_lines_tenant_id_order_id` | loading an order with its lines (always `WHERE tenant_id = … AND order_id = …`) |
| `ix_order_lines_tenant_id_sku_id` | "which orders contain this SKU?" — an operational question Phase 2 fulfilment needs |
| `ix_products_tenant_id_created_at_id`, `ix_products_tenant_id_status_created_at_id` | the catalogue list, unfiltered and status-filtered |
| `ix_skus_tenant_id_product_id` | loading a product's SKUs (`SkuRepository.list_for_products`) |
| `ix_warehouses_tenant_id_created_at_id` | the warehouse list |
| `ix_idempotency_records_expires_at` | retention cleanup (Phase 2 job) |

## Uniqueness constraints are not indexes "for speed"

`uq_tenants_slug`, `uq_products_tenant_id_slug`, `uq_skus_tenant_id_code` and
`uq_warehouses_tenant_id_code` are correctness: two products in one tenant may not
share a slug even if two requests race. They happen to be indexed, which is how
PostgreSQL implements uniqueness.

## What was deliberately *not* indexed

* **`inventory_movements` has no index on `movement_type`.** The column has five
  values, and a low-cardinality standalone index is exactly the "blind index" that
  costs writes and returns nothing; queries filter by tenant, item or reference.
* **No index on `reservations.order_id` alone / `orders.status` alone.** Both are
  covered as the second column of a tenant-prefixed index, and neither is ever
  queried without a tenant.
* **No covering indexes yet.** The seeded plans return in well under a millisecond
  with `shared hit` counts in the single or low double digits; adding INCLUDE
  columns now would trade write amplification for a gain no measurement shows.
* **No index on `orders.customer_reference`.** Phase 1 never queries by it; it is a
  human-facing field for the caller.
* **No partial index on `inventory_items.reserved > 0`.** The availability query
  reads full rows and filters on SKU/warehouse; there is no "find all reserved
  stock" workload in Phase 1.

## One honest caveat

`ix_inventory_items_tenant_id_warehouse_id` is not the index the planner chose for
the warehouse-filtered query in the measurement above: with `tenant_id` alone
already narrowing 50 000 rows to 43 750 and a `LIMIT 50`, it scanned
`ix_inventory_items_tenant_id_created_at_id` and filtered. That is fine — the query
is 0.09 ms — but it is recorded here rather than dressed up as a win. The index
earns its keep once a warehouse holds a small fraction of a large tenant's stock,
which is the normal shape of the data, and in the reservation path, which looks
stock up per warehouse.

## Reservation path

Reservations do not add an index of their own: `ReservationRepository` reads and
writes rows by primary key, and the allocation rows by
`ix_reservation_allocations_tenant_id_reservation_id`. The row lock itself
(`SELECT … FOR UPDATE`) is served by the primary key — see
[ADR-0007](../decisions/0007-inventory-allocation-and-concurrency.md).
