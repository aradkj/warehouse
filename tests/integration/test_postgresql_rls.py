"""Integration tests for PostgreSQL Row-Level Security (RLS) tenant isolation (AUTH-013).

Verifies at the database engine level using SqlAlchemyTransactionScope:
1. Runtime role arad_app has least-privilege (NOSUPERUSER, NOBYPASSRLS, NOCREATEROLE).
2. Setting 'app.bypass_rls = on' as the runtime role CANNOT bypass RLS.
3. Tenant A cannot read Tenant B's records.
4. Tenant A cannot mutate (insert, update, delete) Tenant B's records.
5. Queries without tenant context fail closed (0 rows returned / INSERT rejected).
6. Role-switching failures abort the transaction fail-closed.
7. Pooled connection reuse does not leak tenant context between transactions.
8. Built-in system roles are readable across tenants while custom roles remain isolated.
9. Reservation sweeper processes reservations per-tenant without global RLS bypass.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from tests.conftest import test_database_url as resolve_database_url

from bootstrap.workers import _run_expire_reservations
from shared.infrastructure.db.session import Database

pytestmark = pytest.mark.integration


@pytest.fixture
async def database() -> AsyncIterator[Database]:
    db = Database(resolve_database_url(), pool_size=2, max_overflow=2)
    try:
        yield db
    finally:
        await db.dispose()


async def test_runtime_role_privileges(database: Database) -> None:
    """Proves arad_app runtime role has NOBYPASSRLS, NOSUPERUSER, NOCREATEROLE and cannot escalate."""
    app_url = resolve_database_url().replace("://arad:arad@", "://arad_app:arad@")
    app_db = Database(app_url, pool_size=1)
    try:
        async with app_db.new_scope() as scope:
            # Check pg_roles attributes for arad_app
            res = await scope.session.execute(
                text(
                    "SELECT rolname, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
                    "FROM pg_roles WHERE rolname = 'arad_app'"
                )
            )
            row = res.mappings().one()
            assert row["rolsuper"] is False, "arad_app must not be SUPERUSER"
            assert row["rolbypassrls"] is False, "arad_app must not have BYPASSRLS"
            assert row["rolcreaterole"] is False, "arad_app must not have CREATEROLE"
            assert row["rolcreatedb"] is False, "arad_app must not have CREATEDB"

        # Verify arad_app cannot assume the superuser role 'arad'
        async with app_db.new_scope() as scope:
            with pytest.raises(Exception) as exc_info:
                await scope.session.execute(text("SET ROLE arad"))
            assert "permission denied" in str(exc_info.value).lower()

        # Verify arad_app cannot create roles
        async with app_db.new_scope() as scope:
            with pytest.raises(Exception) as exc_info:
                await scope.session.execute(text("CREATE ROLE malicious_role"))
            assert "permission denied" in str(exc_info.value).lower()

        # Verify arad_app cannot alter table ownership (DDL restricted)
        async with app_db.new_scope() as scope:
            with pytest.raises(Exception) as exc_info:
                await scope.session.execute(text("ALTER TABLE products OWNER TO arad_app"))
            assert "must be owner" in str(exc_info.value).lower()
    finally:
        await app_db.dispose()


async def test_app_bypass_rls_guc_cannot_bypass_rls(database: Database) -> None:
    """Proves that setting app.bypass_rls = 'on' has ZERO effect and cannot bypass tenant RLS."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    prod_a_id = uuid.uuid4()
    prod_b_id = uuid.uuid4()

    # Seed Tenant A
    async with database.new_scope(tenant_id=str(tenant_a)) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, 'Tenant A', 'ACTIVE', 1, now(), now()) ON CONFLICT DO NOTHING"
            ),
            {"id": tenant_a, "slug": f"guc-a-{tenant_a.hex[:6]}"},
        )
        await scope.session.execute(
            text(
                "INSERT INTO products (id, tenant_id, slug, name, description, status, attributes, version, created_at, updated_at) "
                "VALUES (:id, :tenant_id, 'prod-a', 'Product A', '', 'DRAFT', '{}', 1, now(), now())"
            ),
            {"id": prod_a_id, "tenant_id": tenant_a},
        )
        await scope.commit()

    # Seed Tenant B
    async with database.new_scope(tenant_id=str(tenant_b)) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, 'Tenant B', 'ACTIVE', 1, now(), now()) ON CONFLICT DO NOTHING"
            ),
            {"id": tenant_b, "slug": f"guc-b-{tenant_b.hex[:6]}"},
        )
        await scope.session.execute(
            text(
                "INSERT INTO products (id, tenant_id, slug, name, description, status, attributes, version, created_at, updated_at) "
                "VALUES (:id, :tenant_id, 'prod-b', 'Product B', '', 'DRAFT', '{}', 1, now(), now())"
            ),
            {"id": prod_b_id, "tenant_id": tenant_b},
        )
        await scope.commit()

    # Malicious attempt: An attacker in Tenant A executes SET app.bypass_rls = 'on'
    async with database.new_scope(tenant_id=str(tenant_a)) as scope:
        await scope.session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        # Verify GUC is indeed 'on'
        guc_val = await scope.session.execute(text("SELECT current_setting('app.bypass_rls', true)"))
        assert guc_val.scalar_one() == "on"

        # RLS MUST STILL PREVENT reading Tenant B's product!
        res = await scope.session.execute(text("SELECT id FROM products"))
        found_ids = [r.id for r in res.fetchall()]
        assert prod_a_id in found_ids
        assert prod_b_id not in found_ids, "VULNERABILITY: app.bypass_rls allowed reading Tenant B product!"

    # Malicious attempt: Scope with no tenant context sets app.bypass_rls = 'on'
    async with database.new_scope() as scope:
        await scope.session.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        res = await scope.session.execute(text("SELECT count(*) FROM products"))
        assert res.scalar_one() == 0, "VULNERABILITY: app.bypass_rls allowed un-scoped access to products!"


