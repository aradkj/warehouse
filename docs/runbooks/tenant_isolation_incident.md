# Runbook: Multi-Tenant Isolation Incident

## 1. Symptoms
- Prometheus alert: `CrossTenantAccessAttempt` or audit log alert `TENANT_MISMATCH`.
- Customer reports seeing orders, SKUs, or warehouse data belonging to another organization.
- Audit logs report `tenant_id` mismatch between authenticated token/header and target aggregate owner.

## 2. Immediate Diagnostic Commands
```bash
# 1. Search audit logs for any cross-tenant rejection events
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT occurred_at, actor_id, action, resource_id, details
FROM audit_logs
WHERE details->'after'->>'reason' ILIKE '%tenant%'
ORDER BY occurred_at DESC
LIMIT 20;"

# 2. Verify PostgreSQL Row-Level Security (RLS) enforcement status
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT relname, relrowsecurity, relforcerowsecurity 
FROM pg_class 
WHERE relname IN ('orders', 'products', 'stock_records', 'audit_logs', 'roles');"

# 3. Check for any rows with missing or NULL tenant_id
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT count(*) FROM orders WHERE tenant_id IS NULL;
SELECT count(*) FROM products WHERE tenant_id IS NULL;
SELECT count(*) FROM stock_records WHERE tenant_id IS NULL;"
```

## 3. Investigation
1. **Multi-Layer Defense Review**:
   - The platform enforces isolation at four distinct architectural layers:
     1. HTTP Middleware / Dependency validation (`RequestContext.require_tenant()`).
     2. Application Service / Domain aggregate boundaries.
     3. PostgreSQL Row-Level Security (`FORCE ROW LEVEL SECURITY`).
     4. Cache key prefixing (`tenant:<id>:...`) and Elasticsearch routing filters.
2. **RLS Configuration Audit**:
   - Verify that `relforcerowsecurity` is `true` on all tenant-owned tables. Table owners otherwise bypass RLS unless `FORCE` is enabled.
3. **Cache Leakage Check**:
   - Verify that cache keys always include the tenant ID prefix. Never allow unqualified keys like `product:<id>`.

## 4. Safe Mitigation
- If RLS was disabled on a table:
  ```sql
  ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
  ALTER TABLE orders FORCE ROW LEVEL SECURITY;
  ```
- If an invalid session or cache key was leaked:
  ```bash
  # Evict only the affected tenant's cache keys
  docker compose exec arad-redis redis-cli --scan --pattern "tenant:<tenant_id>:*" | xargs -r docker compose exec -T arad-redis redis-cli del
  ```

## 5. Verification
```bash
# Run automated multi-tenant isolation test suite
docker compose run --rm test pytest tests/integration/test_tenant_isolation.py
```

## 6. Escalation & Warnings
> [!CAUTION]
> A multi-tenant data leak is a P1 Security Incident. Immediately revoke affected session tokens, notify the Data Protection Officer / Security Lead, and preserve database audit logs for forensic reporting.
