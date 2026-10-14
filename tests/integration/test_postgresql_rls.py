"""Integration tests for PostgreSQL Row-Level Security (RLS) tenant isolation (AUTH-013).

Verifies at the database engine level using SqlAlchemyTransactionScope:
1. Tenant A cannot read Tenant B's records.
2. Tenant A cannot mutate (insert, update, delete) Tenant B's records.
3. Queries without tenant context fail closed (0 rows returned).
4. Trusted system/worker execution with bypass_rls=True can process cross-tenant data.
5. Built-in system roles are readable across tenants while custom roles remain isolated.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from tests.conftest import test_database_url as resolve_database_url

from shared.infrastructure.db.session import Database

pytestmark = pytest.mark.integration


@pytest.fixture
async def database() -> AsyncIterator[Database]:
    db = Database(resolve_database_url(), pool_size=2, max_overflow=2)
    try:
        yield db
    finally:
        await db.dispose()


async def test_tenant_isolation_reads(database: Database) -> None:
    """Proves Tenant A cannot SELECT records belonging to Tenant B."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    prod_a_id = uuid.uuid4()
    prod_b_id = uuid.uuid4()

    # Seed data using trusted bypass scope
    async with database.new_scope(bypass_rls=True) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, :name, 'ACTIVE', 1, now(), now())"
            ),
            [
                {"id": tenant_a, "slug": f"tenant-a-{tenant_a.hex[:6]}", "name": "Tenant A"},
                {"id": tenant_b, "slug": f"tenant-b-{tenant_b.hex[:6]}", "name": "Tenant B"},
            ],
        )
        await scope.session.execute(
            text(
                "INSERT INTO products ("
                "  id, tenant_id, slug, name, description, status, attributes, version, "
                "  created_at, updated_at"
                ") VALUES (:id, :tenant_id, :slug, :name, '', 'DRAFT', '{}', 1, now(), now())"
            ),
            [
                {"id": prod_a_id, "tenant_id": tenant_a, "slug": "prod-a", "name": "Product A"},
                {"id": prod_b_id, "tenant_id": tenant_b, "slug": "prod-b", "name": "Product B"},
            ],
        )
        await scope.commit()

    # 1. Query under Tenant A scope
    async with database.new_scope(tenant_id=str(tenant_a)) as scope:
        res = await scope.session.execute(text("SELECT id, name FROM products"))
        rows = res.fetchall()
        ids = [r.id for r in rows]
        assert prod_a_id in ids
        assert prod_b_id not in ids

    # 2. Query under Tenant B scope
    async with database.new_scope(tenant_id=str(tenant_b)) as scope:
        res = await scope.session.execute(text("SELECT id, name FROM products"))
        rows = res.fetchall()
        ids = [r.id for r in rows]
        assert prod_b_id in ids
        assert prod_a_id not in ids


