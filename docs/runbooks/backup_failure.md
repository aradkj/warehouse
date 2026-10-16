# Runbook: Backup Failure and Incomplete Snapshot

## 1. Symptoms
- Prometheus alert: `PostgresBackupFailed` or `PostgresBackupMissing` (no successful backup in >24 hours).
- Scheduled backup job (`scripts/backup_postgres.py`) exits with non-zero exit code.
- Backup destination directory contains `.tmp` partial files or missing `.sha256` checksums.

## 2. Immediate Diagnostic Commands
```bash
# 1. Inspect recent backup artifacts in backup volume
docker compose run --rm test ls -lh /app/backups/

# 2. Check disk space on backup mount
docker compose run --rm test df -h /app/backups/

# 3. Test pg_dump utility execution manually
docker compose run --rm test python scripts/backup_postgres.py --backup-dir /app/backups
```

## 3. Investigation
1. **Disk Space Exhaustion**: If available space on the backup filesystem is below 500MB (or 2x the database size), `backup_postgres.py` halts to prevent corrupting the storage pool.
2. **PostgreSQL pg_dump Lock Wait**: Long-running transactions blocking `pg_dump` from acquiring shared table locks.
3. **Permission / Network Share Dropped**: If backup directory is mounted from an external NFS or S3 volume, write permissions or credentials may have expired.

## 4. Safe Mitigation
- If blocked by disk space, run automated backup retention pruning:
  ```bash
  # Prune backups older than 7 days
  docker compose run --rm test python scripts/backup_postgres.py --backup-dir /app/backups --retention-days 7
  ```
- If an interrupted `.tmp` file is present:
  ```bash
  # Clean up orphaned temporary artifacts
  rm -f /app/backups/*.tmp
  ```
- Re-run backup script immediately to generate a fresh valid snapshot:
  ```bash
  docker compose run --rm test python scripts/backup_postgres.py --backup-dir /app/backups
  ```

## 5. Verification
```bash
# Check that latest backup file and valid sha256 checksum exist
LATEST_BACKUP=$(docker compose run --rm test ls -t /app/backups/*.sql.gz | head -n 1)
docker compose run --rm test sha256sum -c "${LATEST_BACKUP}.sha256"
```
