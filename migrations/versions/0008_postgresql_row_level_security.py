"""PostgreSQL Row-Level Security (RLS) for tenant isolation (AUTH-013).

Revision ID: 0008_postgresql_rls
Revises: 0007_durable_audit_logging

Enables Row-Level Security (RLS) with FORCE on all tenant-owned tables:
* products
* skus
* warehouses
* inventory_items
* inventory_movements
* reservations
* reservation_allocations
* orders
* order_lines
* idempotency_records
* tenant_memberships
* service_accounts
* roles (with system-role read bypass)
* role_permissions (via roles)
* membership_roles (via tenant_memberships)
* service_account_scopes (via service_accounts)
* access_policies
* audit_logs
"""

from __future__ import annotations

from alembic import op

revision: str = "0008_postgresql_rls"
down_revision: str | None = "0007_durable_audit_logging"
branch_labels: str | None = None
depends_on: str | None = None

TENANT_TABLES = [
    "products",
    "skus",
    "warehouses",
    "inventory_items",
    "inventory_movements",
    "reservations",
    "reservation_allocations",
    "orders",
    "order_lines",
    "idempotency_records",
    "tenant_memberships",
    "service_accounts",
    "access_policies",
]


def upgrade() -> None:
    # 0. Ensure unprivileged application role arad_app exists and has permissions
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'arad_app') THEN
                CREATE ROLE arad_app LOGIN PASSWORD 'arad' NOBYPASSRLS NOSUPERUSER;
            END IF;
        END $$;
    """)
    op.execute("GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO arad_app")
    op.execute("GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO arad_app")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO arad_app")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO arad_app")

    # 1. Direct tenant-owned tables
    for table in TENANT_TABLES:
        op.execute(f"""
            CREATE POLICY tenant_isolation_policy ON {table}
                USING (
                    current_setting('app.bypass_rls', true) = 'on'
                    OR tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                )
                WITH CHECK (
                    current_setting('app.bypass_rls', true) = 'on'
                    OR tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                )
        """)
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # 2. Roles table (allows reading system roles where tenant_id IS NULL AND is_system = true)
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON roles
            USING (
                current_setting('app.bypass_rls', true) = 'on'
                OR (tenant_id IS NULL AND is_system = true)
                OR tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            )
            WITH CHECK (
                current_setting('app.bypass_rls', true) = 'on'
                OR tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            )
    """)
    op.execute("ALTER TABLE roles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE roles FORCE ROW LEVEL SECURITY")

    # 2b. Audit logs table (permits writing system audit entries with tenant_id IS NULL)
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON audit_logs
            USING (
                current_setting('app.bypass_rls', true) = 'on'
                OR tenant_id IS NULL
                OR tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            )
            WITH CHECK (
                current_setting('app.bypass_rls', true) = 'on'
                OR tenant_id IS NULL
                OR tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            )
    """)
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY")

    # 3. role_permissions table
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON role_permissions
            USING (
                current_setting('app.bypass_rls', true) = 'on'
                OR EXISTS (
                    SELECT 1 FROM roles r
                    WHERE r.id = role_permissions.role_id
                    AND (
                        (r.tenant_id IS NULL AND r.is_system = true)
                        OR r.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                    )
                )
            )
            WITH CHECK (
                current_setting('app.bypass_rls', true) = 'on'
                OR EXISTS (
                    SELECT 1 FROM roles r
                    WHERE r.id = role_permissions.role_id
                    AND r.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                )
            )
    """)
    op.execute("ALTER TABLE role_permissions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE role_permissions FORCE ROW LEVEL SECURITY")

    # 4. membership_roles table
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON membership_roles
            USING (
                current_setting('app.bypass_rls', true) = 'on'
                OR EXISTS (
                    SELECT 1 FROM tenant_memberships tm
                    WHERE tm.id = membership_roles.membership_id
                    AND tm.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                )
            )
            WITH CHECK (
                current_setting('app.bypass_rls', true) = 'on'
                OR EXISTS (
                    SELECT 1 FROM tenant_memberships tm
                    WHERE tm.id = membership_roles.membership_id
                    AND tm.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                )
            )
    """)
    op.execute("ALTER TABLE membership_roles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE membership_roles FORCE ROW LEVEL SECURITY")

    # 5. service_account_scopes table
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON service_account_scopes
            USING (
                current_setting('app.bypass_rls', true) = 'on'
                OR EXISTS (
                    SELECT 1 FROM service_accounts sa
                    WHERE sa.id = service_account_scopes.service_account_id
                    AND sa.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                )
            )
            WITH CHECK (
                current_setting('app.bypass_rls', true) = 'on'
                OR EXISTS (
                    SELECT 1 FROM service_accounts sa
                    WHERE sa.id = service_account_scopes.service_account_id
                    AND sa.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                )
            )
    """)
    op.execute("ALTER TABLE service_account_scopes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE service_account_scopes FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    all_tables = [
        *TENANT_TABLES,
        "audit_logs",
        "roles",
        "role_permissions",
        "membership_roles",
        "service_account_scopes",
    ]
    for table in all_tables:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table}")
