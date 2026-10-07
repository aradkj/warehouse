# 10. Multi-tenancy: shared schema, `tenant_id` everywhere, RLS deferred

* Status: Accepted
* Date: 2026-10-06
* Phase: 1

## Context

Every entity in the platform is owned by a tenant: products, SKUs, warehouses, stock,
reservations, orders. Phase 1 must establish the isolation *foundation* — data model,
repositories, request context, tests proving that one tenant cannot see or touch
another's data — without implementing PostgreSQL row-level security, which the
specification assigns to a later phase. A tenant identifier has to reach every query,
and no query may be able to forget it.

## Decision

**Shared schema with a `tenant_id` column on every tenant-owned row.**

* `tenants` is the only table without a `tenant_id`; it *is* the tenant. Every other
  business table carries `tenant_id uuid NOT NULL` with a `RESTRICT` foreign key.
* **Every tenant-scoped index is prefixed by `tenant_id`**, so the access path matches
  the isolation rule and a query that forgets its tenant is both wrong and slow rather
  than accidentally fast ([index-decisions.md](../architecture/index-decisions.md)).
* **Tenant context comes from the request.** `get_request_context` builds a
  `RequestContext` from `X-Tenant-Id` (plus actor and correlation headers); commands
  carry it, and `UseCase.tenant_of(command)` produces the tenant a use case acts on.
  Without a tenant there is no tenant-scoped operation.
* **Repositories require tenant context.** Every method on
  `ProductRepository`, `SkuRepository`, `InventoryItemRepository`,
  `ReservationRepository`, `OrderRepository`, `WarehouseRepository` takes the tenant as
  an argument, and the SQL always filters on it. A lookup that would cross tenants
  returns "not found" — a `404`, never a `403` that leaks the existence of another
  tenant's resource. `CrossTenantAccess` (raised when an aggregate is asked to adopt a
  child belonging to another tenant) maps to `404 cross_tenant_access` for the same
  reason: the answer to "does this exist for you?" is never allowed to distinguish
  "another tenant owns it" from "it does not exist".
* **Ownership is written, not inferred.** A child row is always persisted with the
  caller's `tenant_id`; nothing derives a tenant from a parent row at write time.
* **Authenticated tenancy is a placeholder.** Phase 1 trusts the header — the
  specification puts advanced authentication in a later phase — and isolates that
  decision in one dependency provider so real authentication changes one function, not
  every call site.
* **No RLS in Phase 1.** Row-level security is Phase 4 by the specification. The schema
  is already compatible with it: `tenant_id` is non-null on every owned table, so
  enabling policies later is a migration plus a session variable, not a remodel.

## Consequences

* Isolation is testable at three levels: `tests/repository/` (a repository cannot read
  across tenants), `tests/api/test_api.py::TestTenantIsolationOverHttp` (read, cancel,
  reserve, release and list endpoints), and the schema tests (every owned table has a
  `tenant_id`).
* A missing `tenant_id` filter is a code-review-visible defect, because the bound
  parameter is always the first argument.
* Noisy-neighbour effects are possible — one tenant's volume shares the same tables. The
  tenant-prefixed indexes bound the damage; per-tenant partitioning or sharding is a
  later, orthogonal decision.
* Deleting a tenant is intentionally impossible while it owns data (`RESTRICT`
  everywhere): a tenant is deactivated, never silently cascaded away.

## Alternatives considered

* **A database (or schema) per tenant** — rejected for Phase 1: it multiplies migration
  work and connection management, and the specification's isolation foundation does not
  require it.
* **Database-enforced RLS now** — rejected: it is explicitly a later phase, and doing it
  early would hide application-level mistakes that the repository contract should catch.
* **Deriving the tenant from a parent row inside the query** — rejected: it silently
  widens the query to "any tenant's parent" and makes cross-tenant leakage a matter of
  luck.
* **A global `current_tenant` context variable** — rejected: implicit ambient state is
  the kind of global mutable state the project rules forbid, and it makes the tenant
  invisible at the call site.
