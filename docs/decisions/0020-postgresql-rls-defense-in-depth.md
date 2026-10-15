# 20. PostgreSQL Row-Level Security (RLS) for Defense-in-Depth

## Context
In a multi-tenant platform, a software bug in an application query (e.g. omitting `WHERE tenant_id = :id`) can lead to catastrophic cross-tenant data leakage.

## Decision
We enforce PostgreSQL Row-Level Security (RLS) on all tenant-owned tables:
1. `ALTER TABLE ... ENABLE ROW LEVEL SECURITY;`
2. `ALTER TABLE ... FORCE ROW LEVEL SECURITY;` (forces RLS even for table owners).
3. Every session sets `SET LOCAL app.current_tenant_id = :id` upon entering transaction scope.
4. RLS policies evaluate `tenant_id = current_setting('app.current_tenant_id', true)::uuid`.
5. If the session variable is unset or missing, all queries return zero rows.

## Alternatives Considered
- *Application-only filtering:* Rejected because a single developer oversight in raw SQL or an ORM join could expose other tenants' data.
- *Database-per-tenant or schema-per-tenant:* Rejected due to severe operational complexity, connection pool exhaustion, and migration overhead across thousands of tenants.

## Consequences
- Guaranteed defense-in-depth: the database engine itself enforces multi-tenant boundary.
- Tests must explicitly set tenant context when querying the database.
