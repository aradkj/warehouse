# Runbook: Reservation Expiration Backlog

## 1. Symptoms
- Prometheus alert: `ReservationExpirationLag` (`expired_unreleased_reservations_count > 100`).
- Customers report items showing "Out of Stock" even though pending cart reservations have timed out (>15 minutes old).
- Database contains active reservations with `expires_at < NOW()`.

## 2. Immediate Diagnostic Commands
```bash
# 1. Count unreleased expired reservations
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT count(*) AS expired_unreleased_count,
       min(expires_at) AS oldest_expired,
       now() - min(expires_at) AS max_lag
FROM reservations 
WHERE status = 'ACTIVE' AND expires_at < now();"

# 2. Inspect Celery beat scheduler status
docker compose ps arad-scheduler
docker compose logs --tail=100 arad-scheduler

# 3. Check worker execution logs for expire_reservations task
docker compose logs --tail=100 arad-worker | grep "expire_reservations"
```

## 3. Investigation
1. **Scheduler Failure**: The Celery beat scheduler process `arad-scheduler` crashed, so periodic expiration sweeps are not being enqueued.
2. **Lock Contention**: The expiration use case locks SKU stock records to release quantity back to available inventory. If high-concurrency order creation is holding SKU locks, the expiration job times out.
3. **Queue Stoppage**: Tasks enqueued to the `celery` queue are backed up behind heavy batch tasks.

## 4. Safe Mitigation
- If scheduler stopped:
  ```bash
  docker compose restart arad-scheduler
  ```
- Manually trigger an immediate reservation expiration sweep via CLI:
  ```bash
  docker compose run --rm test python -c "
  import asyncio
  from bootstrap.workers import _run_expire_reservations
  released = asyncio.run(_run_expire_reservations())
  print(f'Successfully released {released} expired reservations')
  "
  ```

## 5. Verification
```bash
# Confirm expired unreleased count dropped to 0
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT count(*) FROM reservations WHERE status = 'ACTIVE' AND expires_at < now();"
```

## 6. Escalation & Warnings
> [!IMPORTANT]
> The expiration use case is idempotent and guarded against double release: repeated executions never release the same reservation twice. Do not manually update `reservations.status = 'EXPIRED'` with direct SQL; doing so skips the inventory quantity release!
