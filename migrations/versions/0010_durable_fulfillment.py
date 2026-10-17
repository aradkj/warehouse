"""Durable PostgreSQL fulfillment persistence schema and RLS policies.

Revision ID: 0010_durable_fulfillment
Revises: 0009_harden_rls_and_roles

Creates ``fulfillments`` and ``fulfillment_tasks`` tables with tenant isolation,
foreign keys, partial unique index enforcing single active fulfillment per order,
and hardened RLS policies.
"""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import op

revision: str = "0010_durable_fulfillment"
down_revision: str | None = "0009_harden_rls_and_roles"
branch_labels: str | None = None
depends_on: str | None = None

_FULFILLMENT_STATUS_LIST = (
    "'PENDING', 'ALLOCATED', 'IN_PROGRESS', 'SHIPPED', 'FAILED', 'COMPENSATED'"
)


def upgrade() -> None:
    # 1. Create fulfillments table
    op.create_table(
        "fulfillments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("tracking_number", sa.String(length=128), nullable=True),
        sa.Column("carrier", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_retries", sa.Integer(), server_default="3", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fulfillments")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_fulfillments_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_fulfillments_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouses.id"],
            name=op.f("fk_fulfillments_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("version >= 1", name="ck_fulfillments_version_positive"),
        sa.CheckConstraint("retry_count >= 0", name="ck_fulfillments_retry_count_non_negative"),
        sa.CheckConstraint("max_retries >= 0", name="ck_fulfillments_max_retries_non_negative"),
        sa.CheckConstraint(
            f"status IN ({_FULFILLMENT_STATUS_LIST})",
            name="ck_fulfillments_status_valid",
        ),
    )
    op.create_index(
        op.f("ix_fulfillments_tenant_id_order_id"),
        "fulfillments",
        ["tenant_id", "order_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_fulfillments_tenant_id_created_at_id"),
        "fulfillments",
        ["tenant_id", sa.text("created_at DESC"), sa.text("id DESC")],
        unique=False,
    )
    # BIZ-002: Partial unique index enforcing at most one active fulfillment per order
    op.create_index(
        "uq_fulfillments_active_per_order",
        "fulfillments",
        ["tenant_id", "order_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('PENDING', 'ALLOCATED', 'IN_PROGRESS')"),
    )

    # 2. Create fulfillment_tasks table
    op.create_table(
        "fulfillment_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("fulfillment_id", sa.Uuid(), nullable=False),
        sa.Column("warehouse_id", sa.Uuid(), nullable=False),
        sa.Column("sku_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("is_completed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fulfillment_tasks")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_fulfillment_tasks_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fulfillment_id"],
            ["fulfillments.id"],
            name=op.f("fk_fulfillment_tasks_fulfillment_id_fulfillments"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouses.id"],
            name=op.f("fk_fulfillment_tasks_warehouse_id_warehouses"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["skus.id"],
            name=op.f("fk_fulfillment_tasks_sku_id_skus"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_fulfillment_tasks_quantity_positive"),
    )
    op.create_index(
        op.f("ix_fulfillment_tasks_tenant_id_fulfillment_id"),
        "fulfillment_tasks",
        ["tenant_id", "fulfillment_id"],
        unique=False,
    )

    # 3. Enable RLS and create tenant isolation policies
    for table in ("fulfillments", "fulfillment_tasks"):
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

    # 4. Grant DML privileges to application runtime role using safe identifier quoting
    app_role = os.environ.get("ARAD_APP_DB_USER", "arad_app")
    connection = op.get_bind()
    role_exists = connection.execute(
        sa.text("SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = CAST(:role AS text))"),
        {"role": app_role},
    ).scalar_one()
    if role_exists:
        grant_sql = connection.execute(
            sa.text(
                "SELECT format("
                "'GRANT SELECT, INSERT, UPDATE, DELETE ON fulfillments, fulfillment_tasks TO %I', "
                "CAST(:role AS text))"
            ),
            {"role": app_role},
        ).scalar_one()
        op.execute(grant_sql)


def downgrade() -> None:
    for table in ("fulfillment_tasks", "fulfillments"):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table}")

    op.drop_table("fulfillment_tasks")
    op.drop_table("fulfillments")
