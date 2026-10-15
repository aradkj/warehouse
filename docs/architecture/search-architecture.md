# Search and Projection Architecture

## 1. Architectural Role of Elasticsearch

In the Warehouse platform:
- **PostgreSQL is the Authoritative System of Record:** All transactional decisions (order placement, inventory reservation, fulfillment transitions) are committed to PostgreSQL.
- **Elasticsearch is a Disposable Projection:** Elasticsearch indexes are read-optimized projections built asynchronously from domain events. If Elasticsearch is wiped or lost, the entire index can be reconstructed from PostgreSQL without data loss.

---

## 2. Supported Projections & Indexes

The search subsystem manages four distinct versioned indices with atomic aliases:

| Index Name (Versioned) | Read / Write Alias | Managed Aggregate / Source | Searchable Fields |
|---|---|---|---|
| `arad_products_v1` | `products` | Catalog (`Product`, `Sku`) | `slug`, `name`, `description`, `status`, `sku_code`, dynamic `attributes` |
| `arad_orders_v1` | `orders` | Ordering (`Order`) | `customer_reference`, `order_id`, `status`, `line_count`, `currency`, `created_at` |
| `arad_fulfillment_exceptions_v1` | `fulfillment_exceptions` | Fulfillment (`Fulfillment`) | `fulfillment_id`, `order_id`, `warehouse_id`, `carrier`, `reason`, `is_permanent` |
| `arad_audit_v1` | `audit` | Identity & Audit (`AuditLog`) | `actor_id`, `action`, `resource_type`, `resource_id`, `decision`, `decision_reason` |

---

## 3. Mandatory Tenant Isolation in Search

Every search query generated against Elasticsearch **must** contain an explicit filter clause:

```json
{
  "query": {
    "bool": {
      "filter": [
        { "term": { "tenant_id": "018f2d50-7f2e-7cb3-9fa1-cfd128394823" } }
      ],
      "must": [ ... ]
    }
  }
}
```

This filter is enforced at the port/adapter layer in `src/search/adapters/elasticsearch.py` and cannot be bypassed by client query inputs.

---

## 4. Zero-Downtime Indexing with Aliases

When index schema mappings evolve or full reindexing is executed:
1. A new versioned index is created: e.g. `arad_orders_v2`.
2. Replay workers index documents from PostgreSQL into `arad_orders_v2`.
3. An atomic alias swap is executed via Elasticsearch API:
   ```json
   {
     "actions": [
       { "remove": { "index": "arad_orders_v1", "alias": "orders" } },
       { "add": { "index": "arad_orders_v2", "alias": "orders" } }
     ]
   }
   ```
4. Read queries targeting the alias switch seamlessly with zero downtime.

---

## 5. Eventual Consistency, Replay, and Reconciliation

- **Projection Latency:** Projections typically update within 1-2 seconds after outbox relay dispatch.
- **Projector Idempotency:** Event processing in `ProductSearchProjector` checks document versions (`if event.version < doc.version: return`) or uses deterministic document IDs:
  - Product document ID: `{tenant_id}:{product_id}`
  - Order document ID: `{tenant_id}:{order_id}`
  - Fulfillment Exception document ID: `{tenant_id}:{fulfillment_id}:{timestamp}`
  - Audit document ID: `{tenant_id}:{audit_id}`
- **Scheduled Reconciliation:** A periodic Celery reconciliation job compares aggregate count hashes between PostgreSQL and Elasticsearch, flagging any discrepancies and emitting sync events.
