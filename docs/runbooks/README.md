# Arad Platform Operational Runbooks

This directory contains verified, step-by-step incident response and recovery procedures for the Arad Distributed Order and Inventory Orchestration Platform.

Each runbook contains symptoms, immediate diagnostic commands, root-cause investigation steps, safe mitigation, verification, rollback procedures, escalation rules, and data-integrity warnings.

---

## Runbook Directory Index

| # | Runbook | Scope / Failure Mode | Severity |
|---|---|---|---|
| 1 | [API Outage and Readiness Failure](api_outage.md) | API down, container restarts, `/readyz` 503 failures | P1 |
| 2 | [Database Connection Pool Exhaustion](db_connection_exhaustion.md) | PostgreSQL pool saturation, queue timeouts, locks | P1 |
| 3 | [Failed Database Migration](failed_migration.md) | Alembic migration failure, rollback, transactional DDL | P2 |
| 4 | [Database Migrations Guide](migrations.md) | Standard Alembic authoring, verification, and deploy guide | P3 |
| 5 | [RabbitMQ Outage and Broker Recovery](rabbitmq_outage.md) | Broker crash, alarms, connection errors, outbox guarantee | P1 |
| 6 | [Queue Backlog and Message Age](queue_backlog.md) | Message accumulation, consumer scaling, latency | P2 |
| 7 | [Stuck Outbox Records and Relay Recovery](stuck_outbox.md) | Unpublished outbox rows, relay deadlock, retry exhaustion | P1 |
| 8 | [Dead-Letter Queue (DLQ) Growth](dead_letter_queue.md) | Poison messages, serialization errors, schema conflicts | P2 |
| 9 | [Repeated Celery Task Failures](celery_task_failures.md) | Worker crashes, task retries exceeded, timeout handling | P2 |
| 10 | [Reservation Expiration Backlog](reservation_expiration_backlog.md) | Unreleased expired stock, scheduler stall, manual sweep | P2 |
| 11 | [Redis Outage and Cache Invalidation Failure](redis_outage.md) | Redis crash, key eviction, cache-aside fallback | P2 |
| 12 | [Elasticsearch Outage and Indexing Lag](elasticsearch_lag.md) | Elasticsearch failure, projection lag, flood stage | P2 |
| 13 | [Failed Reindexing and Rollback](reindex_failure.md) | Reindex failure, alias recovery, zero-downtime safety | P2 |
| 14 | [Reconciliation Discrepancies](reconciliation_discrepancies.md) | PostgreSQL vs Elasticsearch drift, auto-repair | P3 |
| 15 | [Inventory Inconsistency & Overselling](inventory_overselling.md) | Negative stock alert, physical vs logical discrepancy | P1 |
| 16 | [Authentication and Authorization Incident](auth_incident.md) | JWT failure, clock skew, 401/403 spikes, brute force | P1 |
| 17 | [Multi-Tenant Isolation Incident](tenant_isolation_incident.md) | Cross-tenant access attempt, RLS audit, leak containment | P1 (Security) |
| 18 | [PostgreSQL Backup Failure](backup_failure.md) | Snapshot failure, disk full, checksum failure | P2 |
| 19 | [Database Restoration and Disaster Recovery](database_restoration.md) | Full DR restore, checksum validation, isolated DB verification | P1 (DR) |
| 20 | [Suspected Credential Leakage & Rotation](credential_leakage.md) | Secret compromise, JWT rotation, DB credential rotation | P1 (Security) |
| 21 | [Resource Exhaustion (CPU/Mem/Disk)](resource_exhaustion.md) | Host/container OOM, disk full, WAL bloat, log pruning | P2 |

---

## Golden Operational Rules

1. **PostgreSQL is the Sole Authority**: Never treat Redis cache or Elasticsearch search documents as authoritative state. In any conflict, PostgreSQL transactional rows prevail.
2. **Never Delete Outbox Records**: Unpublished outbox records represent committed domain events. Deleting them permanently breaks distributed consistency.
3. **No Direct SQL Updates on Stock**: Stock quantities must only be adjusted through audited domain endpoints to maintain accounting provenance.
4. **Isolated DR Verification**: Always test database restores into a separate database (`arad_dr_restore`) before ever touching active production targets.
5. **No Destructive Flushes in Production**: Never execute `FLUSHALL` on Redis or delete active Elasticsearch aliases without confirming traffic rerouting.
