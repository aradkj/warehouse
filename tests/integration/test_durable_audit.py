"""Durable audit logging integration tests against PostgreSQL."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.conftest import test_database_url as get_test_database_url

from audit.adapters.persistence.repositories import SqlAlchemyAuditAdapter
from audit.adapters.persistence.tables import audit_logs
from audit.domain.entry import AuditEntry
from shared.infrastructure.db.session import Database


@pytest.fixture
async def database_handle() -> Database:
    db = Database(get_test_database_url(), pool_size=2)
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture
async def engine() -> AsyncEngine:
    db = Database(get_test_database_url())
    try:
        yield db._engine
    finally:
        await db.dispose()


@pytest.mark.asyncio
async def test_audit_persisted_durable_in_postgres(
    database_handle: Database, engine: AsyncEngine
) -> None:
    """Proves audit records are written durably to PostgreSQL audit_logs table."""
    audit_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    now = datetime.now(UTC)

    # 1. First ensure a tenant row exists so foreign key is satisfied
    async with database_handle.new_scope(bypass_rls=True) as scope:
        stmt = sa.text(
            "INSERT INTO tenants (id, slug, name, status, created_at, updated_at, version) "
            "VALUES (:id, :slug, :name, 'ACTIVE', :now, :now, 1) ON CONFLICT DO NOTHING"
        )
        await scope.session.execute(
            stmt,
            {
                "id": tenant_id,
                "slug": f"audit-test-{uuid.uuid4().hex[:6]}",
                "name": "Audit Tenant",
                "now": now,
            },
        )
        await scope.commit()

    # 2. Record audit entry through SqlAlchemyAuditAdapter
    async with database_handle.new_scope(tenant_id=str(tenant_id)) as scope:
        adapter = SqlAlchemyAuditAdapter(scope.session)
        entry = AuditEntry(
            audit_id=str(audit_id),
            tenant_id=str(tenant_id),
            actor_id="admin-user-42",
            action="access.role.created",
            resource_type="role",
            resource_id="role-101",
            correlation_id="corr-xyz",
            causation_id=None,
            occurred_at=now,
            before=None,
            after={"name": "BILLING_MANAGER", "password": "clear_password"},
            details={"client_secret": "raw_secret_value", "level": "privileged"},
        )
        await adapter.record(entry)
        await scope.commit()

    # 3. Read directly via independent connection
    async with engine.connect() as conn:
        result = await conn.execute(sa.select(audit_logs).where(audit_logs.c.id == audit_id))
        row = result.fetchone()
        assert row is not None
        assert row.tenant_id == tenant_id
        assert row.actor_id == "admin-user-42"
        assert row.action == "access.role.created"
        assert row.resource_type == "role"
        assert row.resource_id == "role-101"
        assert row.correlation_id == "corr-xyz"

        # Verify credentials/secrets are redacted in database
        details = row.details
        assert details["after"]["name"] == "BILLING_MANAGER"
        assert details["after"]["password"] == "[REDACTED]"
        assert details["client_secret"] == "[REDACTED]"
        assert details["level"] == "privileged"


@pytest.mark.asyncio
async def test_audit_transaction_rollback_prevents_false_success(
    database_handle: Database, engine: AsyncEngine
) -> None:
    """Proves failed transactions roll back audit logs so no false records exist."""
    audit_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with database_handle.new_scope() as scope:
        adapter = SqlAlchemyAuditAdapter(scope.session)
        entry = AuditEntry(
            audit_id=str(audit_id),
            tenant_id=None,
            actor_id="actor-fail",
            action="identity.failed_mutation",
            resource_type="service_account",
            resource_id="sa-fail",
            correlation_id=None,
            causation_id=None,
            occurred_at=now,
        )
        await adapter.record(entry)
        # Explicitly rollback instead of commit
        await scope.rollback()

    # Verify no row in PostgreSQL
    async with engine.connect() as conn:
        result = await conn.execute(sa.select(audit_logs).where(audit_logs.c.id == audit_id))
        row = result.fetchone()
        assert row is None