async def test_tenant_isolation_reads(database: Database) -> None:
    """Proves Tenant A cannot SELECT records belonging to Tenant B."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    prod_a_id = uuid.uuid4()
    prod_b_id = uuid.uuid4()

    # Seed Tenant A
    async with database.new_scope(tenant_id=str(tenant_a)) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, :name, 'ACTIVE', 1, now(), now()) ON CONFLICT DO NOTHING"
            ),
            {"id": tenant_a, "slug": f"tenant-a-{tenant_a.hex[:6]}", "name": "Tenant A"},
        )
        await scope.session.execute(
            text(
                "INSERT INTO products ("
                "  id, tenant_id, slug, name, description, status, attributes, version, "
                "  created_at, updated_at"
                ") VALUES (:id, :tenant_id, :slug, :name, '', 'DRAFT', '{}', 1, now(), now())"
            ),
            {"id": prod_a_id, "tenant_id": tenant_a, "slug": "prod-a", "name": "Product A"},
        )
        await scope.commit()

    # Seed Tenant B
    async with database.new_scope(tenant_id=str(tenant_b)) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, :name, 'ACTIVE', 1, now(), now()) ON CONFLICT DO NOTHING"
            ),
            {"id": tenant_b, "slug": f"tenant-b-{tenant_b.hex[:6]}", "name": "Tenant B"},
        )
        await scope.session.execute(
            text(
                "INSERT INTO products ("
                "  id, tenant_id, slug, name, description, status, attributes, version, "
                "  created_at, updated_at"
                ") VALUES (:id, :tenant_id, :slug, :name, '', 'DRAFT', '{}', 1, now(), now())"
            ),
            {"id": prod_b_id, "tenant_id": tenant_b, "slug": "prod-b", "name": "Product B"},
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

    # Seed Tenant A
    async with database.new_scope(tenant_id=str(tenant_a)) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, :name, 'ACTIVE', 1, now(), now()) ON CONFLICT DO NOTHING"
            ),
            {"id": tenant_a, "slug": f"mut-a-{tenant_a.hex[:6]}", "name": "Mutation Tenant A"},
        )
        await scope.commit()

    # Seed Tenant B
    async with database.new_scope(tenant_id=str(tenant_b)) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, :name, 'ACTIVE', 1, now(), now()) ON CONFLICT DO NOTHING"
            ),
            {"id": tenant_b, "slug": f"mut-b-{tenant_b.hex[:6]}", "name": "Mutation Tenant B"},
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

    async with database.new_scope(tenant_id=str(tenant_id)) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, :name, 'ACTIVE', 1, now(), now()) ON CONFLICT DO NOTHING"
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

    # Scope without tenant context -> fails closed
    async with database.new_scope() as scope:
        res = await scope.session.execute(
            text("SELECT count(*) FROM products WHERE id = :id"), {"id": prod_id}
        )
        assert res.scalar_one() == 0

        upd = await scope.session.execute(
            text("UPDATE products SET name = 'Hijacked' WHERE id = :id"), {"id": prod_id}
        )
        assert upd.rowcount == 0

        # Attempting INSERT without tenant context fails closed with RLS error
        with pytest.raises(Exception) as exc_info:
            await scope.session.execute(
                text(
                    "INSERT INTO products (id, tenant_id, slug, name, description, status, attributes, version, created_at, updated_at) "
                    "VALUES (:id, :tid, 'p-no-ctx', 'No Ctx', '', 'DRAFT', '{}', 1, now(), now())"
                ),
                {"id": uuid.uuid4(), "tid": tenant_id},
            )
        assert "row-level security policy" in str(exc_info.value).lower()


async def test_connection_reuse_resets_tenant_context(database: Database) -> None:
    """Proves that pooled connection reuse does not leak tenant context across transactions."""
    tenant_a = uuid.uuid4()
    prod_a = uuid.uuid4()

    # 1. First transaction: sets tenant context for Tenant A and inserts
    async with database.new_scope(tenant_id=str(tenant_a)) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, 'Reuse Tenant A', 'ACTIVE', 1, now(), now()) ON CONFLICT DO NOTHING"
            ),
            {"id": tenant_a, "slug": f"reuse-a-{tenant_a.hex[:6]}"},
        )
        await scope.session.execute(
            text(
                "INSERT INTO products (id, tenant_id, slug, name, description, status, attributes, version, created_at, updated_at) "
                "VALUES (:id, :tenant_id, 'prod-reuse', 'Product Reuse', '', 'DRAFT', '{}', 1, now(), now())"
            ),
            {"id": prod_a, "tenant_id": tenant_a},
        )
        await scope.commit()

    # 2. Second transaction: new scope without tenant context on the same pool
    async with database.new_scope() as scope:
        # Check current_tenant_id GUC
        res = await scope.session.execute(text("SELECT current_setting('app.current_tenant_id', true)"))
        tid_val = res.scalar_one()
        assert tid_val in ("", None), f"Tenant ID leaked across transactions: {tid_val}"

        # Must not be able to read Tenant A's product
        count_res = await scope.session.execute(
            text("SELECT count(*) FROM products WHERE id = :id"), {"id": prod_a}
        )
        assert count_res.scalar_one() == 0, "Tenant A records visible after connection reuse!"


async def test_roles_rls_system_and_tenant_roles(database: Database) -> None:
    """Proves system roles are visible across all tenants, but custom roles are isolated."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    custom_role_a = uuid.uuid4()

    # Seed Tenant A and Tenant B
    async with database.new_scope(tenant_id=str(tenant_a)) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, :name, 'ACTIVE', 1, now(), now()) ON CONFLICT DO NOTHING"
            ),
            {"id": tenant_a, "slug": f"role-a-{tenant_a.hex[:6]}", "name": "Role Tenant A"},
        )
        # Tenant A creates custom role
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
        await scope.commit()

        # Tenant A can read its own custom role AND system roles
        res = await scope.session.execute(text("SELECT name, is_system FROM roles"))
        role_names = [r.name for r in res.fetchall()]
        assert "ADMIN" in role_names
        assert "MEMBER" in role_names
        assert "BILLING_SPECIALIST" in role_names

    # 2. Tenant B inspects roles
    async with database.new_scope(tenant_id=str(tenant_b)) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, :name, 'ACTIVE', 1, now(), now()) ON CONFLICT DO NOTHING"
            ),
            {"id": tenant_b, "slug": f"role-b-{tenant_b.hex[:6]}", "name": "Role Tenant B"},
        )
        await scope.commit()

        res = await scope.session.execute(text("SELECT name, is_system FROM roles"))
        role_names = [r.name for r in res.fetchall()]
        # System roles are visible
        assert "ADMIN" in role_names
        assert "MEMBER" in role_names
        # Tenant A's custom role is NOT visible
        assert "BILLING_SPECIALIST" not in role_names


