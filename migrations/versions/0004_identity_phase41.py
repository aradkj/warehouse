"""Phase 4.1 identity schema: users, memberships, sessions, and service accounts.

Revision ID: 0004_identity_phase41
Revises: 0003_inbox_lease_and_retry

This migration creates the Phase 4.1 identity and access management schema:
* **users** - user accounts with email, password hash, status, timestamps, version.
* **tenant_memberships** - multi-tenant memberships linking users and tenants.
* **user_sessions** - refresh token families and session lifecycle tracking.
* **service_accounts** - tenant-scoped machine identities and credentials.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004_identity_phase41"
down_revision: str | None = "0003_inbox_lease_and_retry"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # 1. users
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
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
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_users_version_positive")),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED', 'LOCKED')", name=op.f("ck_users_status_valid")
        ),
        sa.CheckConstraint("length(btrim(email)) > 0", name=op.f("ck_users_email_not_empty")),
    )

    # 2. tenant_memberships
    op.create_table(
        "tenant_memberships",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
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
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_memberships")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_tenant_memberships_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_tenant_memberships_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("user_id", "tenant_id", name=op.f("uq_tenant_memberships_user_tenant")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_tenant_memberships_version_positive")),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED')", name=op.f("ck_tenant_memberships_status_valid")
        ),
    )
    op.create_index(
        op.f("ix_tenant_memberships_tenant_id"),
        "tenant_memberships",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tenant_memberships_user_id"),
        "tenant_memberships",
        ["user_id"],
        unique=False,
    )

    # 3. user_sessions
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=128), nullable=False),
        sa.Column("family_id", sa.Uuid(), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_sessions")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_user_sessions_user_id"),
        "user_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_sessions_family_id"),
        "user_sessions",
        ["family_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_sessions_token_hash"),
        "user_sessions",
        ["token_hash"],
        unique=False,
    )

    # 4. service_accounts
    op.create_table(
        "service_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("client_id", sa.String(length=64), nullable=False),
        sa.Column("secret_hash", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="ACTIVE", nullable=False),
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
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_service_accounts")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_service_accounts_tenant_id_tenants"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("client_id", name=op.f("uq_service_accounts_client_id")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_service_accounts_version_positive")),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'DISABLED')", name=op.f("ck_service_accounts_status_valid")
        ),
    )
    op.create_index(
        op.f("ix_service_accounts_tenant_id"),
        "service_accounts",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_service_accounts_tenant_id"), table_name="service_accounts")
    op.drop_table("service_accounts")

    op.drop_index(op.f("ix_user_sessions_token_hash"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_family_id"), table_name="user_sessions")
    op.drop_index(op.f("ix_user_sessions_user_id"), table_name="user_sessions")
    op.drop_table("user_sessions")

    op.drop_index(op.f("ix_tenant_memberships_user_id"), table_name="tenant_memberships")
    op.drop_index(op.f("ix_tenant_memberships_tenant_id"), table_name="tenant_memberships")
    op.drop_table("tenant_memberships")

    op.drop_table("users")
