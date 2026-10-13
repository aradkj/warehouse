"""Phase 4.1 security hardening: tenant membership role.

Revision ID: 0005_tenant_membership_role
Revises: 0004_identity_phase41

This migration adds an explicit role to tenant memberships to establish an administrative
boundary for service-account and membership provisioning prior to Phase 4.2.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005_tenant_membership_role"
down_revision: str | None = "0004_identity_phase41"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "tenant_memberships",
        sa.Column("role", sa.String(length=16), server_default="MEMBER", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_tenant_memberships_role_valid"),
        "tenant_memberships",
        "role IN ('ADMIN', 'MEMBER')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_tenant_memberships_role_valid"),
        "tenant_memberships",
        type_="check",
    )
    op.drop_column("tenant_memberships", "role")