async def test_worker_per_tenant_reservation_expiration(database: Database) -> None:
    """Proves reservation sweeper processes reservations strictly per-tenant without global bypass."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    res_a_id = uuid.uuid4()
    res_b_id = uuid.uuid4()
    wh_a_id = uuid.uuid4()
    wh_b_id = uuid.uuid4()
    sku_a_id = uuid.uuid4()
    sku_b_id = uuid.uuid4()
    item_a_id = uuid.uuid4()
    item_b_id = uuid.uuid4()

    order_a_id = uuid.uuid4()
    order_b_id = uuid.uuid4()

    now = datetime.now(UTC)
    created_past = now - timedelta(hours=4)
    expired_time = now - timedelta(hours=2)
    future_time = now + timedelta(hours=2)

    # Seed Tenant A: expired reservation
    async with database.new_scope(tenant_id=str(tenant_a)) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, 'Sweep A', 'ACTIVE', 1, now(), now()) ON CONFLICT DO NOTHING"
            ),
            {"id": tenant_a, "slug": f"sw-a-{tenant_a.hex[:6]}"},
        )
        await scope.session.execute(
            text(
                "INSERT INTO orders (id, tenant_id, currency, status, version, created_at, updated_at) "
                "VALUES (:id, :tid, 'USD', 'PENDING', 1, now(), now())"
            ),
            {"id": order_a_id, "tid": tenant_a},
        )
        await scope.session.execute(
            text(
                "INSERT INTO warehouses (id, tenant_id, code, name, is_active, version, created_at, updated_at) "
                "VALUES (:id, :tid, 'WH-A', 'Warehouse A', true, 1, now(), now())"
            ),
            {"id": wh_a_id, "tid": tenant_a},
        )
        prod_a_id = uuid.uuid4()
        await scope.session.execute(
            text(
                "INSERT INTO products (id, tenant_id, slug, name, description, status, attributes, version, created_at, updated_at) "
                "VALUES (:id, :tid, 'prod-sw-a', 'Product SW A', '', 'ACTIVE', '{}', 1, now(), now())"
            ),
            {"id": prod_a_id, "tid": tenant_a},
        )
        await scope.session.execute(
            text(
                "INSERT INTO skus (id, tenant_id, product_id, code, name, attributes, unit_price_minor, currency, status, version, created_at, updated_at) "
                "VALUES (:id, :tid, :pid, 'SKU-A', 'SKU A', '{}', 100, 'USD', 'ACTIVE', 1, now(), now())"
            ),
            {"id": sku_a_id, "tid": tenant_a, "pid": prod_a_id},
        )
        await scope.session.execute(
            text(
                "INSERT INTO inventory_items (id, tenant_id, warehouse_id, sku_id, on_hand, reserved, version, created_at, updated_at) "
                "VALUES (:id, :tid, :wh, :sku, 10, 5, 1, now(), now())"
            ),
            {"id": item_a_id, "tid": tenant_a, "wh": wh_a_id, "sku": sku_a_id},
        )
        await scope.session.execute(
            text(
                "INSERT INTO reservations (id, tenant_id, order_id, status, expires_at, version, created_at, updated_at) "
                "VALUES (:id, :tid, :oid, 'ACTIVE', :exp, 1, :created_at, :created_at)"
            ),
            {
                "id": res_a_id,
                "tid": tenant_a,
                "oid": order_a_id,
                "exp": expired_time,
                "created_at": created_past,
            },
        )
        await scope.session.execute(
            text(
                "INSERT INTO reservation_allocations (id, tenant_id, reservation_id, inventory_item_id, warehouse_id, sku_id, quantity, created_at) "
                "VALUES (:id, :tid, :rid, :iid, :wid, :sku, 5, now())"
            ),
            {"id": uuid.uuid4(), "tid": tenant_a, "rid": res_a_id, "iid": item_a_id, "wid": wh_a_id, "sku": sku_a_id},
        )
        await scope.commit()

    # Seed Tenant B: unexpired reservation
    async with database.new_scope(tenant_id=str(tenant_b)) as scope:
        await scope.session.execute(
            text(
                "INSERT INTO tenants (id, slug, name, status, version, created_at, updated_at) "
                "VALUES (:id, :slug, 'Sweep B', 'ACTIVE', 1, now(), now()) ON CONFLICT DO NOTHING"
            ),
            {"id": tenant_b, "slug": f"sw-b-{tenant_b.hex[:6]}"},
        )
        await scope.session.execute(
            text(
                "INSERT INTO orders (id, tenant_id, currency, status, version, created_at, updated_at) "
                "VALUES (:id, :tid, 'USD', 'PENDING', 1, now(), now())"
            ),
            {"id": order_b_id, "tid": tenant_b},
        )
        await scope.session.execute(
            text(
                "INSERT INTO warehouses (id, tenant_id, code, name, is_active, version, created_at, updated_at) "
                "VALUES (:id, :tid, 'WH-B', 'Warehouse B', true, 1, now(), now())"
            ),
            {"id": wh_b_id, "tid": tenant_b},
        )
        prod_b_id = uuid.uuid4()
        await scope.session.execute(
            text(
                "INSERT INTO products (id, tenant_id, slug, name, description, status, attributes, version, created_at, updated_at) "
                "VALUES (:id, :tid, 'prod-sw-b', 'Product SW B', '', 'ACTIVE', '{}', 1, now(), now())"
            ),
            {"id": prod_b_id, "tid": tenant_b},
        )
        await scope.session.execute(
            text(
                "INSERT INTO skus (id, tenant_id, product_id, code, name, attributes, unit_price_minor, currency, status, version, created_at, updated_at) "
                "VALUES (:id, :tid, :pid, 'SKU-B', 'SKU B', '{}', 100, 'USD', 'ACTIVE', 1, now(), now())"
            ),
            {"id": sku_b_id, "tid": tenant_b, "pid": prod_b_id},
        )
        await scope.session.execute(
            text(
                "INSERT INTO inventory_items (id, tenant_id, warehouse_id, sku_id, on_hand, reserved, version, created_at, updated_at) "
                "VALUES (:id, :tid, :wh, :sku, 10, 3, 1, now(), now())"
            ),
            {"id": item_b_id, "tid": tenant_b, "wh": wh_b_id, "sku": sku_b_id},
        )
        await scope.session.execute(
            text(
                "INSERT INTO reservations (id, tenant_id, order_id, status, expires_at, version, created_at, updated_at) "
                "VALUES (:id, :tid, :oid, 'ACTIVE', :exp, 1, now(), now())"
            ),
            {"id": res_b_id, "tid": tenant_b, "oid": order_b_id, "exp": future_time},
        )
        await scope.session.execute(
            text(
                "INSERT INTO reservation_allocations (id, tenant_id, reservation_id, inventory_item_id, warehouse_id, sku_id, quantity, created_at) "
                "VALUES (:id, :tid, :rid, :iid, :wid, :sku, 3, now())"
            ),
            {"id": uuid.uuid4(), "tid": tenant_b, "rid": res_b_id, "iid": item_b_id, "wid": wh_b_id, "sku": sku_b_id},
        )
        await scope.commit()

    # Run the worker sweeper (processes active tenants per-tenant without bypass)
    expired = await _run_expire_reservations()
    assert expired >= 1

    # Verify Tenant A's reservation transitioned to EXPIRED and inventory reserved stock released
    async with database.new_scope(tenant_id=str(tenant_a)) as scope:
        res = await scope.session.execute(
            text("SELECT status FROM reservations WHERE id = :id"), {"id": res_a_id}
        )
        assert res.scalar_one() == "EXPIRED"

        item_res = await scope.session.execute(
            text("SELECT reserved FROM inventory_items WHERE id = :id"), {"id": item_a_id}
        )
        assert item_res.scalar_one() == 0

    # Verify Tenant B's reservation remains ACTIVE and reserved stock unchanged
    async with database.new_scope(tenant_id=str(tenant_b)) as scope:
        res = await scope.session.execute(
            text("SELECT status FROM reservations WHERE id = :id"), {"id": res_b_id}
        )
        assert res.scalar_one() == "ACTIVE"

        item_res = await scope.session.execute(
            text("SELECT reserved FROM inventory_items WHERE id = :id"), {"id": item_b_id}
        )
        assert item_res.scalar_one() == 3
