"""Phase 4.2 authorization schema: roles, permissions, scopes, and ABAC policies.

Revision ID: 0006_authorization_phase42
Revises: 0005_tenant_membership_role

Creates tables for RBAC, ABAC, and fine-grained authorization:
* ``roles`` - tenant-scoped and system-wide roles.
* ``role_permissions`` - mapping of permissions granted to roles.
* ``membership_roles`` - multi-role assignments for tenant memberships.
* ``service_account_scopes`` - granular scopes granted to machine identities.
* ``access_policies`` - attribute-based access control policies with condition expressions.
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_authorization_phase42"
down_revision: str | None = "0005_tenant_membership_role"
branch_labels: str | None = None
depends_on: str | None = None

ADMIN_ROLE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
MEMBER_ROLE_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
SUPPORT_ROLE_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")
AUDITOR_ROLE_ID = uuid.UUID("00000000-0000-0000-0000-000000000004")


def upgrade() -> None:
    # 1. roles
    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), server_default="", nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_roles")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_roles_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "name", name=op.f("uq_roles_tenant_name")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_roles_version_positive")),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_roles_name_not_empty")),
    )
    op.create_index(op.f("ix_roles_tenant_id"), "roles", ["tenant_id"], unique=False)

    # 2. role_permissions
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("role_id", "permission", name=op.f("pk_role_permissions")),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name=op.f("fk_role_permissions_role_id_roles"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_role_permissions_role_id"), "role_permissions", ["role_id"], unique=False
    )

    # 3. membership_roles
    op.create_table(
        "membership_roles",
        sa.Column("membership_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("membership_id", "role_id", name=op.f("pk_membership_roles")),
        sa.ForeignKeyConstraint(
            ["membership_id"],
            ["tenant_memberships.id"],
            name=op.f("fk_membership_roles_membership_id_tenant_memberships"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name=op.f("fk_membership_roles_role_id_roles"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_membership_roles_membership_id"),
        "membership_roles",
        ["membership_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_membership_roles_role_id"), "membership_roles", ["role_id"], unique=False
    )

    # 4. service_account_scopes
    op.create_table(
        "service_account_scopes",
        sa.Column("service_account_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint(
            "service_account_id", "scope", name=op.f("pk_service_account_scopes")
        ),
        sa.ForeignKeyConstraint(
            ["service_account_id"],
            ["service_accounts.id"],
            name=op.f("fk_service_account_scopes_sa_id_service_accounts"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_service_account_scopes_sa_id"),
        "service_account_scopes",
        ["service_account_id"],
        unique=False,
    )

    # 5. access_policies
    op.create_table(
        "access_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.String(length=255), server_default="", nullable=False),
        sa.Column("effect", sa.String(length=16), nullable=False),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("resources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("conditions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_policies")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_access_policies_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_access_policies_version_positive")),
        sa.CheckConstraint(
            "effect IN ('ALLOW', 'DENY')", name=op.f("ck_access_policies_effect_valid")
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'INACTIVE')", name=op.f("ck_access_policies_status_valid")
        ),
        sa.CheckConstraint(
            "length(btrim(name)) > 0", name=op.f("ck_access_policies_name_not_empty")
        ),
    )
    op.create_index(
        op.f("ix_access_policies_tenant_id"), "access_policies", ["tenant_id"], unique=False
    )
    op.create_index(op.f("ix_access_policies_status"), "access_policies", ["status"], unique=False)

    # 6. Seed system roles
    roles_table = sa.table(
        "roles",
        sa.column("id", sa.Uuid()),
        sa.column("tenant_id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("description", sa.String()),
        sa.column("is_system", sa.Boolean()),
        sa.column("version", sa.Integer()),
    )
    op.bulk_insert(
        roles_table,
        [
            {
                "id": ADMIN_ROLE_ID,
                "tenant_id": None,
                "name": "ADMIN",
                "description": "Full tenant administrative access",
                "is_system": True,
                "version": 1,
            },
            {
                "id": MEMBER_ROLE_ID,
                "tenant_id": None,
                "name": "MEMBER",
                "description": "Standard tenant member",
                "is_system": True,
                "version": 1,
            },
            {
                "id": SUPPORT_ROLE_ID,
                "tenant_id": None,
                "name": "SUPPORT_AGENT",
                "description": "Support agent with restricted financial views",
                "is_system": True,
                "version": 1,
            },
            {
                "id": AUDITOR_ROLE_ID,
                "tenant_id": None,
                "name": "AUDITOR",
                "description": "Read-only auditor with access to audit logs",
                "is_system": True,
                "version": 1,
            },
        ],
    )

    role_permissions_table = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission", sa.String()),
    )
    op.bulk_insert(
        role_permissions_table,
        [
            {"role_id": ADMIN_ROLE_ID, "permission": "*"},
            {"role_id": MEMBER_ROLE_ID, "permission": "orders.read"},
            {"role_id": MEMBER_ROLE_ID, "permission": "orders.create"},
            {"role_id": MEMBER_ROLE_ID, "permission": "orders.cancel"},
            {"role_id": MEMBER_ROLE_ID, "permission": "orders.confirm"},
            {"role_id": MEMBER_ROLE_ID, "permission": "orders.financials.read"},
            {"role_id": MEMBER_ROLE_ID, "permission": "inventory.read"},
            {"role_id": MEMBER_ROLE_ID, "permission": "catalog.products.read"},
            {"role_id": SUPPORT_ROLE_ID, "permission": "orders.read"},
            {"role_id": SUPPORT_ROLE_ID, "permission": "inventory.read"},
            {"role_id": SUPPORT_ROLE_ID, "permission": "catalog.products.read"},
            {"role_id": AUDITOR_ROLE_ID, "permission": "orders.read"},
            {"role_id": AUDITOR_ROLE_ID, "permission": "orders.financials.read"},
            {"role_id": AUDITOR_ROLE_ID, "permission": "inventory.read"},
            {"role_id": AUDITOR_ROLE_ID, "permission": "catalog.products.read"},
            {"role_id": AUDITOR_ROLE_ID, "permission": "audit.events.read"},
        ],
    )

    # 7. Backfill existing tenant_memberships into membership_roles
    op.execute(
        """
        INSERT INTO membership_roles (membership_id, role_id)
        SELECT id,
            CASE
                WHEN role = 'ADMIN' THEN '00000000-0000-0000-0000-000000000001'::uuid
                ELSE '00000000-0000-0000-0000-000000000002'::uuid
            END
        FROM tenant_memberships
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_table("access_policies")
    op.drop_table("service_account_scopes")
    op.drop_table("membership_roles")
    op.drop_table("role_permissions")
    op.drop_table("roles")
