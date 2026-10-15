# Database Design, Index Optimization, and Lifecycle Management

## 1. Relational Design & System of Record

PostgreSQL is the single authoritative system of record for all business entities in the platform.

### Core Tables & Relationships:
- `tenants`, `users`, `roles`, `permissions`, `access_policies`: Identity & ABAC authorization.
- `products`, `skus`: Catalog hierarchy with separate aggregate tables.
- `warehouses`, `inventory_items`, `reservations`, `stock_movements`: Stock tracking and reservation management.
- `orders`, `order_lines`: Order management with immutable price snapshots.
- `fulfillments`, `fulfillment_tasks`: Allocation and carrier tracking.
- `outbox_events`, `inbox`: Transactional messaging backbone.
- `audit_logs`: Immutable security and operation audit trail.

---

## 2. Concurrency Controls

### 2.1. Optimistic Concurrency Control
Every aggregate table includes a `version: INTEGER NOT NULL DEFAULT 1` column. State updates execute:
```sql
UPDATE orders
SET status = :new_status, version = version + 1, updated_at = :now
WHERE id = :id AND tenant_id = :tenant_id AND version = :expected_version;
```
If 0 rows are affected, SQLAlchemy raises a `ConcurrencyConflict` error, preventing lost updates.

### 2.2. Row Locking under High Contention
For concurrent stock allocation, inventory items are locked row-by-row using:
```sql
SELECT id, on_hand, reserved, allocated, version
FROM inventory_items
WHERE tenant_id = :tenant_id AND warehouse_id = :warehouse_id AND sku_id = :sku_id
FOR UPDATE;
```
When multiple SKUs are reserved in a single transaction, SKU locks are acquired in sorted order of `(sku_id, warehouse_id)` to eliminate database deadlocks.

---

## 3. Index Strategy and Query Plan Analysis

Indexes are established based on query access patterns and verified using `EXPLAIN ANALYZE`:

### 3.1. Outbox Relay Claim Index
```sql
CREATE INDEX idx_outbox_unprocessed 
ON outbox_events (created_at ASC) 
WHERE status = 'PENDING';
```
- **Rationale:** The outbox relay worker repeatedly queries `WHERE status = 'PENDING' ORDER BY created_at ASC LIMIT 100 FOR UPDATE SKIP LOCKED`.
- **Query Plan:**
  ```text
  Limit (cost=0.15..8.20 rows=100 width=312)
    -> LockRows (cost=0.15..8.20 rows=100 width=312)
      -> Index Scan using idx_outbox_unprocessed on outbox_events (cost=0.15..8.20 rows=100 width=312)
  ```
  This partial index keeps the index size tiny since published records (`status = 'PUBLISHED'`) are excluded.

### 3.2. Expired Reservation Scanning Index
```sql
CREATE INDEX idx_reservations_expiration 
ON reservations (expires_at ASC) 
WHERE status = 'ACTIVE';
```
- **Rationale:** Celery periodic task runs `SELECT id FROM reservations WHERE status = 'ACTIVE' AND expires_at <= :now`.
- **Query Plan:**
  ```text
  Index Scan using idx_reservations_expiration on reservations
    Index Cond: (expires_at <= '2026-10-14 19:30:00+00'::timestamptz)
  ```

### 3.3. Tenant Order History Cursor Pagination
```sql
CREATE INDEX idx_orders_tenant_created 
ON orders (tenant_id, created_at DESC, id DESC);
```
- **Rationale:** API cursor pagination requests `WHERE tenant_id = :tenant_id AND (created_at, id) < (:cursor_created_at, :cursor_id) ORDER BY created_at DESC, id DESC LIMIT 50`.
- **Query Plan:** Backward index scan completely avoids memory sorts (`Sort Method: none used`).

---

## 4. Outbox and Audit Data Lifecycle: Time-Based Partitioning & Retention

High-volume tables (`outbox_events` and `audit_logs`) generate millions of rows in production.

### 4.1. Monthly Range Partitioning Architecture
In high-throughput environments, PostgreSQL declarative partitioning by range (`PARTITION BY RANGE (created_at)`) isolates active and historical data:

```sql
CREATE TABLE outbox_events (
    id UUID NOT NULL,
    tenant_id UUID NOT NULL,
    event_id UUID NOT NULL,
    event_type VARCHAR(128) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- Monthly partition examples:
CREATE TABLE outbox_events_2026_10 PARTITION OF outbox_events
    FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00');
```

### 4.2. Retention Policy
- **Outbox Published Records:** Retained for 14 days in hot storage. After 14 days, published records are safely dropped via `DROP TABLE outbox_events_YYYY_MM` or partitioned truncation, with zero vacuum contention on active transactions.
- **Audit Logs:** Retained in PostgreSQL for 90 days for operational investigation. After 90 days, partitions are exported to immutable cold storage (e.g. Parquet in S3 / GCS) before partition detachment.

---

## 5. Read-Replica-Ready Query Separation

To support read-heavy scaling without coupling the domain model to replication mechanics:
1. **Separation of Concerns:** Write use cases require `UnitOfWork` (which executes against the primary read-write connection pool).
2. **Read-Only Queries:** Read/search use cases can accept a read-only repository or session (`readonly_session`).
3. **Connection Routing:** The application's `get_db_session` dependency can be configured via environment variables (`ARAD_DATABASE_REPLICA_URL`) to route query-only calls to read replicas while all transactional mutations route strictly to the primary.
