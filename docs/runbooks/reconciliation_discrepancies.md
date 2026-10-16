# Runbook: PostgreSQL/Elasticsearch Reconciliation Discrepancies

## 1. Symptoms
- Prometheus alert: `SearchProjectionDiscrepancy` (`search_projection_missing_documents > 0`).
- Users report an order or product exists in direct detail view (`GET /v1/products/{id}` or `GET /v1/orders/{id}`) but does not appear in search queries (`GET /v1/search/products`).
- Scheduled reconciliation sweep reports repaired records.

## 2. Immediate Diagnostic Commands
```bash
# 1. Compare total record counts in PostgreSQL vs Elasticsearch index
docker compose exec arad-postgres psql -U arad -d arad -c "SELECT count(*) FROM products;"
docker compose exec arad-elasticsearch curl -s "http://localhost:9200/products/_count" | jq .count

# 2. Check for missing orders in projection
docker compose exec arad-postgres psql -U arad -d arad -c "SELECT count(*) FROM orders;"
docker compose exec arad-elasticsearch curl -s "http://localhost:9200/orders/_count" | jq .count

# 3. Trigger tenant-scoped dry-run reconciliation
curl -X POST "http://localhost:8000/v1/search/reconcile" \
  -H "X-Tenant-Id: <tenant_id>" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true, "entity_type": "products"}'
```

## 3. Investigation
1. **Network Drop During Publication**: An outbox message was dropped before reaching the broker, or a search worker encountered an unhandled exception before indexing.
2. **Document Version Conflict**: Stale outbox messages processed out of order resulting in Elasticsearch version conflicts (`version_conflict_engine_exception`).
3. **Soft-Deleted Entities**: Entities deleted in PostgreSQL whose tombstone events failed to propagate to Elasticsearch.

## 4. Safe Mitigation
- Execute tenant-specific projection auto-repair:
  ```bash
  # Execute live reconciliation repair
  curl -X POST "http://localhost:8000/v1/search/reconcile" \
    -H "X-Tenant-Id: <tenant_id>" \
    -H "Content-Type: application/json" \
    -d '{"dry_run": false, "entity_type": "products"}'
  ```
- Or run the platform reconciliation utility script:
  ```bash
  docker compose run --rm test python -c "
  import asyncio
  from bootstrap.dependencies import build_use_cases
  # Runs reconciliation service directly against PostgreSQL authoritative state
  "
  ```

## 5. Verification
```bash
# Re-run dry-run reconciliation and assert repaired_count == 0
curl -X POST "http://localhost:8000/v1/search/reconcile" \
  -H "X-Tenant-Id: <tenant_id>" \
  -H "Content-Type: application/json" \
  -d '{"dry_run": true, "entity_type": "products"}'
```
