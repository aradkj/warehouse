# 3. Product and SKU are separate aggregates

* Status: Accepted
* Date: 2026-10-06
* Phase: 1

## Context

A product has a lifecycle (`DRAFT → ACTIVE → ARCHIVED`) and descriptive attributes. A
SKU has a code, a price and its own lifecycle (`ACTIVE ↔ INACTIVE`), and it is the
thing inventory holds stock for and ordering sells. The obvious modelling shortcut is
one `Product` aggregate containing its `Sku`s, which makes "a product must have at
least one SKU" a single-aggregate invariant and keeps reads simple.

## Decision

**Two aggregates, two lifecycles, two version counters.** `Product` and `Sku` are
independent roots (`src/catalog/domain/product.py`, `src/catalog/domain/sku.py`), with
`Sku` referencing its product by identifier. When a read model needs both, it loads
SKUs through `SkuRepository.list_for_products`.

Consequences that follow directly:

* Ordering snapshots `sku_id`, `product_id`, `sku_code` and `product_name` into each
  order line, because neither side of that relationship is allowed to be re-read to
  reconstruct history.
* Archive a product, and its SKUs are not silently deleted: they stop being
  purchasable because the product is not, and purchasability is decided in one place
  (`PurchasableSku` / `ProductNotPurchasable`).
* `version` is per aggregate, so deactivating a SKU does not make a concurrent product
  edit fail with a spurious conflict.

## Consequences

* No "product with children" write amplification: a price change touches one row and
  one version.
* The invariant "a product may not have SKUs that reference a different tenant" is
  enforced structurally: `skus.tenant_id` is required and equals the product's tenant
  at creation.
* Two aggregate loads on some read paths instead of one join. Phase 1 accepts that:
  reads are indexed by `(tenant_id, product_id)` and cost sub-millisecond
  ([index-decisions.md](../architecture/index-decisions.md)).

## Alternatives considered

* **`Product` as an aggregate root owning `Sku` entities** — rejected: a catalogue with
  hundreds of SKUs per product would make every price change contend on one version
  number, and inventory's hot path (SKU × warehouse) would keep loading a product it
  does not need.
* **`Sku` as a value object inside `Product`** — rejected: a SKU has identity that
  outlives the product's attribute changes (order lines and stock rows reference it),
  so it cannot be a value.
