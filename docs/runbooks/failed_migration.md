# Runbook: Failed Database Migration

## 1. Symptoms
- API container fails startup readiness check (`GET /readyz` returns 503 Service Unavailable).
- Deployment pipeline halts on `alembic upgrade head` with a non-zero exit code.
- PostgreSQL errors in migration log: `relation already exists`, `column cannot be dropped`, `deadlock detected`, or `check constraint violation`.

## 2. Immediate Diagnostic Commands
```bash
# 1. Determine current database migration head vs repository revisions
docker compose run --rm test alembic current
docker compose run --rm test alembic history --verbose

# 2. Check Alembic version table directly in PostgreSQL
docker compose exec arad-postgres psql -U arad -d arad -c "SELECT version_num FROM alembic_version;"

# 3. Inspect last applied statements and active transaction state
docker compose exec arad-postgres psql -U arad -d arad -c "
SELECT pid, query, state, age(clock_timestamp(), query_start) 
FROM pg_stat_activity 
WHERE query ILIKE '%alembic%' OR query ILIKE '%alter table%';"
```

## 3. Investigation
1. **Transactional DDL Status**: PostgreSQL supports transactional DDL. If Alembic encountered a failure during a standard DDL migration, the transaction rolled back cleanly.
2. **Post-Commit Lock Contention**: A migration requiring `ACCESS EXCLUSIVE` lock on heavily queried tables (`orders`, `stock_records`) was blocked by long-running reads.
3. **Data Drift & Pre-existing Invariants**: A migration adding a `NOT NULL` column or `CHECK` constraint failed because existing rows violate the new domain invariant.

## 4. Safe Mitigation
- If blocked by lock contention, identify blocking PIDs and cancel them:
  ```sql
  SELECT pg_cancel_backend(blocking_pid) FROM ...;
  ```
- If the migration is partially applied due to manual commands or external scripts:
  ```bash
  # Check schema state without altering rows
  docker compose run --rm test alembic upgrade head --sql
  ```
- If a revision file was authored with an invalid constraint:
  1. Fix the Python migration script in `migrations/versions/`.
  2. Test against a disposable database:
     ```bash
     docker compose run --rm test pytest tests/integration/test_migrations.py
     ```
  3. Re-run `alembic upgrade head`.

## 5. Rollback Procedure
> [!CAUTION]
> - **Security Boundary (Revision `0009_harden_rls_and_roles`)**: Revision 0009 is an irreversible security boundary. Running `alembic downgrade` will be rejected with `RuntimeError` because reverting it would reintroduce the deprecated `app.bypass_rls` backdoor.
> - If an application rollback is required, redeploy the previous application container images without downgrading the database (schema remains backward-compatible).
> - If database state recovery is strictly necessary, execute restore from pre-deploy backup rather than running downgrade:
```bash
docker compose run --rm test python scripts/restore_postgres.py --backup-file /app/backups/pre_deploy_snapshot.sql.gz --target-db arad
```

## 6. Verification
```bash
# 1. Verify Alembic reports head
docker compose run --rm test alembic current

# 2. Check API readiness probe
curl -f http://localhost:8000/readyz
```
