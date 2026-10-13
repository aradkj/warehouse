# 15. Authorization and Fine-Grained Permissions (Phase 4.2)

* Status: Accepted
* Date: 2026-10-11
* Phase: 4.2

## Context

Phase 4.1 established identity and authentication (ADR 0014), establishing who the caller is and validating membership in a requested tenant. However, coarse administrative roles (`ADMIN` vs `MEMBER`) and lack of fine-grained authorization were insufficient:

1. **Permission Granularity**: Business operations (such as creating orders, canceling orders, adjusting stock, releasing reservations, or viewing audit logs) required discrete, identifiable permissions rather than binary role checks.
2. **Attribute-Based Access Control (ABAC)**: Complex business authorization rules require policy conditions over subject, resource, action, and environment attributes (e.g. high-value orders, warehouse-scoped operator access).
3. **Resource & Field-Level Authorization**: Sensitive financial data on resources (e.g., total price, unit price on orders) must be filtered or redacted for roles lacking financial view permissions (such as `SUPPORT_AGENT`).
4. **Service Account Least Privilege**: Machine credentials must be constrained to explicit, narrow scopes rather than having blanket access within a tenant.
5. **Auditing**: Every sensitive authorization decision must be traceable with action, resource, tenant, decision reason code, and policy version.

## Decision

1. **Domain-Driven Permission & Role Catalog:**
   - Established a structured permission catalog (`Permission` enum) covering orders, inventory, catalog, reservations, fulfillments, access management, and audit events.
   - Permissions support exact matches and hierarchical prefix wildcards (e.g. `orders.*` or `*`).
   - Defined core system roles (`ADMIN`, `MEMBER`, `SUPPORT_AGENT`, `AUDITOR`) with explicit permission sets.
   - Preserved backward compatibility: existing memberships with legacy enum roles map cleanly to effective permissions without privilege escalation.
   - Supported tenant-scoped custom roles preventing cross-tenant role inheritance or assignment.

2. **Safe, Deterministic ABAC Engine:**
   - Introduced `AccessPolicy` aggregate with deterministic condition evaluation without dynamic code execution (`eval`/`exec`).
   - Built a type-safe condition evaluator supporting operators: `eq`, `neq`, `in`, `not_in`, `contains`, `exists`, `lt`, `lte`, `gt`, `gte`, and logical combinators (`and`, `or`, `not`), plus cross-attribute comparisons (`value_field`).
   - Default security model: **Deny by default**.
   - Structured `AuthorizationDecision` returning boolean `allowed`, `DecisionReason`, `policy_id`, `missing_permissions`, and audit-ready metadata.

3. **Multi-Layer Enforcement & Field-Level Redaction:**
   - Integrated `require_permission(...)` dependency into FastAPI endpoints and sensitive application use cases.
   - Orders API inspects `orders.financials.read` permission: support agents viewing orders receive redacted financial fields (`total`, `unit_price`, `line_total` set to `None`).
   - Service accounts are strictly evaluated against their granted `scopes` and tenant boundaries.
   - Cross-tenant requests fail closed with structured 404/403 responses without information disclosure.

4. **Access Management Router (`/v1/access`):**
   - Implemented tenant-administrative endpoints under `/v1/access`:
     - `GET /v1/access/permissions` - Canonical permission catalog.
     - `GET /v1/access/me` - Current principal's effective roles and permissions.
     - `GET/POST /v1/access/roles` - List and create tenant roles.
     - `GET/POST /v1/access/policies` - List and define ABAC policies.
     - `POST /v1/access/policies/{id}/status` - Activate/deactivate policies.
     - `POST /v1/access/evaluate` - Test/evaluate authorization decisions.
     - `POST/DELETE /v1/access/memberships/{id}/roles` - Assign and revoke roles.

5. **Persistence & Migration:**
   - Alembic migration `0006_authorization_phase42` revises `0005_tenant_membership_role`.
   - Created tables: `roles`, `role_permissions`, `membership_roles`, `service_account_scopes`, and `access_policies` (using JSONB for condition expressions).
   - Seeded system roles and backfilled existing `tenant_memberships` into `membership_roles`.

6. **Audit Integration:**
   - Authorization decisions are logged through `AuditPort` / `LoggingAuditAdapter` recording actor, action, tenant, resource, decision, and reason code without leaking credentials or secrets.

## Consequences

- Full RBAC and ABAC capabilities are operational across API and application boundaries.
- Cross-tenant isolation is strictly maintained at the application and repository layer.
- Field-level financial redaction protects sensitive data for operational staff.
- Existing Phase 1–4.1 functionality remains completely compatible.
- PostgreSQL Row-Level Security (RLS) and dedicated durable audit outbox storage remain intentionally deferred to subsequent security milestones.
