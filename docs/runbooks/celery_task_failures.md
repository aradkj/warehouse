# Runbook: Repeated Celery Task Failures

## 1. Symptoms
- Prometheus alert: `CeleryTaskFailureSpike` (`rate(celery_task_failures_total[5m]) > 0.05`).
- Celery worker log output contains repeated backtraces, `TaskRevokedError`, `TimeLimitExceeded`, or `WorkerLostError`.
- Asynchronous jobs (reindexing, email dispatch, reservation sweep) fail to complete.

## 2. Immediate Diagnostic Commands
```bash
# 1. Inspect Celery worker status and active tasks
docker compose exec arad-worker celery -A bootstrap.celery_app inspect active

# 2. Inspect registered and reserved tasks
docker compose exec arad-worker celery -A bootstrap.celery_app inspect reserved

# 3. Check worker stats and error counters
docker compose exec arad-worker celery -A bootstrap.celery_app inspect stats

# 4. View worker logs
docker compose logs --tail=100 arad-worker
```

## 3. Investigation
1. **OOM Kills**: Check if the worker was killed by the Linux kernel OOM killer (`dmesg -T | grep -i oom` or container exit code 137). Heavy reindexing tasks can cause excessive memory growth.
2. **Database Timeouts**: Tasks executing long-running transactional operations exceeding `statement_timeout_ms`.
3. **Deadlocks**: Competing tasks acquiring row-level locks in conflicting order.

## 4. Safe Mitigation
- If worker crashed due to memory exhaustion:
  ```bash
  # Restart worker and verify memory limits
  docker compose restart arad-worker
  ```
- If a stuck task is blocking a worker thread:
  ```bash
  # Revoke a specific task by task_id with termination
  docker compose exec arad-worker celery -A bootstrap.celery_app control revoke <task_id> --terminate
  ```
- Adjust worker concurrency in `.env.production` if resource limits are exceeded:
  ```env
  CELERY_CONCURRENCY=4
  ```

## 5. Verification
```bash
# Ensure active tasks return to normal operating baseline
docker compose exec arad-worker celery -A bootstrap.celery_app inspect active
```
