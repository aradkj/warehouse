# Runbook: API Outage and Readiness Failure

## 1. Symptoms
- Prometheus alert: `HighErrorRate5xx` (>1% 5xx errors for >2m) or `ServiceDown` (`up{job="arad-api"} == 0`).
- Container restart loops (`docker ps` shows frequent restarts or status `unhealthy`).
- External load balancers report `HTTP 502 Bad Gateway` or `HTTP 503 Service Unavailable`.
- HTTP probes to `/readyz` fail with `503 Service Unavailable` or timeout.

## 2. Immediate Diagnostic Commands
```bash
# 1. Inspect container lifecycle and status
docker compose ps arad-api

# 2. View recent error logs
docker compose logs --tail=100 -f arad-api | grep -E "ERROR|CRITICAL|Traceback"

# 3. Test liveness and readiness manually
curl -i http://localhost:8000/healthz
curl -i http://localhost:8000/readyz

# 4. Check PostgreSQL connectivity and migrations
docker compose exec arad-postgres pg_isready -U arad
docker compose run --rm test alembic current
```

## 3. Investigation
1. **Liveness vs Readiness**:
   - If `/healthz` fails: The Python process is hung (event loop blocked) or dead.
   - If `/healthz` succeeds (200) but `/readyz` fails (503): Essential dependencies (PostgreSQL connectivity or pending Alembic migration) are unfulfilled.
2. **Database Connectivity**:
   - Inspect if PostgreSQL connection pool is saturated: `pg_stat_activity`.
   - Check if database credentials in `ARAD_DATABASE_URL` are valid.
3. **Migration Drift**:
   - If `/readyz` returns `{"status": "degraded", "alembic_version": "pending"}`: Schema migrations were not applied before API container startup.

## 4. Safe Mitigation
- If blocked by pending migrations:
  ```bash
  docker compose run --rm test alembic upgrade head
  ```
- If process is deadlocked or non-responsive:
  ```bash
  docker compose restart arad-api
  ```
- If downstream database is temporarily unreachable, allow bounded retry intervals. Do not hammer the database with rapid crash-loops.

## 5. Verification
```bash
# Ensure both endpoints return 200 OK
curl -f http://localhost:8000/healthz || exit 1
curl -f http://localhost:8000/readyz || exit 1
```

## 6. Rollback
If a newly deployed image introduced a regression:
```bash
# Revert to previous image release
docker compose up -d --no-deps arad-api
```

## 7. Escalation & Warnings
> [!CAUTION]
> Never disable readiness checks or remove database checks from `/readyz` to bypass routing errors. Routing traffic to an unmigrated or disconnected node will corrupt client requests and produce false 404s.
