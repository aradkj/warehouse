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
python scripts/restore_postgres.py \
  --backup-file /app/backups/arad_backup_YYYYMMDD_HHMMSS.sql.gz \
  --target-db arad_dr_restore \
  --verify-invariants
```

The restoration script automatically:
1. Verifies SHA-256 checksum integrity of the gzip artifact.
2. Creates the isolated target database `arad_dr_restore`.
3. Streams decompressed SQL statements via `psql`.
4. Executes post-restore data integrity validations:
   - Validates Alembic migration version matches codebase.
   - Verifies zero negative stock records (`on_hand >= 0`, `reserved >= 0`).
   - Verifies tenant isolation and foreign key referential integrity.
   - Verifies order counts and outbox consistency.

## 4. Production Cutover (If Primary Database Was Destroyed)
> [!CAUTION]
> Production cutover terminates existing connections and replaces the production database. Ensure full operational team alignment before execution.

```bash
# 1. Stop all write traffic (scale down API and workers)
docker compose stop arad-api arad-worker arad-relay arad-search-worker

# 2. Execute restore targeting primary 'arad' database
python scripts/restore_postgres.py \
  --backup-file /app/backups/arad_backup_YYYYMMDD_HHMMSS.sql.gz \
  --target-db arad \
  --verify-invariants

# 3. Restart application services
docker compose up -d arad-api arad-worker arad-relay arad-search-worker

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
