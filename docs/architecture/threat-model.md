# Security Threat Model and Risk Assessment

This threat model identifies potential security vectors across the Warehouse platform and details corresponding mitigations or acknowledged remaining risks.

---

## 1. Threat Matrix

| Threat ID | Threat Description | Attack Vector | Implemented Mitigation | Remaining Risk & Defense-in-Depth |
|---|---|---|---|---|
| **TM-01** | **Cross-Tenant Data Access** | Malicious tenant submits IDs belonging to another tenant in HTTP routes or headers. | Authentication middleware extracts tenant from cryptographically signed JWT. Database layer applies PostgreSQL Row-Level Security (RLS). | RLS acts as defense-in-depth even if an application SQL query misses a tenant predicate. |
| **TM-02** | **Privilege Escalation** | Standard user attempts administrative actions (e.g. creating ABAC policies). | Multi-tier authorization: RBAC check enforces required permission strings; ABAC engine verifies subject attributes. | Unassigned permissions fail closed with HTTP 403. |
| **TM-03** | **Service-Account Abuse** | Compromised service account attempts actions outside assigned warehouse scope. | Service accounts are strictly scope-limited. ABAC checks enforce `assigned_warehouses` match the resource's warehouse ID. | Token revocation list required for instant revocation (Phase 5). |
| **TM-04** | **Message Tenant-Header Tampering** | Attacker publishes message directly to broker with spoofed tenant headers. | Broker network isolation (RabbitMQ is inside Docker internal network; not exposed externally). Outbox relay stamps authenticated `tenant_id` from trusted DB commit. | Direct broker access requires physical network compromise of the internal container network. |
| **TM-05** | **Replayed Messages** | Attacker replays intercepted message to duplicate shipments or reservations. | Consumers use the Inbox Pattern with unique message ID constraints and lease expirations. | Replayed messages are recognized as processed and immediately acknowledged without reprocessing. |
| **TM-06** | **Duplicate Commands** | Network jitter causes client to send duplicate `POST /v1/orders` requests. | Mandatory `Idempotency-Key` header with database-backed idempotency store (`idempotency_records`). | Duplicate key within retention window returns cached response with no side effects. |
| **TM-07** | **Stale Authorization Decisions** | User role or policy revoked mid-session while cached token or cached policy remains. | JWT tokens have short TTLs (1 hour). Redis-cached ABAC compiled rules have explicit TTLs and are evicted on policy modification events. | Maximum window of staleness is bounded by cache TTL (default 5 minutes). |
| **TM-08** | **Unauthorized Search / Reindex** | Non-privileged user triggers reindex or queries cross-tenant indices in Elasticsearch. | Reindex endpoints require `search.reindex` permission. All Elasticsearch search queries enforce a hardcoded `{"term": {"tenant_id": ...}}` filter. | Search index is non-authoritative; no write operations can be performed directly on ES. |
| **TM-09** | **Audit-Log Tampering** | Rogue insider attempts to delete or alter audit history. | Audit table has no update/delete endpoints in API. Database permissions grant only `INSERT` and `SELECT` to application user. | Production cold storage export to WORM (Write Once, Read Many) cloud bucket (Phase 5). |
| **TM-10** | **Credential / Token Leakage** | Tokens or DB passwords printed to log streams or error responses. | Custom structured logging formatters sanitize Authorization headers, passwords, and sensitive fields. | Never return internal stack traces in production API responses (handled by standard error handlers). |
| **TM-11** | **Redis Tenant Leakage** | Tenant accesses another tenant's cached product or availability summary. | All Redis cache keys are strictly prefixed with `{tenant_id}:`. No global un-prefixed flush operations exist. | Redis does not store raw secrets or payment details. |
| **TM-12** | **Elasticsearch Tenant Leakage** | Complex search query bypasses tenant boundaries. | Search adapter encapsulates query generation; tenant filter is added as a top-level mandatory boolean filter. | Search queries are validated and parsed through typed `SearchQuery` objects. |
| **TM-13** | **PostgreSQL RLS Bypass** | Direct raw SQL query executed without setting session tenant. | Session context manager automatically executes `SET LOCAL app.current_tenant_id = :id` upon entering transaction scope. | If session variable is empty or unset, RLS policies evaluate to false and return zero rows. |
