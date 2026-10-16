# Architecture Specification: Disaster Recovery and Business Continuity

## 1. Objectives and Guarantees

The Arad Distributed Order and Inventory Orchestration Platform operates on an asynchronous, event-driven modular architecture where **PostgreSQL is the single authoritative system of record**. All other stores (Redis cache, Elasticsearch projections, RabbitMQ queues) are derived and recoverable from PostgreSQL.

### Service Level Objectives
- **Recovery Point Objective (RPO)**: **<= 15 minutes** (maximum acceptable data loss in disaster).
- **Recovery Time Objective (RTO)**: **<= 30 minutes** (maximum acceptable downtime to restore operations).

---

## 2. Backup Strategy and Retention Policy

### Logical Database Backups (`scripts/backup_postgres.py`)
- **Cadence**: Scheduled hourly during active hours; daily baseline full backups.
- **Format**: Plain SQL with explicit table drops, no ownership constraints (`--clean --if-exists --no-owner`).
- **Integrity**: Every backup artifact is validated with an accompanying SHA-256 checksum file (`.sha256`).
- **Atomicity**: Backups are written to a temporary staging file (`.tmp`) and atomically renamed upon successful completion.
- **Retention**:
  - Hourly snapshots: Retained for 24 hours.
  - Daily snapshots: Retained for 30 days.
  - Monthly snapshots: Retained for 1 year in immutable cold storage (e.g., AWS S3 Glacier with Object Lock).
- **Pruning**: Automated in `scripts/backup_postgres.py` via `--max-keep` parameter.

### Physical WAL Archiving (for sub-minute RPO in High-Availability setups)
- PostgreSQL `archive_mode = on` with `archive_command` streaming WAL segments to secure off-host object storage.
- Continuous WAL archiving enables Point-In-Time Recovery (PITR) up to the second preceding a failure.

---

## 3. Restoration & Verification Procedures

### Restoration Workflow (`scripts/restore_postgres.py`)
```text
1. Read backup file and accompanying SHA-256 checksum
2. Verify integrity (abort immediately if checksum mismatch)
3. Ensure isolated target database exists
4. Stream SQL dump with ON_ERROR_STOP=1
5. Run post-restoration verification suite:
   - Alembic migration version matches expected head
   - Referential integrity constraints valid
   - Domain invariants verified: on_hand - reserved >= 0 (zero negative available stock)
6. Start API container against restored database and verify /readyz
```

### Safety Rules
1. Never overwrite the active production database during test restorations.
2. Routine restoration verification runs against an isolated disposable database (e.g., `arad_restore_test`).
3. Set `CONFIRM_PRODUCTION_RESTORE=true` explicitly only during verified disaster recovery.

---

## 4. Recovery of Auxiliary Infrastructure

### RabbitMQ Broker Outage / Message Loss
- **Guarantees**: Business transactions commit aggregate changes and outbox events atomically to PostgreSQL. No committed event is lost if RabbitMQ fails.
- **Recovery**:
  1. Restore RabbitMQ broker.
  2. The Outbox Relay worker restarts and scans for unpublished events (`status = 'PENDING'`).
  3. Events are published to RabbitMQ exchanges.
  4. Consumer inboxes deduplicate redelivered messages, guaranteeing at-least-once transport with idempotent business effects.

### Elasticsearch Search Projection Loss
- **Guarantees**: Elasticsearch is a disposable search projection, never the authoritative source of truth.
- **Recovery**:
  1. Re-create index mappings via `POST /v1/search/indices/reindex` or management CLI.
  2. Run `ReconciliationService.reconcile(auto_repair=True)` to stream products and orders from PostgreSQL and rebuild search documents.

### Redis Cache Loss
- **Guarantees**: Redis stores disposable cached views, locks, and rate limits. Inventory correctness never depends on cached values.
- **Recovery**:
  1. Start clean Redis instance.
  2. The application's cache-aside adapter (`RedisCacheAdapter`) automatically falls back to PostgreSQL on cache misses and repopulates keys with tenant prefixes on demand.
