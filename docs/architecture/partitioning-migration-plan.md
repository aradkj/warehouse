# PostgreSQL High-Volume Table Partitioning and Retention Plan

## 1. Executive Summary

This architecture document defines the zero-downtime, staged migration plan for PostgreSQL declarative range partitioning of high-volume event tables:
- `outbox_events` (partitioned by `created_at`, monthly ranges)
- `audit_logs` (partitioned by `occurred_at`, monthly ranges)

In accordance with Section 11 of the platform specification, high-volume event and audit tables require time-based partitioning to prevent B-tree index bloat, enable instant partition drops for retention pruning, and optimize vacuum and query performance.

---

## 2. Technical Prerequisites & PostgreSQL Constraints

In PostgreSQL 14+, declarative range partitioning imposes specific architectural constraints:
1. **Composite Primary Keys:** Every unique constraint and primary key on a partitioned table must include all partitioning columns. For `outbox_events`, the primary key must be `(id, created_at)`. For `audit_logs`, the primary key must be `(id, occurred_at)`.
2. **Foreign Key Limitations:** Foreign keys referencing a partitioned table require the full composite key. Foreign keys pointing *from* a partitioned table to a non-partitioned table (e.g. `tenant_id REFERENCES tenants(id)`) are supported.
3. **Row-Level Security (RLS):** PostgreSQL automatically propagates RLS policies from the parent partitioned table to child partitions. The session variable `app.current_tenant_id` remains strictly enforced across all child partitions.

---

## 3. Staged Zero-Downtime Migration Procedure

Attempting a single-step `ALTER TABLE` conversion on live tables with active traffic risks exclusive lock contention (`ACCESS EXCLUSIVE`) and downtime. The supported production migration follows a three-stage expansion/contraction pattern:

### Phase 1: Forward-Compatible Domain & Query Alignment
- Ensure all application queries on `outbox_events` and `audit_logs` include or tolerate composite primary key lookups (`id` + time boundary).
- Deploy application-level retention pruners (Celery daily worker tasks `outbox.prune_published_events_task`).

### Phase 2: Schema Migration & Partition Creation
1. Create the new partitioned table alongside the legacy table:
   ```sql
   CREATE TABLE outbox_events_v2 (
       id UUID NOT NULL,
       tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
       event_type VARCHAR(120) NOT NULL,
       schema_version INTEGER NOT NULL DEFAULT 1,
       aggregate_type VARCHAR(64) NOT NULL,
       aggregate_id UUID NOT NULL,
       payload JSONB NOT NULL,
       headers JSONB NOT NULL DEFAULT '{}',
       correlation_id VARCHAR(128) NOT NULL,
       causation_id VARCHAR(128),
       occurred_at TIMESTAMPTZ NOT NULL,
       status VARCHAR(16) NOT NULL DEFAULT 'PENDING',
       attempt_count INTEGER NOT NULL DEFAULT 0,
       available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       claimed_at TIMESTAMPTZ,
       claimed_by VARCHAR(128),
       published_at TIMESTAMPTZ,
       last_error TEXT,
       created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       CONSTRAINT pk_outbox_events_v2 PRIMARY KEY (id, created_at),
       CONSTRAINT ck_outbox_events_v2_status_valid CHECK (status IN ('PENDING', 'CLAIMED', 'PUBLISHED', 'FAILED'))
   ) PARTITION BY RANGE (created_at);
   ```
2. Enable RLS and re-apply tenant isolation policies:
   ```sql
   ALTER TABLE outbox_events_v2 ENABLE ROW LEVEL SECURITY;
   ALTER TABLE outbox_events_v2 FORCE ROW LEVEL SECURITY;

   CREATE POLICY tenant_isolation_outbox_v2 ON outbox_events_v2
   FOR ALL
   USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
   ```
3. Pre-create monthly child partitions (current month, past month, and 3 future months):
   ```sql
   CREATE TABLE outbox_events_y2026m10 PARTITION OF outbox_events_v2
   FOR VALUES FROM ('2026-10-01 00:00:00+00') TO ('2026-11-01 00:00:00+00');

   CREATE TABLE outbox_events_y2026m11 PARTITION OF outbox_events_v2
   FOR VALUES FROM ('2026-11-01 00:00:00+00') TO ('2026-12-01 00:00:00+00');
   ```

### Phase 3: Traffic Cutover & Draining
1. Swap table names atomically in a brief transaction:
   ```sql
   BEGIN;
   ALTER TABLE outbox_events RENAME TO outbox_events_legacy;
   ALTER TABLE outbox_events_v2 RENAME TO outbox_events;
   COMMIT;
   ```
2. The outbox relay continues to drain remaining unpublished messages from `outbox_events_legacy`.
3. Once `outbox_events_legacy` contains 0 pending messages and retention age is met, drop `outbox_events_legacy`.

---

## 4. Automated Partition Management & Retention

### Future Partition Creation
Future monthly partitions are provisioned automatically via a scheduled PostgreSQL cron/maintenance task or migration before the end of each month:
```sql
CREATE OR REPLACE FUNCTION create_future_outbox_partition()
RETURNS void AS $$
DECLARE
    next_month DATE := date_trunc('month', NOW() + INTERVAL '1 month');
    month_after DATE := date_trunc('month', NOW() + INTERVAL '2 month');
    part_name TEXT := 'outbox_events_y' || to_char(next_month, 'YYYYmMM');
BEGIN
    EXECUTE format(
        'CREATE TABLE IF NOT EXISTS %I PARTITION OF outbox_events FOR VALUES FROM (%L) TO (%L);',
        part_name, next_month, month_after
    );
END;
$$ LANGUAGE plpgsql;
```

### Retention Policy
- `outbox_events`: Published events are retained for 14 days. After cutover to partitioning, partitions older than 30 days are dropped using `DROP TABLE` (instant O(1) operation).
- `audit_logs`: Retained online for 365 days. Partitions older than 1 year are detached (`ALTER TABLE audit_logs DETACH PARTITION ... CONCURRENTLY`) and archived to compressed cold storage before dropping.
