# Architecture Specification: Production Deployment and Release Reliability

## 1. Environment Segregation

The platform strictly isolates runtime environments through dedicated compose configurations:

| Environment | Compose File | Database | Focus | Security Model |
| :--- | :--- | :--- | :--- | :--- |
| **Development** | `docker-compose.yml` | `arad` | Fast feedback, mapped volumes, exposed debug ports | Local defaults, non-production secrets allowed |
| **Testing** | `docker-compose.yml` (`--profile test`) | `arad_test` | Automated integration & regression test suites | Ephemeral containers, isolated test database |
| **Production** | `docker-compose.prod.yml` | `arad` | Least-privilege hardened, pinned images, resource limits | Rootless (uid 1001), `cap_drop: [ALL]`, mandatory secrets |

---

## 2. Release & Migration Sequence (Zero-Downtime Strategy)

Every release must adhere to forward-compatible, multi-step migration rules to enable zero-downtime deployments:

### Step 1: Pre-Deployment Database Migration
1. Apply forward schema changes via the dedicated one-shot runner:
   ```bash
   docker compose -f docker-compose.prod.yml run --rm migrate
   ```
2. Migrations must follow the **Expand/Contract** pattern:
   - Columns are added as nullable or with defaults.
   - Old columns are not dropped in the same release as application code changes.
   - Code writes to both old and new columns during transition phases.

### Step 2: Rolling API Deployment
1. Start new API container instances with updated application images.
2. The orchestrator polls `/readyz`. Traffic is routed only after `/readyz` confirms:
   - Database connection is healthy.
   - `alembic_version` matches the required migration revision.
3. Drain existing connections from old instances using graceful shutdown (`SIGTERM` with 30s timeout).

### Step 3: Rolling Worker Deployment
1. Deploy `outbox-relay`, `search-consumer`, and Celery `worker` services.
2. Workers complete currently claimed inbox/outbox items before terminating.
3. Unacknowledged RabbitMQ messages are automatically returned to queues upon channel close.

---

## 3. Container Security & Least Privilege

Production manifests (`docker-compose.prod.yml`) enforce defense-in-depth:
- **Rootless execution**: All application processes run under unprivileged user `arad:arad` (`uid: 1001, gid: 1001`).
- **Capability dropping**: `cap_drop: [ALL]` removes all Linux kernel capabilities.
- **Privilege escalation prevention**: `security_opt: [no-new-privileges:true]`.
- **Filesystem immutability**: Containers run with `read_only: true` and temporary scratch paths mounted via `tmpfs: [/tmp]`.
- **Resource quotas**: Explicit limits on CPU cores and memory allocations prevent resource exhaustion or noisy-neighbor interference.
- **Secret enforcement**: Startup fails immediately if `ARAD_AUTH_JWT_SECRET`, `POSTGRES_PASSWORD`, or `RABBITMQ_PASSWORD` are missing or default.

---

## 4. Rollback Procedures

If an incident occurs post-deployment:
1. **Application Code Rollback**:
   Deploy previous image tag (`arad-api:previous-release`). Because schema changes are backward-compatible, older application code runs safely against the hardened schema. The application communicates using standard `app.current_tenant_id` context and does not rely on administrative database privileges.
2. **Database Schema Rollback & One-Way Security Hardening**:
   - Reversible migrations (revisions `0001` through `0008`) support downward migration if needed.
   - **Revision `0009_harden_rls_and_roles` is an irreversible security boundary**: Downgrading revision `0009` is strictly prohibited by Alembic runtime checks because reverting it would restore the deprecated `app.bypass_rls` GUC backdoor and weaken runtime role `arad_app`.
   - If an operational rollback of database state is strictly necessary after applying `0009`, **do not run `alembic downgrade`**. Instead, restore an authoritative pre-deployment database backup using `scripts/restore_postgres.py` or apply a forward corrective migration.
