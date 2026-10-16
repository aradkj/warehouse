# Runbook: Inventory Inconsistency and Suspected Overselling

## 1. Symptoms
- Prometheus alert: `InventoryInvariantViolation` or `NegativeAvailableStockDetected`.
- Customer complaint: Order confirmed for an item that is physically unavailable.
- Database audit record indicates `available_quantity < 0` or total reserved + available != total on hand.

## 2. Immediate Diagnostic Commands
```bash
# 1. Check for any stock records where available_quantity is negative
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT id, tenant_id, sku_id, warehouse_id, on_hand, reserved, (on_hand - reserved) AS computed_available
FROM stock_records
WHERE (on_hand - reserved) < 0;"

# 2. Check for double active reservations on the same order or line
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT order_id, sku_id, count(*) AS active_reservation_count
FROM reservations
WHERE status = 'ACTIVE'
GROUP BY order_id, sku_id
HAVING count(*) > 1;"

# 3. Inspect audit trail for the affected SKU
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT id, occurred_at, actor_id, action, details
FROM audit_logs
WHERE resource_id = '<sku_id>'
ORDER BY occurred_at DESC
LIMIT 20;"
```

## 3. Investigation
1. **Concurrency Guarantee Verification**:
   - The platform strictly prevents negative available stock at the PostgreSQL transactional level using row-level locking (`SELECT ... FOR UPDATE`) and database check constraints: `available >= 0`.
2. **Physical Discrepancy vs Logical Discrepancy**:
   - Determine if the issue is a logical calculation error (defect in reservation logic) or an external warehouse inventory shrinkage (damaged or lost physical stock).
3. **Audit Chain Inspection**:
   - Trace all stock adjustments (`inventory/adjustments`), reservation creations, and cancellations for the affected SKU.

## 4. Safe Mitigation
> [!CAUTION]
> NEVER execute arbitrary `UPDATE stock_records SET on_hand = ...;` statements directly in SQL. All stock changes MUST occur through audited domain adjustments to preserve event provenance.

- If physical shrinkage occurred, submit an audited stock reconciliation adjustment:
  ```bash
  curl -X POST "http://localhost:8000/v1/inventory/adjustments" \
    -H "X-Tenant-Id: <tenant_id>" \
    -H "Content-Type: application/json" \
    -d '{
      "sku_id": "<sku_id>",
      "warehouse_id": "<warehouse_id>",
      "quantity_delta": -5,
      "reason": "cycle_count_discrepancy"
    }'
  ```
- If an order was confirmed against backorder policy:
  - Check fulfillment status in `order_fulfillments` and initiate standard customer notification/compensation workflow.

## 5. Verification
```bash
# Verify all stock invariants hold true across the tenant
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT count(*) FROM stock_records WHERE on_hand < 0 OR reserved < 0 OR (on_hand - reserved) < 0;"
```