async def test_tenant_isolation_mutations(database: Database) -> None:
    """Proves Tenant A cannot INSERT, UPDATE, or DELETE records belonging to Tenant B."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    prod_b_id = uuid.uuid4()

    # Seed data
    async with database.new_scope(bypass_rls=True) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, :name, 'ACTIVE', 1, now(), now())"
            ),
            [
                {"id": tenant_a, "slug": f"mut-a-{tenant_a.hex[:6]}", "name": "Mutation Tenant A"},
                {"id": tenant_b, "slug": f"mut-b-{tenant_b.hex[:6]}", "name": "Mutation Tenant B"},
            ],
        )
        await scope.session.execute(
            text(
                "INSERT INTO products ("
                "  id, tenant_id, slug, name, description, status, attributes, version, "
                "  created_at, updated_at"
                ") VALUES (:id, :tenant_id, :slug, :name, '', 'DRAFT', '{}', 1, now(), now())"
            ),
            {
                "id": prod_b_id,
                "tenant_id": tenant_b,
                "slug": "prod-b-mut",
                "name": "Product B Original",
            },
        )
        await scope.commit()

    # 1. Tenant A attempts to INSERT a product with Tenant B's tenant_id -> Rejected by RLS
    async with database.new_scope(tenant_id=str(tenant_a)) as scope:
        with pytest.raises(Exception) as exc_info:
            await scope.session.execute(
                text(
                    "INSERT INTO products ("
                    "  id, tenant_id, slug, name, description, status, attributes, version, "
                    "  created_at, updated_at"
                    ") VALUES (:id, :tenant_id, :slug, :name, '', 'DRAFT', '{}', 1, now(), now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_b,
                    "slug": "spoofed-prod",
                    "name": "Spoofed Product",
                },
            )
        assert "row-level security policy" in str(exc_info.value).lower()

    # 2. Tenant A attempts to UPDATE Tenant B's product -> 0 rows affected
    async with database.new_scope(tenant_id=str(tenant_a)) as scope:
        res = await scope.session.execute(
            text("UPDATE products SET name = 'Hijacked Name' WHERE id = :id"),
            {"id": prod_b_id},
        )
        assert res.rowcount == 0

    # Verify original product was not modified
    async with database.new_scope(tenant_id=str(tenant_b)) as scope:
        res = await scope.session.execute(
            text("SELECT name FROM products WHERE id = :id"),
            {"id": prod_b_id},
        )
        assert res.scalar_one() == "Product B Original"

    # 3. Tenant A attempts to DELETE Tenant B's product -> 0 rows affected
    async with database.new_scope(tenant_id=str(tenant_a)) as scope:
        res = await scope.session.execute(
            text("DELETE FROM products WHERE id = :id"),
            {"id": prod_b_id},
        )
        assert res.rowcount == 0

    # Verify product still exists in Tenant B
    async with database.new_scope(tenant_id=str(tenant_b)) as scope:
        res = await scope.session.execute(
            text("SELECT count(*) FROM products WHERE id = :id"),
            {"id": prod_b_id},
        )
        assert res.scalar_one() == 1


async def test_missing_tenant_context_fails_closed(database: Database) -> None:
    """Proves queries without tenant context return 0 rows for tenant-owned tables."""
    tenant_id = uuid.uuid4()
    prod_id = uuid.uuid4()

    async with database.new_scope(bypass_rls=True) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, :name, 'ACTIVE', 1, now(), now())"
            ),
            {"id": tenant_id, "slug": f"closed-{tenant_id.hex[:6]}", "name": "Closed Tenant"},
        )
        await scope.session.execute(
            text(
                "INSERT INTO products ("
                "  id, tenant_id, slug, name, description, status, attributes, version, "
                "  created_at, updated_at"
                ") VALUES (:id, :tenant_id, :slug, :name, '', 'DRAFT', '{}', 1, now(), now())"
            ),
            {
                "id": prod_id,
                "tenant_id": tenant_id,
                "slug": "prod-closed",
                "name": "Closed Product",
            },
        )
        await scope.commit()

    # Scope without tenant context or bypass -> fails closed
    async with database.new_scope() as scope:
        res = await scope.session.execute(
            text("SELECT count(*) FROM products WHERE id = :id"), {"id": prod_id}
        )
        assert res.scalar_one() == 0


async def test_system_worker_bypass_rls(database: Database) -> None:
    """Proves trusted background worker flow with bypass_rls=True can process cross-tenant data."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    prod_a = uuid.uuid4()
    prod_b = uuid.uuid4()

    async with database.new_scope(bypass_rls=True) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, :name, 'ACTIVE', 1, now(), now())"
            ),
            [
                {"id": tenant_a, "slug": f"work-a-{tenant_a.hex[:6]}", "name": "Worker Tenant A"},
                {"id": tenant_b, "slug": f"work-b-{tenant_b.hex[:6]}", "name": "Worker Tenant B"},
            ],
        )
        await scope.session.execute(
            text(
                "INSERT INTO products ("
                "  id, tenant_id, slug, name, description, status, attributes, version, "
                "  created_at, updated_at"
                ") VALUES (:id, :tenant_id, :slug, :name, '', 'DRAFT', '{}', 1, now(), now())"
            ),
            [
                {
                    "id": prod_a,
                    "tenant_id": tenant_a,
                    "slug": "work-prod-a",
                    "name": "Worker Product A",
                },
                {
                    "id": prod_b,
                    "tenant_id": tenant_b,
                    "slug": "work-prod-b",
                    "name": "Worker Product B",
                },
            ],
        )
        await scope.commit()

    # Worker session with bypass
    async with database.new_scope(bypass_rls=True) as scope:
        res = await scope.session.execute(
            text("SELECT id FROM products WHERE id IN (:p1, :p2)"),
            {"p1": prod_a, "p2": prod_b},
        )
        found_ids = [r.id for r in res.fetchall()]
        assert prod_a in found_ids
        assert prod_b in found_ids


async def test_roles_rls_system_and_tenant_roles(database: Database) -> None:
    """Proves system roles are visible across all tenants, but custom roles are isolated."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    custom_role_a = uuid.uuid4()

    async with database.new_scope(bypass_rls=True) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, :name, 'ACTIVE', 1, now(), now())"
            ),
            [
                {"id": tenant_a, "slug": f"role-a-{tenant_a.hex[:6]}", "name": "Role Tenant A"},
                {"id": tenant_b, "slug": f"role-b-{tenant_b.hex[:6]}", "name": "Role Tenant B"},
            ],
        )
        await scope.commit()

    # 1. Tenant A creates custom role
    async with database.new_scope(tenant_id=str(tenant_a)) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO roles ("
                "  id, tenant_id, name, description, is_system, version, created_at, updated_at"
                ") VALUES ("
                "  :id, :tenant_id, 'BILLING_SPECIALIST', 'Billing specialist', false, 1, "
                "  now(), now()"
                ")"
            ),
            {"id": custom_role_a, "tenant_id": tenant_a},
        )
        # Tenant A can read its own custom role AND system roles
        res = await scope.session.execute(text("SELECT name, is_system FROM roles"))
        role_names = [r.name for r in res.fetchall()]
        assert "ADMIN" in role_names
        assert "MEMBER" in role_names
        assert "BILLING_SPECIALIST" in role_names
        await scope.commit()

    # 2. Tenant B inspects roles
    async with database.new_scope(tenant_id=str(tenant_b)) as scope:
        res = await scope.session.execute(text("SELECT name, is_system FROM roles"))
        role_names = [r.name for r in res.fetchall()]
        # System roles are visible
        assert "ADMIN" in role_names
        assert "MEMBER" in role_names
        # Tenant A's custom role is NOT visible
        assert "BILLING_SPECIALIST" not in role_names
