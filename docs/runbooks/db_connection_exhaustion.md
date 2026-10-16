# Runbook: Database Connection Pool Exhaustion

## 1. Symptoms
- Prometheus alert: `PostgresConnectionPoolExhaustion` (`database_connections_in_use / database_pool_size >= 0.90`).
- FastAPI logs: `sqlalchemy.exc.TimeoutError: QueuePool limit of size X overflow Y reached, connection timed out, timeout 30.00`.
- API endpoints hanging or timing out with HTTP 500/504.
- High database connection wait duration in metrics.

## 2. Immediate Diagnostic Commands
```bash
# 1. Inspect PostgreSQL connection count and active queries
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT count(*) AS total_conns, state, wait_event_type, wait_event 
FROM pg_stat_activity 
WHERE datname = 'arad' 
GROUP BY state, wait_event_type, wait_event;"

# 2. Identify long-running transactions (>30 seconds)
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT pid, usename, client_addr, state, now() - xact_start AS xact_age, query 
FROM pg_stat_activity 
WHERE state != 'idle' AND xact_start IS NOT NULL AND now() - xact_start > interval '30 seconds'
ORDER BY xact_age DESC;"

# 3. Check for lock contention
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT blocked_locks.pid AS blocked_pid,
       blocking_locks.pid AS blocking_pid,
       blocked_activity.query AS blocked_statement,
       blocking_activity.query AS current_statement_in_blocking_process
FROM  pg_catalog.pg_locks blocked_locks
JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
JOIN pg_catalog.pg_locks blocking_locks 
    ON blocking_locks.locktype = blocked_locks.locktype
   AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
   AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
   AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
   AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
   AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
   AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
   AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
   AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
   AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
   AND blocking_locks.pid != blocked_locks.pid
JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
WHERE NOT blocked_locks.granted;"
```

## 3. Investigation
1. **Connection Leaks**: Verify if async sessions are properly closed in use cases. Check `src/shared/infrastructure/db/session.py`.
2. **Slow Queries**: Unindexed full table scans holding transactions open during heavy traffic.
3. **Pessimistic Locking**: `SELECT ... FOR UPDATE` contention on high-volume SKUs during flash reservation sales.
4. **Pool Configuration**: `ARAD_DB_POOL_SIZE` and `ARAD_DB_MAX_OVERFLOW` settings in `.env.production`.

## 4. Safe Mitigation
- Cancel a specific blocking query:
  ```sql
  SELECT pg_cancel_backend(<pid>);
  ```
- Terminate an abandoned idle-in-transaction connection:
  ```sql
  SELECT pg_terminate_backend(<pid>);
  ```
- Increase temporary pool overflow headroom in production configuration:
  ```env
  ARAD_DB_MAX_OVERFLOW=20
  ARAD_DB_POOL_TIMEOUT=15.0
  ```
- Gracefully restart leaking worker services:
  ```bash
  docker compose restart arad-worker
  ```

## 5. Verification
```bash
# Verify active connections dropped below threshold
docker compose exec arad-postgres psql -U arad -d arad -c "SELECT count(*) FROM pg_stat_activity WHERE datname='arad' AND state='active';"
curl -s http://localhost:8000/metrics | grep database_connections_in_use
```

## 6. Escalation & Warnings
> [!WARNING]
> Never run `SELECT pg_terminate_backend(pid)` on PostgreSQL system processes (`usename='postgres'`, background writer, walwriter). Terminate only application worker queries after verifying their transaction age.
