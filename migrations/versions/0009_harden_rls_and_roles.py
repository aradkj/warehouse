"""Hardened PostgreSQL RLS policies and role privilege separation (Phase 5).

Revision ID: 0009_harden_rls_and_roles
Revises: 0008_postgresql_rls

1. Eliminates unsafe GUC bypass ('app.bypass_rls') from all RLS policies.
2. Restricts arad_app to least-privilege runtime role (NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT).
3. Ensures arad_app has table DML and sequence usage without DDL.
4. Preserves system role readability (is_system = true AND tenant_id IS NULL) and system audit logging (tenant_id IS NULL).
5. Secures tenant_memberships and service_accounts with user/client isolation.
"""

from __future__ import annotations

from alembic import op

revision: str = "0009_harden_rls_and_roles"
down_revision: str | None = "0008_postgresql_rls"
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
    "access_policies",
]


def upgrade() -> None:
    # 0. Ensure the runtime role has least-privilege flags and safe credentials.
    import os

    import sqlalchemy as sa

    app_role = os.environ.get("ARAD_APP_DB_USER", "arad_app")
    app_password = os.environ.get("ARAD_APP_DB_PASSWORD") or os.environ.get(
        "POSTGRES_PASSWORD", "arad"
    )

    connection = op.get_bind()

    role_exists = connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = CAST(:role AS text))"),
        {"role": app_role},
    ).scalar_one()

    role_template = (
        "ALTER ROLE %I WITH PASSWORD %L NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
        if role_exists
        else "CREATE ROLE %I LOGIN PASSWORD %L "
        "NOBYPASSRLS NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT"
    )

    role_sql = connection.execute(
        sa.text(
            "SELECT format(CAST(:template AS text), CAST(:role AS text), CAST(:password AS text))"
        ),
        {
            "template": role_template,
            "role": app_role,
            "password": app_password,
        },
    ).scalar_one()

    op.execute(role_sql)

    def execute_role_statement(template: str) -> None:
        statement = connection.execute(
            sa.text("SELECT format(CAST(:template AS text), CAST(:role AS text))"),
            {"template": template, "role": app_role},
        ).scalar_one()
        op.execute(statement)

    execute_role_statement("GRANT USAGE ON SCHEMA public TO %I")
    execute_role_statement(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I"
    )
    execute_role_statement("GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO %I")
    execute_role_statement(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I"
    )
    execute_role_statement(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I"
    )

    # 1. Hardened direct tenant-owned tables (NO app.bypass_rls GUC condition)

    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table}")
        op.execute(f"""
            CREATE POLICY tenant_isolation_policy ON {table}
                USING (
                    tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                )
                WITH CHECK (
                    tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                )
        """)
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    # 2. Roles table (allows reading system roles where tenant_id IS NULL AND is_system = true)
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON roles")
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON roles
            USING (
                (tenant_id IS NULL AND is_system = true)
                OR tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            )
    """)
    op.execute("ALTER TABLE roles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE roles FORCE ROW LEVEL SECURITY")

    # 3. Audit logs table (strictly isolates tenant-scoped audit records from system logs)
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON audit_logs")
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON audit_logs
            USING (
                CASE
                    WHEN NULLIF(current_setting('app.current_tenant_id', true), '') IS NOT NULL
                    THEN tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                    ELSE tenant_id IS NULL
                END
            )
            WITH CHECK (
                CASE
                    WHEN NULLIF(current_setting('app.current_tenant_id', true), '') IS NOT NULL
                    THEN tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                    ELSE tenant_id IS NULL
                END
            )
    """)
    op.execute("ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY")

    # 4. role_permissions table
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON role_permissions")
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON role_permissions
            USING (
                EXISTS (
                    SELECT 1 FROM roles r
                    WHERE r.id = role_permissions.role_id
                    AND (
                        (r.tenant_id IS NULL AND r.is_system = true)
                        OR r.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                    )
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1 FROM roles r
                    WHERE r.id = role_permissions.role_id
                    AND r.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                )
            )
    """)
    op.execute("ALTER TABLE role_permissions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE role_permissions FORCE ROW LEVEL SECURITY")

    # 5. tenant_memberships table
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON tenant_memberships")
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON tenant_memberships
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                OR user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            )
    """)
    op.execute("ALTER TABLE tenant_memberships ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_memberships FORCE ROW LEVEL SECURITY")

    # 6. membership_roles table
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON membership_roles")
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON membership_roles
            USING (
                EXISTS (
                    SELECT 1 FROM tenant_memberships tm
                    WHERE tm.id = membership_roles.membership_id
                    AND (
                        tm.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                        OR tm.user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid
                    )
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1 FROM tenant_memberships tm
                    WHERE tm.id = membership_roles.membership_id
                    AND tm.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                )
            )
    """)
    op.execute("ALTER TABLE membership_roles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE membership_roles FORCE ROW LEVEL SECURITY")

    # 7. service_accounts table
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON service_accounts")
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON service_accounts
            USING (
                tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                OR client_id = NULLIF(current_setting('app.current_client_id', true), '')
            )
            WITH CHECK (
                tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
            )
    """)
    op.execute("ALTER TABLE service_accounts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE service_accounts FORCE ROW LEVEL SECURITY")

    # 8. service_account_scopes table
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON service_account_scopes")
    op.execute("""
        CREATE POLICY tenant_isolation_policy ON service_account_scopes
            USING (
                EXISTS (
                    SELECT 1 FROM service_accounts sa
                    WHERE sa.id = service_account_scopes.service_account_id
                    AND (
                        sa.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                        OR sa.client_id = NULLIF(current_setting('app.current_client_id', true), '')
                    )
                )
            )
            WITH CHECK (
                EXISTS (
                    SELECT 1 FROM service_accounts sa
                    WHERE sa.id = service_account_scopes.service_account_id
                    AND sa.tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
                )
            )
    """)
    op.execute("ALTER TABLE service_account_scopes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE service_account_scopes FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    raise RuntimeError(
        "Downgrading migration 0009_harden_rls_and_roles is strictly prohibited for security reasons: "
        "reverting this revision would restore the deprecated 'app.bypass_rls' GUC backdoor in PostgreSQL "
        "Row-Level Security policies and weaken the runtime role 'arad_app', violating tenant isolation guarantees. "
        "Application rollbacks should redeploy compatible application images against the hardened database schema. "
        "If a true database rollback is mandatory, restore an authoritative pre-deployment backup."
    )
