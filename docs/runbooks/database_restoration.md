# Runbook: Database Restoration and Disaster Recovery

## 1. Scenario & Objectives
- Target RTO (Recovery Time Objective): **< 30 minutes**.
- Target RPO (Recovery Point Objective): **< 15 minutes**.
- Mandatory Requirement: Always restore into an isolated verification database first before performing production cutover. Never overwrite active production without verification.

## 2. Immediate Diagnostic & Pre-requisites
```bash
# 1. Locate the latest validated backup snapshot and its checksum
ls -lht /app/backups/arad_backup_*.sql.gz | head -n 1

# 2. Verify archive SHA-256 integrity BEFORE attempting restore
sha256sum -c /app/backups/arad_backup_YYYYMMDD_HHMMSS.sql.gz.sha256

# 3. Check target database connection availability
pg_isready -h localhost -p 5432 -U arad
```

## 3. Execution Procedure (Isolated Restoration)
```bash
# Execute automated restore script targeting an isolated disaster recovery database
python scripts/restore_postgres.py /app/backups/arad_backup_YYYYMMDD_HHMMSS.sql \
  --database arad_dr_restore \
  --expected-revision 0009_harden_rls_and_roles
```

The restoration script automatically:
1. Validates that the target database name is a recognized non-production isolated target (`_test`, `_restore`, `_dr`, `_stage`, `_dev`).
2. Validates SQL identifiers to prevent any injection vulnerability.
3. Enforces SHA-256 checksum integrity verification against sidecar `.sha256` file (fails closed if missing or mismatched).
4. Creates the isolated target database `arad_dr_restore`.
5. Streams SQL statements via `psql` (or Docker container `arad-postgres-1`).
6. Executes post-restore data integrity validations:
   - Validates Alembic migration version matches expected revision (`0009_harden_rls_and_roles`).
   - Verifies zero negative stock records (`on_hand >= 0`, `reserved >= 0`).
   - Verifies tenant isolation and foreign key referential integrity.
   - Verifies order counts and outbox consistency.

## 4. Production Cutover (If Primary Database Was Destroyed)
> [!CAUTION]
> Production cutover terminates existing connections and replaces the production database.
> The restore utility enforces a strict fail-closed safety policy: restoring over a non-isolated or production database (`arad`, `production`, `prod`, `warehouse`) requires BOTH `--confirm-destructive-restore` AND the environment variable `CONFIRM_PRODUCTION_RESTORE=true`.

```bash
# 1. Stop all write traffic (scale down API and workers)
docker compose stop api worker relay search-consumer

# 2. Execute restore targeting primary 'arad' database with mandatory fail-closed confirmations
CONFIRM_PRODUCTION_RESTORE=true python scripts/restore_postgres.py /app/backups/arad_backup_YYYYMMDD_HHMMSS.sql \
  --database arad \
  --confirm-destructive-restore \
  --expected-revision 0009_harden_rls_and_roles

# 3. Restart application services
docker compose up -d api worker relay search-consumer

# 4. Reconcile search projections against restored PostgreSQL state
curl -X POST "http://localhost:8000/v1/search/reindex" -H "X-Admin-Key: <admin_key>"
```

## 5. Post-Restore Verification
```bash
# Check API healthz and readyz
curl -f http://localhost:8000/healthz
curl -f http://localhost:8000/readyz

# Run test query to confirm read access
curl -i http://localhost:8000/v1/products -H "X-Tenant-Id: <tenant_id>"
```
