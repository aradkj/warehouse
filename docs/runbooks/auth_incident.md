# Runbook: Authentication and Authorization Incident

## 1. Symptoms
- Prometheus alert: `AuthFailuresSpike` (`rate(http_requests_total{status=~"401|403"}[5m]) > 0.10`).
- Users reporting sudden token rejections or "Forbidden" errors when accessing endpoints.
- Log entries: `Invalid token signature`, `Token expired`, or `Permission denied for action`.

## 2. Immediate Diagnostic Commands
```bash
# 1. Inspect recent authorization denials and tenant IDs in audit logs
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT occurred_at, actor_id, action, resource_type, details->'after'->>'reason' AS reason
FROM audit_logs
WHERE details->'after'->>'decision' = 'DENY'
ORDER BY occurred_at DESC
LIMIT 20;"

# 2. Check token verification logs
docker compose logs --tail=100 arad-api | grep -E "401|403|TokenService"

# 3. Verify current system clock synchronization across containers
docker compose exec arad-api date -u
docker compose exec arad-postgres date -u
```

## 3. Investigation
1. **Clock Skew**: If container clocks drift (>30s), JWT `nbf` (not before) or `exp` (expiration) timestamps will fail validation.
2. **Secret Mismatch**: If `ARAD_AUTH_JWT_SECRET` was modified without updating active clients, all outstanding tokens become invalid.
3. **ABAC Policy Drift**: A newly applied policy or role revocation unexpectedly blocked a required service action.
4. **Credential Stuffing / Brute Force Attack**: Elevated 401 rates originating from a specific IP address or targeting a single tenant.

## 4. Safe Mitigation
- If caused by container clock skew:
  ```bash
  # Synchronize host system clock with NTP
  w32tm /resync || sudo chronyc makestep
  docker compose restart arad-api
  ```
- If caused by an over-restrictive ABAC policy:
  ```bash
  # Check active policies for the tenant
  docker compose exec arad-postgres psql -U arad -d arad -c "
  SELECT id, tenant_id, name, is_active FROM access_policies WHERE tenant_id = '<tenant_id>';"
  ```
- If an active credential stuffing attack is detected:
  - Temporarily block the offending client IP at the reverse proxy / firewall level.

## 5. Verification
```bash
# Issue a test token and verify authorization against /v1/products
curl -i http://localhost:8000/v1/products -H "Authorization: Bearer <test_token>"
```
