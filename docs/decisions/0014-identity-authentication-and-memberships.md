# 14. Identity, Authentication, and Tenant Membership (Phase 4.1)

* Status: Accepted
* Date: 2026-10-10
* Phase: 4.1

## Context

Phase 1 established multi-tenancy fundamentals (ADR 0010) where requests were tenant-scoped via the `X-Tenant-Id` header, with authenticated tenancy explicitly reserved for Phase 4.

Phase 4.1 requires:
1. First-class User identity and persistence.
2. Explicit Tenant Membership model supporting users belonging to multiple tenants.
3. Machine-to-machine Service Account identity model.
4. Secure authentication (password-based and service-account API secrets) with session lifecycle and revocation.
5. Trusted principal and tenant context validation preventing unauthorized or cross-tenant access.

## Decision

1. **User Aggregate & Password Security:**
   - Introduced `User` entity with normalized email login identifier, status (`ACTIVE`, `DISABLED`, `LOCKED`), versioning for optimistic concurrency, and audit timestamps.
   - Password hashing utilizes Argon2id with automatic cryptographic fallback to `hashlib.scrypt` when `argon2-cffi` is unavailable in minimal environments.
   - Passwords and password hashes are strictly forbidden in API responses, logs, and token claims.

2. **Explicit Tenant Membership:**
   - Introduced `TenantMembership` entity with status (`ACTIVE`, `DISABLED`), mapping users to tenants.
   - Users do not carry a single hardcoded `tenant_id`. Cross-tenant membership is supported, and tenant access is validated against database membership rather than blindly trusting client headers.
   - Suspended tenants and disabled memberships reject tenant-scoped access with structured domain errors (`TenantSuspended`, `MembershipDisabled`).

3. **Service Accounts:**
   - Introduced `ServiceAccount` aggregate scoped to an active tenant.
   - Machine credentials use high-entropy random secrets (32+ bytes, URL-safe base64), stored as SHA-256 hashes (`secret_hash`).
   - Plaintext secrets are revealed strictly once upon creation (`/v1/auth/service-accounts`).

4. **Token and Session Lifecycle:**
   - Signed JWT access tokens (HMAC-SHA256) with short lifetime (default: 900 seconds / 15 minutes) carrying subject, principal type (`user` or `service_account`), and active tenant/membership context.
   - Refresh token sessions are tracked in `user_sessions` with family ID (`family_id`) for rotation and reuse detection.
   - Rotated refresh tokens are replaced atomically. If an already-rotated refresh token is replayed, the entire token family is immediately revoked to prevent session hijacking.
   - Logout and password changes immediately revoke sessions (`revoke_family` / `revoke_all_for_user`).

5. **Trusted Principal & FastAPI Dependencies:**
   - Introduced framework-independent `Principal` domain representation (`id`, `type`, `email`, `active_tenant_id`, `active_membership_id`, `account_status`).
   - FastAPI dependencies:
     - `get_current_principal`: Extracts and cryptographically verifies Bearer JWT, validates account state against identity database.
     - `require_current_principal`: Enforces presence of authenticated principal.
     - `get_authenticated_tenant_context`: Validates requested `X-Tenant-Id` selector against active database membership for users, or ensures matching tenant scope for service accounts, constructing trusted `RequestContext`.
   - Backward compatibility: If no token is provided and `ARAD_AUTH_REQUIRED=false` (default during transition), falls back to previous header-based context resolution. Operational probes (`/healthz`, `/readyz`) remain unauthenticated.

6. **Database Schema & Migrations:**
   - Alembic migration `0004_identity_phase41` adds `users`, `tenant_memberships`, `user_sessions`, and `service_accounts` tables.
   - Unique constraints on normalized user email, `(user_id, tenant_id)` membership pairs, and `(tenant_id, client_id)` service account identifiers.
   - Foreign keys enforce `ON DELETE RESTRICT` against `tenants` and `users`.

## Consequences

- Authenticated users and machine accounts can be securely provisioned, authenticated, and authorized within tenant boundaries.
- Cross-tenant spoofing via client-controlled headers is eliminated for authenticated requests.
- Full compatibility with Phase 1–3 business services, transactional outbox, Celery workers, and search projection is preserved.
- Provides clean groundwork for Phase 4.2 RBAC/ABAC authorization policies.
